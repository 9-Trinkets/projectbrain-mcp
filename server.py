import json
from datetime import datetime
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime import get_runtime

_runtime = get_runtime()
settings = _runtime.settings
auth_token = _runtime.auth_token

_server_host = settings.mcp_server_url.removeprefix("https://").removeprefix("http://").split("/")[0]
_transport_security = TransportSecuritySettings(
    allowed_hosts=[_server_host, "localhost", "127.0.0.1"],
    allowed_origins=settings.cors_origins,
)

mcp_server = FastMCP("ProjectBrain", stateless_http=True, transport_security=_transport_security)

VALID_TASK_STATUSES = {"todo", "in_progress", "blocked", "done", "cancelled"}
VALID_RESPONSE_MODES = {"human", "json", "both"}


class TaskBatchUpdateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="UUID of the task to update.")
    title: Optional[str] = Field(default=None, description="Updated task title.")
    description: Optional[str] = Field(default=None, description="Updated task description.")
    status: Optional[str] = Field(default=None, description="Updated status.")
    priority: Optional[str] = Field(default=None, description="Updated priority.")
    estimate: Optional[int] = Field(default=None, description="Updated estimate.")
    sort_order: Optional[int] = Field(default=None, description="Updated sort order.")
    milestone_id: Optional[str] = Field(default=None, description="Updated milestone UUID; empty string clears it.")
    assignee_id: Optional[str] = Field(default=None, description="Updated assignee UUID; empty string clears it.")

    @model_validator(mode="before")
    @classmethod
    def reject_task_id_alias(cls, value: Any) -> Any:
        if isinstance(value, dict) and "id" not in value and "task_id" in value:
            raise ValueError("Each updates item must include updates[].id.")
        return value


def _preview(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "unknown"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _normalize_terms(terms: Optional[list[str]]) -> list[str]:
    if not terms:
        return []
    return [term.strip() for term in terms if term and term.strip()]


def _compact_params(params: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not params:
        return None
    compacted: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list):
            cleaned = [item for item in value if item is not None]
            if cleaned:
                compacted[key] = cleaned
            continue
        compacted[key] = value
    return compacted or None


def _error_detail(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
    else:
        detail = payload
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, dict):
                loc = item.get("loc")
                msg = item.get("msg")
                if loc and msg:
                    joined_loc = ".".join(str(part) for part in loc)
                    parts.append(f"{joined_loc}: {msg}")
                elif msg:
                    parts.append(str(msg))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "; ".join(parts)
    if detail is None:
        return ""
    return str(detail)


async def _api_request(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Any:
    token = auth_token.get()
    if not token:
        raise ValueError("Not authenticated. Provide a valid bearer token.")

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{settings.api_base_url.rstrip('/')}{path}"
    cleaned_params = _compact_params(params)

    async def _send(req_client: httpx.AsyncClient) -> httpx.Response:
        return await req_client.request(
            method=method,
            url=url,
            headers=headers,
            params=cleaned_params,
            json=json_body,
        )

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as local_client:
                response = await _send(local_client)
        else:
            response = await _send(client)
    except httpx.HTTPError as exc:
        raise ValueError(f"Unable to reach API service: {exc}") from exc

    if response.status_code >= 400:
        detail = ""
        try:
            detail = _error_detail(response.json())
        except ValueError:
            detail = response.text.strip()
        if not detail:
            detail = f"Request failed with status {response.status_code}"
        raise ValueError(detail)

    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError("API returned a non-JSON response.") from exc


async def _api_get(path: str, *, params: Optional[dict[str, Any]] = None, client: Optional[httpx.AsyncClient] = None) -> Any:
    return await _api_request("GET", path, params=params, client=client)


async def _api_post(path: str, *, body: Optional[dict[str, Any]] = None, client: Optional[httpx.AsyncClient] = None) -> Any:
    return await _api_request("POST", path, json_body=body, client=client)


async def _api_patch(path: str, *, body: Optional[dict[str, Any]] = None, client: Optional[httpx.AsyncClient] = None) -> Any:
    return await _api_request("PATCH", path, json_body=body, client=client)


async def _api_delete(path: str, *, client: Optional[httpx.AsyncClient] = None) -> None:
    await _api_request("DELETE", path, client=client)


def _require_fields(action: str, **kwargs: Any) -> Optional[str]:
    missing = [name for name, value in kwargs.items() if value in (None, "")]
    if missing:
        return f"Error: action '{action}' requires field(s): {', '.join(missing)}"
    return None


def _validate_response_mode(response_mode: str) -> Optional[str]:
    if response_mode not in VALID_RESPONSE_MODES:
        return f"Error: Invalid response_mode. Must be one of: {sorted(VALID_RESPONSE_MODES)}"
    return None


def _task_to_dict(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "description": task.get("description"),
        "status": task.get("status"),
        "priority": task.get("priority"),
        "estimate": task.get("estimate"),
        "sort_order": task.get("sort_order"),
        "project_id": task.get("project_id"),
        "assignee_id": task.get("assignee_id"),
        "milestone_id": task.get("milestone_id"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "blocked_by": task.get("blocked_by", []),
    }


def _json_envelope(tool: str, data: dict, query: Optional[dict] = None) -> str:
    payload: dict[str, object] = {
        "ok": True,
        "data": data,
        "meta": {"tool": tool, "response_mode": "json"},
        "error": None,
    }
    if query is not None:
        payload["meta"]["query"] = query
    return json.dumps(payload, ensure_ascii=False)


@mcp_server.tool(description="Unified project context and discovery operations")
async def context(
    action: str = "session",
    project_id: Optional[str] = None,
    since: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 5,
) -> str:
    """Actions: session, summary, changes, search."""
    try:
        if action == "session":
            error = _require_fields(action, project_id=project_id)
            if error:
                return error
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
                session = await _api_get(f"/api/projects/{project_id}/session-context", client=client)
                facts_page = await _api_get(f"/api/projects/{project_id}/facts", params={"limit": 10}, client=client)
                skills_page = await _api_get("/api/skills", params={"project_id": project_id, "limit": 10}, client=client)

            project = session["project"]
            in_progress = session.get("in_progress_tasks", [])
            todo = session.get("todo_tasks", [])
            decisions = session.get("recent_decisions", [])
            members = session.get("team_members", [])
            facts = facts_page.get("items", [])
            skills = skills_page.get("items", [])

            lines = [f"# Project: {project['name']}", f"Description: {project.get('description') or '(none)'}"]
            lines.append(f"\n## In-Progress Tasks ({len(in_progress)})")
            for item in in_progress:
                lines.append(f"  - {item['title']} (ID: {item['id']})")

            lines.append(f"\n## Todo Tasks ({len(todo)})")
            for item in todo:
                priority = f" [{item['priority']}]" if item.get("priority") else ""
                lines.append(f"  - {item['title']}{priority} (ID: {item['id']})")

            lines.append(f"\n## Recent Decisions ({len(decisions)})")
            for item in decisions:
                lines.append(f"  - {item['title']} (ID: {item['id']})")
                if item.get("rationale"):
                    lines.append(f"    {_preview(item['rationale'], 120)}")

            lines.append(f"\n## Facts ({len(facts)})")
            for item in facts:
                category = f" [{item['category']}]" if item.get("category") else ""
                lines.append(f"  - {item['title']}{category} (ID: {item['id']})")

            lines.append(f"\n## Skills ({len(skills)})")
            for item in skills:
                scope = "team-wide" if not item.get("project_id") else "project"
                lines.append(f"  - {item['title']} ({scope}) (ID: {item['id']})")

            lines.append(f"\n## Team Members ({len(members)})")
            for item in members:
                lines.append(f"  - {item['name']} <{item['email']}> [{item['user_type']}] (ID: {item['id']})")
            return "\n".join(lines)

        if action == "summary":
            error = _require_fields(action, project_id=project_id)
            if error:
                return error
            summary = await _api_get(f"/api/projects/{project_id}/summary")
            project = summary["project"]
            counts = summary.get("task_counts", {})
            milestones = summary.get("milestones", [])
            lines = [
                f"# {project['name']} — Summary",
                "\n## Overall Tasks",
                f"  todo: {counts.get('todo', 0)}  in_progress: {counts.get('in_progress', 0)}  blocked: {counts.get('blocked', 0)}  done: {counts.get('done', 0)}",
                f"  total: {sum(int(value) for value in counts.values())}",
                f"\n## Milestones ({len(milestones)})",
            ]
            for milestone in milestones:
                due_str = f" (due {milestone['due_date']})" if milestone.get("due_date") else ""
                lines.append(f"  [{milestone['status']}] {milestone['title']}{due_str} (ID: {milestone['id']})")
            return "\n".join(lines)

        if action == "changes":
            error = _require_fields(action, project_id=project_id, since=since)
            if error:
                return error
            changes = await _api_get(f"/api/projects/{project_id}/changes", params={"since": since})
            total = int(changes.get("total", 0))
            if total == 0:
                return f"No changes since {since}."
            lines = [f"# Changes since {changes.get('since', since)} ({total} total)"]
            for group in changes.get("groups", []):
                group_items = group.get("changes", [])
                lines.append(f"\n## {group.get('entity_type', 'unknown').title()} ({len(group_items)} changes)")
                for entry in group_items:
                    actor = entry.get("actor_name") or "system"
                    title = f" '{entry['entity_title']}'" if entry.get("entity_title") else ""
                    lines.append(f"  - [{entry['action']}]{title} by {actor} at {_format_timestamp(entry.get('created_at'))}")
            if changes.get("truncated"):
                lines.append("\n(Showing first 200 changes. Use a newer 'since' to narrow results.)")
            return "\n".join(lines)

        if action == "search":
            error = _require_fields(action, project_id=project_id, q=q)
            if error:
                return error
            per_entity_limit = max(1, min(limit, 20))
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
                await _api_get(f"/api/projects/{project_id}", client=client)
                tasks_page = await _api_get(f"/api/projects/{project_id}/tasks", params={"q": q, "limit": per_entity_limit}, client=client)
                decisions_page = await _api_get(f"/api/projects/{project_id}/decisions", params={"q": q, "limit": per_entity_limit}, client=client)
                facts_page = await _api_get(f"/api/projects/{project_id}/facts", params={"q": q, "limit": per_entity_limit}, client=client)
                skills_page = await _api_get("/api/skills", params={"project_id": project_id, "q": q, "limit": per_entity_limit}, client=client)

            tasks_items = tasks_page.get("items", [])
            decisions_items = decisions_page.get("items", [])
            facts_items = facts_page.get("items", [])
            skills_items = skills_page.get("items", [])
            total = len(tasks_items) + len(decisions_items) + len(facts_items) + len(skills_items)
            if total == 0:
                return f"No results for '{q}'."

            lines = [f"# Search results for '{q}' ({total} hits)"]
            if tasks_items:
                lines.append(f"\n## Tasks ({len(tasks_items)})")
                for item in tasks_items:
                    lines.append(f"  - [{item['status']}] {item['title']} (ID: {item['id']})")
                    if item.get("description"):
                        lines.append(f"    {_preview(item['description'])}")
            if decisions_items:
                lines.append(f"\n## Decisions ({len(decisions_items)})")
                for item in decisions_items:
                    lines.append(f"  - {item['title']} (ID: {item['id']})")
            if facts_items:
                lines.append(f"\n## Facts ({len(facts_items)})")
                for item in facts_items:
                    lines.append(f"  - {item['title']} (ID: {item['id']})")
            if skills_items:
                lines.append(f"\n## Skills ({len(skills_items)})")
                for item in skills_items:
                    lines.append(f"  - {item['title']} (ID: {item['id']})")
            return "\n".join(lines)

        return "Error: action must be one of: session, summary, changes, search."
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Unified project CRUD operations")
async def projects(
    action: str = "list",
    project_id: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """Actions: list, get, create, update."""
    try:
        if action == "list":
            items = await _api_get("/api/projects/")
            if not items:
                return "No projects found."
            lines = [f"- {item['name']}: {item.get('description') or '(no description)'} (ID: {item['id']})" for item in items]
            return "Projects:\n" + "\n".join(lines)

        if action == "get":
            error = _require_fields(action, project_id=project_id)
            if error:
                return error
            item = await _api_get(f"/api/projects/{project_id}")
            return (
                f"# {item['name']}\n"
                f"ID: {item['id']}\n"
                f"Description: {item.get('description') or '(none)'}\n"
                f"Team: {item['team_id']}\n"
            )

        if action == "create":
            error = _require_fields(action, name=name)
            if error:
                return error
            item = await _api_post("/api/projects/", body={"name": name, "description": description or ""})
            return f"Project created: {item['name']} (ID: {item['id']})"

        if action == "update":
            error = _require_fields(action, project_id=project_id)
            if error:
                return error
            payload: dict[str, Any] = {}
            if name is not None:
                payload["name"] = name
            if description is not None:
                payload["description"] = description
            if not payload:
                return "Error: action 'update' requires at least one of: name, description."
            item = await _api_patch(f"/api/projects/{project_id}", body=payload)
            return f"Project updated: {item['name']} (ID: {item['id']})"

        return "Error: action must be one of: list, get, create, update."
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Unified task operations including dependencies and comments")
async def tasks(
    action: str,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    estimate: Optional[int] = None,
    sort_order: Optional[int] = None,
    milestone_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    q: Optional[str] = None,
    q_any: Optional[list[str]] = None,
    q_all: Optional[list[str]] = None,
    q_not: Optional[list[str]] = None,
    cursor: Optional[str] = None,
    limit: Optional[int] = None,
    response_mode: str = "human",
    depends_on_id: Optional[str] = None,
    comment_body: Optional[str] = None,
    items: Optional[list[dict[str, Any]]] = None,
    updates: Optional[list[TaskBatchUpdateItem]] = None,
) -> str:
    """Actions: list, create, update, delete, context, batch_create, batch_update, add_dependency, remove_dependency, list_dependencies, add_comment, list_comments."""
    try:
        if action == "list":
            error = _require_fields(action, project_id=project_id)
            if error:
                return error
            mode_error = _validate_response_mode(response_mode)
            if mode_error:
                return mode_error
            if status and status not in VALID_TASK_STATUSES:
                return f"Error: Invalid status. Must be one of: {sorted(VALID_TASK_STATUSES)}"

            any_terms = _normalize_terms(q_any)
            all_terms = _normalize_terms(q_all)
            not_terms = _normalize_terms(q_not)
            result = await _api_get(
                f"/api/projects/{project_id}/tasks",
                params={
                    "status": status,
                    "milestone_id": milestone_id,
                    "q": q,
                    "q_any": any_terms,
                    "q_all": all_terms,
                    "q_not": not_terms,
                    "cursor": cursor,
                    "limit": limit,
                },
            )
            task_items = result.get("items", [])
            next_cursor = result.get("next_cursor")
            has_more = bool(result.get("has_more", False))
            effective_limit = limit if limit is not None else 50

            if not task_items and response_mode == "human":
                return "No tasks found."

            human_lines = [f"- [{item['status']}] {item['title']} (ID: {item['id']})" for item in task_items]
            human_text = f"Tasks ({len(task_items)}):\n" + "\n".join(human_lines) if task_items else "No tasks found."
            if next_cursor:
                human_text = f"{human_text}\n\nnext_cursor: {next_cursor}"

            envelope = _json_envelope(
                tool="tasks.list",
                data={
                    "items": [_task_to_dict(item) for item in task_items],
                    "pagination": {"next_cursor": next_cursor, "has_more": has_more, "limit": effective_limit},
                },
                query={
                    "project_id": project_id,
                    "status": status,
                    "milestone_id": milestone_id,
                    "q": q,
                    "q_any": any_terms,
                    "q_all": all_terms,
                    "q_not": not_terms,
                    "cursor": cursor,
                    "limit": limit,
                },
            )
            if response_mode == "json":
                return envelope
            if response_mode == "both":
                return f"{human_text}\n\n---\n{envelope}"
            return human_text

        if action == "create":
            error = _require_fields(action, project_id=project_id, title=title)
            if error:
                return error
            payload: dict[str, Any] = {
                "title": title,
                "description": description or "",
                "status": status or "todo",
                "priority": priority,
                "estimate": estimate,
                "sort_order": sort_order,
                "milestone_id": None if milestone_id == "" else milestone_id,
                "assignee_id": None if assignee_id == "" else assignee_id,
            }
            if payload["status"] not in VALID_TASK_STATUSES:
                return f"Error: Invalid status. Must be one of: {sorted(VALID_TASK_STATUSES)}"
            payload = {key: value for key, value in payload.items() if value is not None}
            item = await _api_post(f"/api/projects/{project_id}/tasks", body=payload)
            return f"Task created: {item['title']} [{item['status']}] (ID: {item['id']})"

        if action == "update":
            error = _require_fields(action, task_id=task_id)
            if error:
                return error
            payload: dict[str, Any] = {}
            for field, value in {
                "title": title,
                "description": description,
                "status": status,
                "priority": priority,
                "estimate": estimate,
                "sort_order": sort_order,
            }.items():
                if value is not None:
                    payload[field] = value
            if milestone_id is not None:
                payload["milestone_id"] = None if milestone_id == "" else milestone_id
            if assignee_id is not None:
                payload["assignee_id"] = None if assignee_id == "" else assignee_id
            if "status" in payload and payload["status"] not in VALID_TASK_STATUSES:
                return f"Error: Invalid status. Must be one of: {sorted(VALID_TASK_STATUSES)}"
            if not payload:
                return "Error: action 'update' requires at least one mutable field."
            item = await _api_patch(f"/api/tasks/{task_id}", body=payload)
            return f"Task updated: {item['title']} [{item['status']}] (ID: {item['id']})"

        if action == "delete":
            error = _require_fields(action, task_id=task_id)
            if error:
                return error
            task_title = ""
            try:
                task = await _api_get(f"/api/tasks/{task_id}")
                task_title = task.get("title", "")
            except Exception:
                pass
            await _api_delete(f"/api/tasks/{task_id}")
            if task_title:
                return f"Task deleted: '{task_title}' (ID: {task_id})"
            return f"Task deleted (ID: {task_id})"

        if action == "context":
            error = _require_fields(action, task_id=task_id)
            if error:
                return error
            context_payload = await _api_get(f"/api/tasks/{task_id}/context")
            item = context_payload["task"]
            decisions = context_payload.get("decisions", [])
            lines = [
                f"# Task: {item['title']}",
                f"Status: {item['status']}",
                f"Priority: {item.get('priority') or 'not set'}",
                f"Estimate: {item.get('estimate') or 'not set'}",
                f"ID: {item['id']}",
                f"\nDescription:\n{item.get('description') or '(none)'}",
                f"\n## Decisions ({len(decisions)})",
            ]
            for decision in decisions:
                lines.append(f"  - {decision['title']} (ID: {decision['id']})")
                if decision.get("rationale"):
                    lines.append(f"    {decision['rationale']}")
            return "\n".join(lines)

        if action == "batch_create":
            error = _require_fields(action, project_id=project_id)
            if error:
                return error
            if not items:
                return "Error: action 'batch_create' requires non-empty items."
            created: list[dict[str, Any]] = []
            errors: list[str] = []
            for index, item in enumerate(items):
                item_title = item.get("title")
                if not item_title:
                    errors.append(f"Item {index}: missing required field 'title'")
                    continue
                item_status = item.get("status", "todo")
                if item_status not in VALID_TASK_STATUSES:
                    errors.append(f"Item {index} ({item_title}): invalid status '{item_status}'")
                    continue
                payload = {
                    "title": item_title,
                    "description": item.get("description", ""),
                    "status": item_status,
                    "priority": item.get("priority"),
                    "estimate": item.get("estimate"),
                    "sort_order": item.get("sort_order"),
                    "milestone_id": None if item.get("milestone_id") == "" else item.get("milestone_id"),
                    "assignee_id": None if item.get("assignee_id") == "" else item.get("assignee_id"),
                }
                payload = {key: value for key, value in payload.items() if value is not None}
                try:
                    created_item = await _api_post(f"/api/projects/{project_id}/tasks", body=payload)
                    created.append(created_item)
                except Exception as exc:
                    errors.append(f"Item {index} ({item_title}): {exc}")
            lines = [f"Created {len(created)}/{len(items)} tasks in project {project_id}:"]
            for created_item in created:
                lines.append(f"  - {created_item['title']} [{created_item['status']}] (ID: {created_item['id']})")
            if errors:
                lines.append(f"\nErrors ({len(errors)}):")
                for error_message in errors:
                    lines.append(f"  - {error_message}")
            return "\n".join(lines)

        if action == "batch_update":
            if not updates:
                return "Error: action 'batch_update' requires non-empty updates."
            normalized_updates: list[dict[str, Any]] = []
            for item in updates:
                normalized = item.model_dump(mode="json", exclude_unset=True)
                if "id" not in normalized:
                    return "Error: Each updates item must include 'id'."
                if "task_id" in normalized:
                    return "Error: Each updates item must include updates[].id."
                if normalized.get("status") and normalized["status"] not in VALID_TASK_STATUSES:
                    return f"Error: Invalid status '{normalized['status']}' for task {normalized.get('id', '(unknown)')}."
                if normalized.get("milestone_id") == "":
                    normalized["milestone_id"] = None
                if normalized.get("assignee_id") == "":
                    normalized["assignee_id"] = None
                normalized_updates.append(normalized)
            updated_items = await _api_patch("/api/tasks/batch", body={"updates": normalized_updates})
            lines = [f"- {item['title']} [{item['status']}] (ID: {item['id']})" for item in updated_items]
            return f"Updated {len(updated_items)} tasks:\n" + "\n".join(lines)

        if action == "add_dependency":
            error = _require_fields(action, task_id=task_id, depends_on_id=depends_on_id)
            if error:
                return error
            task = await _api_get(f"/api/tasks/{task_id}")
            dependency = await _api_get(f"/api/tasks/{depends_on_id}")
            await _api_post(f"/api/tasks/{task_id}/dependencies", body={"depends_on_id": depends_on_id})
            return f"Dependency added: '{task['title']}' is now blocked by '{dependency['title']}'."

        if action == "remove_dependency":
            error = _require_fields(action, task_id=task_id, depends_on_id=depends_on_id)
            if error:
                return error
            task_title = ""
            try:
                task = await _api_get(f"/api/tasks/{task_id}")
                task_title = task.get("title", "")
            except Exception:
                pass
            await _api_delete(f"/api/tasks/{task_id}/dependencies/{depends_on_id}")
            if task_title:
                return f"Dependency removed from task '{task_title}'."
            return f"Dependency removed from task {task_id}."

        if action == "list_dependencies":
            error = _require_fields(action, task_id=task_id)
            if error:
                return error
            task = await _api_get(f"/api/tasks/{task_id}")
            dependencies = await _api_get(f"/api/tasks/{task_id}/dependencies")
            if not dependencies:
                return f"Task '{task['title']}' has no dependencies."
            lines = [f"'{task['title']}' is blocked by:"]
            for item in dependencies:
                lines.append(f"  - [{item['status']}] {item['title']} (ID: {item['id']})")
            return "\n".join(lines)

        if action == "add_comment":
            error = _require_fields(action, task_id=task_id, comment_body=comment_body)
            if error:
                return error
            task_title = ""
            try:
                task = await _api_get(f"/api/tasks/{task_id}")
                task_title = task.get("title", "")
            except Exception:
                pass
            comment = await _api_post(f"/api/tasks/{task_id}/comments", body={"body": comment_body})
            if task_title:
                return f"Comment added to '{task_title}' (comment ID: {comment['id']})"
            return f"Comment added (comment ID: {comment['id']})"

        if action == "list_comments":
            error = _require_fields(action, task_id=task_id)
            if error:
                return error
            task_title = "task"
            try:
                task = await _api_get(f"/api/tasks/{task_id}")
                task_title = task.get("title") or task_title
            except Exception:
                pass
            comments = await _api_get(f"/api/tasks/{task_id}/comments")
            if not comments:
                return f"No comments on '{task_title}'."
            lines = [f"# Comments on '{task_title}' ({len(comments)})"]
            for comment in comments:
                author_name = comment.get("author_name") or comment.get("author_id")
                lines.append(f"\n**{author_name}** — {_format_timestamp(comment.get('created_at'))} (ID: {comment['id']})")
                lines.append(comment["body"])
            return "\n".join(lines)

        return (
            "Error: action must be one of: list, create, update, delete, context, batch_create, "
            "batch_update, add_dependency, remove_dependency, list_dependencies, add_comment, list_comments."
        )
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Unified decision/fact/skill operations")
async def knowledge(
    entity: str,
    action: str,
    project_id: Optional[str] = None,
    item_id: Optional[str] = None,
    title: Optional[str] = None,
    body: Optional[str] = None,
    rationale: Optional[str] = None,
    task_id: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Entity: decision|fact|skill. Actions: list, get, create, update, delete."""
    normalized_entity = entity.strip().lower()
    if normalized_entity.endswith("s"):
        normalized_entity = normalized_entity[:-1]
    if normalized_entity not in {"decision", "fact", "skill"}:
        return "Error: entity must be one of: decision, fact, skill."

    try:
        if action == "list":
            if normalized_entity in {"decision", "fact"}:
                error = _require_fields(action, project_id=project_id)
                if error:
                    return error
            if normalized_entity == "decision":
                result = await _api_get(
                    f"/api/projects/{project_id}/decisions",
                    params={"q": q, "cursor": cursor, "limit": limit},
                )
                items = result.get("items", [])
                if not items:
                    return "No decisions found."
                lines = [f"# Decisions ({len(items)})"]
                for item in items:
                    task_str = f" (task: {item['task_id']})" if item.get("task_id") else ""
                    lines.append(f"- {item['title']}{task_str} (ID: {item['id']})")
                    if item.get("rationale"):
                        lines.append(f"  {_preview(item['rationale'], 200)}")
                if result.get("next_cursor"):
                    lines.append(f"\nnext_cursor: {result['next_cursor']}")
                return "\n".join(lines)

            if normalized_entity == "fact":
                result = await _api_get(
                    f"/api/projects/{project_id}/facts",
                    params={"q": q, "cursor": cursor, "limit": limit},
                )
                items = result.get("items", [])
                if not items:
                    return "No facts found."
                lines = [f"# Facts ({len(items)})"]
                for item in items:
                    category_str = f" [{item['category']}]" if item.get("category") else ""
                    lines.append(f"- {item['title']}{category_str} (ID: {item['id']})")
                    if item.get("body"):
                        lines.append(f"  {_preview(item['body'], 200)}")
                if result.get("next_cursor"):
                    lines.append(f"\nnext_cursor: {result['next_cursor']}")
                return "\n".join(lines)

            result = await _api_get(
                "/api/skills",
                params={"project_id": project_id, "category": category, "q": q, "cursor": cursor, "limit": limit},
            )
            items = result.get("items", [])
            if not items:
                return "No skills found."
            lines = [f"# Skills ({len(items)})"]
            for item in items:
                scope = "team-wide" if not item.get("project_id") else "project"
                category_str = f" [{item['category']}]" if item.get("category") else ""
                tags_str = f" tags:{','.join(item['tags'])}" if item.get("tags") else ""
                lines.append(f"- {item['title']}{category_str}{tags_str} ({scope}) (ID: {item['id']})")
                if item.get("body"):
                    lines.append(f"  {_preview(item['body'], 200)}")
            if result.get("next_cursor"):
                lines.append(f"\nnext_cursor: {result['next_cursor']}")
            return "\n".join(lines)

        if action == "get":
            error = _require_fields(action, item_id=item_id)
            if error:
                return error
            if normalized_entity == "decision":
                item = await _api_get(f"/api/decisions/{item_id}")
                return (
                    f"# Decision: {item['title']}\n"
                    f"ID: {item['id']}\n"
                    f"Project: {item['project_id']}\n"
                    f"Task: {item.get('task_id') or '(none)'}\n"
                    f"\nRationale:\n{item.get('rationale') or '(none)'}"
                )
            if normalized_entity == "fact":
                return "Error: facts do not currently expose a dedicated GET by ID endpoint. Use action='list' with q filtering."
            item = await _api_get(f"/api/skills/{item_id}")
            scope = f"project:{item['project_id']}" if item.get("project_id") else "team-wide"
            category_str = f"Category: {item['category']}\n" if item.get("category") else ""
            tags_str = f"Tags: {', '.join(item['tags'])}\n" if item.get("tags") else ""
            return (
                f"# {item['title']}\n"
                f"ID: {item['id']}\n"
                f"Scope: {scope}\n"
                f"{category_str}{tags_str}"
                f"Author: {item['author_type']} ({item['author_id']})\n"
                f"\n{item['body']}"
            )

        if action == "create":
            if normalized_entity == "decision":
                error = _require_fields(action, project_id=project_id, title=title)
                if error:
                    return error
                payload = {"title": title, "rationale": rationale, "author_type": "agent", "task_id": task_id}
                payload = {key: value for key, value in payload.items() if value is not None}
                item = await _api_post(f"/api/projects/{project_id}/decisions", body=payload)
                return f"Decision recorded: '{item['title']}' (ID: {item['id']})"

            if normalized_entity == "fact":
                error = _require_fields(action, project_id=project_id, title=title)
                if error:
                    return error
                payload = {"title": title, "body": body, "category": category, "author_type": "agent"}
                payload = {key: value for key, value in payload.items() if value is not None}
                item = await _api_post(f"/api/projects/{project_id}/facts", body=payload)
                category_str = f" [{item['category']}]" if item.get("category") else ""
                return f"Fact recorded{category_str}: {item['title']} (ID: {item['id']})"

            error = _require_fields(action, title=title, body=body)
            if error:
                return error
            payload = {"title": title, "body": body, "category": category, "tags": tags, "author_type": "agent"}
            payload = {key: value for key, value in payload.items() if value is not None}
            if project_id:
                item = await _api_post(f"/api/projects/{project_id}/skills", body=payload)
            else:
                item = await _api_post("/api/skills", body=payload)
            scope = f"project {project_id}" if project_id else "team-wide"
            category_str = f" [{item['category']}]" if item.get("category") else ""
            return f"Skill published{category_str}: '{item['title']}' ({scope}) (ID: {item['id']})"

        if action == "update":
            error = _require_fields(action, item_id=item_id)
            if error:
                return error
            if normalized_entity == "decision":
                payload = {"title": title, "rationale": rationale, "task_id": task_id}
                payload = {key: value for key, value in payload.items() if value is not None}
                if not payload:
                    return "Error: action 'update' requires at least one mutable field."
                item = await _api_patch(f"/api/decisions/{item_id}", body=payload)
                return f"Decision updated: '{item['title']}' (ID: {item['id']})"

            if normalized_entity == "fact":
                payload = {"title": title, "body": body, "category": category}
                payload = {key: value for key, value in payload.items() if value is not None}
                if not payload:
                    return "Error: action 'update' requires at least one mutable field."
                item = await _api_patch(f"/api/facts/{item_id}", body=payload)
                return f"Fact updated: '{item['title']}' (ID: {item['id']})"

            payload = {"title": title, "body": body, "category": category, "tags": tags}
            payload = {key: value for key, value in payload.items() if value is not None}
            if not payload:
                return "Error: action 'update' requires at least one mutable field."
            item = await _api_patch(f"/api/skills/{item_id}", body=payload)
            return f"Skill updated: '{item['title']}' (ID: {item['id']})"

        if action == "delete":
            error = _require_fields(action, item_id=item_id)
            if error:
                return error
            if normalized_entity == "decision":
                await _api_delete(f"/api/decisions/{item_id}")
                return f"Decision deleted (ID: {item_id})"
            if normalized_entity == "fact":
                await _api_delete(f"/api/facts/{item_id}")
                return f"Fact deleted (ID: {item_id})"
            await _api_delete(f"/api/skills/{item_id}")
            return f"Skill deleted (ID: {item_id})"

        return "Error: action must be one of: list, get, create, update, delete."
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Unified team, messaging, and identity operations")
async def collaboration(
    action: str,
    recipient_id: Optional[str] = None,
    body: Optional[str] = None,
    message_type: str = "info",
    subject: Optional[str] = None,
    include_read: bool = False,
    mark_as_read: bool = False,
    description: Optional[str] = None,
    skills: Optional[list[str]] = None,
    role: Optional[str] = None,
    invite_code: Optional[str] = None,
) -> str:
    """Actions: list_team_members, discover_agents, send_message, get_messages, update_my_card, join_team."""
    try:
        if action == "list_team_members":
            members = await _api_get("/api/teams/members")
            lines = [f"# Team Members ({len(members)})"]
            for member in members:
                role_str = f" [{member['role']}]" if member.get("role") else ""
                lines.append(f"  {member['user_type'].upper()} {member['name']}{role_str} <{member['email']}> (ID: {member['id']})")
            return "\n".join(lines)

        if action == "discover_agents":
            agents = await _api_get("/api/a2a/agents")
            if not agents:
                return "No agents found on your team."
            lines = [f"# Agents on your team ({len(agents)})"]
            for agent in agents:
                lines.append(f"\n## {agent['name']} (ID: {agent['id']})")
                lines.append(f"  Email: {agent['email']}")
                if agent.get("role"):
                    lines.append(f"  Role: {agent['role']}")
                if agent.get("skills"):
                    lines.append(f"  Skills: {', '.join(agent['skills'])}")
                if agent.get("description"):
                    lines.append(f"  Description: {agent['description']}")
            return "\n".join(lines)

        if action == "send_message":
            error = _require_fields(action, recipient_id=recipient_id, body=body)
            if error:
                return error
            payload = {"recipient_id": recipient_id, "message_type": message_type, "subject": subject, "body": body}
            payload = {key: value for key, value in payload.items() if value is not None}
            message = await _api_post("/api/a2a/messages", body=payload)
            sender_name = message.get("sender_name") or "you"
            recipient_name = message.get("recipient_name") or recipient_id
            subject_line = f"Subject: {subject}\n" if subject else ""
            return (
                f"Message sent to {recipient_name} [{message['message_type']}].\n"
                f"From: {sender_name}\n"
                f"{subject_line}"
                f"{_preview(message['body'], 200)}"
            )

        if action == "get_messages":
            messages = await _api_get("/api/a2a/messages", params={"unread_only": not include_read})
            if not messages:
                return "No unread messages." if not include_read else "No messages."
            marked_count = 0
            if mark_as_read:
                unread_items = [item for item in messages if not item.get("read")]
                for item in unread_items:
                    try:
                        await _api_patch(f"/api/a2a/messages/{item['id']}/read")
                        item["read"] = True
                        marked_count += 1
                    except Exception:
                        continue
            label = "Recent messages" if include_read else "Unread messages"
            lines = [f"# {label} ({len(messages)})"]
            for item in messages:
                subject_str = f" — {item['subject']}" if item.get("subject") else ""
                read_str = " [read]" if item.get("read") else ""
                sender_name = item.get("sender_name") or item.get("sender_id")
                lines.append(f"\n## [{item['message_type']}]{subject_str}{read_str}")
                lines.append(f"  From: {sender_name}  |  ID: {item['id']}  |  {_format_timestamp(item.get('created_at'))}")
                lines.append(f"  {item['body']}")
            if mark_as_read and marked_count > 0:
                lines.append(f"\n(Marked {marked_count} message(s) as read)")
            return "\n".join(lines)

        if action == "update_my_card":
            payload: dict[str, Any] = {}
            if description is not None:
                payload["description"] = description
            if skills is not None:
                payload["skills"] = skills
            if role is not None:
                payload["role"] = role
            if not payload:
                return "Error: action 'update_my_card' requires at least one of: description, skills, role."
            member = await _api_patch("/api/auth/me/card", body=payload)
            parts = []
            if member.get("description"):
                parts.append(f"Description: {member['description']}")
            if member.get("skills"):
                parts.append(f"Skills: {', '.join(member['skills'])}")
            if member.get("role"):
                parts.append(f"Role: {member['role']}")
            return f"Agent card updated for {member['name']}.\n" + "\n".join(parts)

        if action == "join_team":
            error = _require_fields(action, invite_code=invite_code)
            if error:
                return error
            member = await _api_post("/api/teams/join", body={"invite_code": invite_code})
            return f"Successfully joined team (ID: {member['team_id']}). Re-authenticate to refresh team claims."

        return "Error: action must be one of: list_team_members, discover_agents, send_message, get_messages, update_my_card, join_team."
    except Exception as exc:
        return f"Error: {exc}"
