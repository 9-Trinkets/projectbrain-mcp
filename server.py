import json
from datetime import datetime
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import TransportSecuritySettings

from runtime import get_runtime

_runtime = get_runtime()
settings = _runtime.settings
auth_token = _runtime.auth_token

# Allow the production host (required for Render; FastMCP blocks non-localhost by default)
_server_host = settings.mcp_server_url.removeprefix("https://").removeprefix("http://").split("/")[0]
_transport_security = TransportSecuritySettings(
    allowed_hosts=[_server_host, "localhost", "127.0.0.1"],
    allowed_origins=settings.cors_origins,
)

mcp_server = FastMCP("ProjectBrain", stateless_http=True, transport_security=_transport_security)

VALID_STATUSES = {"todo", "in_progress", "blocked", "done", "cancelled"}
VALID_RESPONSE_MODES = {"human", "json", "both"}


def _normalize_terms(terms: Optional[list[str]]) -> list[str]:
    if not terms:
        return []
    return [term.strip() for term in terms if term and term.strip()]


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


def _decision_to_dict(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": decision.get("id"),
        "title": decision.get("title"),
        "rationale": decision.get("rationale"),
        "author_type": decision.get("author_type"),
        "author_id": decision.get("author_id"),
        "task_id": decision.get("task_id"),
        "project_id": decision.get("project_id"),
        "created_at": decision.get("created_at"),
        "updated_at": decision.get("updated_at"),
    }


def _json_envelope(tool: str, data: dict, query: Optional[dict] = None) -> str:
    payload: dict[str, object] = {
        "ok": True,
        "data": data,
        "meta": {
            "tool": tool,
            "response_mode": "json",
        },
        "error": None,
    }
    if query is not None:
        payload["meta"]["query"] = query
    return json.dumps(payload, ensure_ascii=False)


def _validate_response_mode(response_mode: str) -> Optional[str]:
    if response_mode not in VALID_RESPONSE_MODES:
        return f"Error: Invalid response_mode. Must be one of: {sorted(VALID_RESPONSE_MODES)}"
    return None


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


@mcp_server.tool(description="Create a new project in your team")
async def create_project(name: str, description: str = "") -> str:
    try:
        project = await _api_post("/api/projects/", body={"name": name, "description": description})
        return f"Project created: {project['name']} (ID: {project['id']})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="List all projects in your team")
async def list_projects() -> str:
    try:
        projects = await _api_get("/api/projects/")
        if not projects:
            return "No projects found."
        lines = [f"- {p['name']}: {p.get('description') or '(no description)'} (ID: {p['id']})" for p in projects]
        return "Projects:\n" + "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Create a task in a project")
async def create_task(
    project_id: str,
    title: str,
    description: str = "",
    status: str = "todo",
    priority: Optional[str] = None,
    estimate: Optional[int] = None,
    milestone_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> str:
    if status not in VALID_STATUSES:
        return f"Error: Invalid status. Must be one of: {VALID_STATUSES}"
    payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "status": status,
        "priority": priority,
        "estimate": estimate,
        "sort_order": sort_order,
        "milestone_id": None if milestone_id == "" else milestone_id,
        "assignee_id": None if assignee_id == "" else assignee_id,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    try:
        task = await _api_post(f"/api/projects/{project_id}/tasks", body=payload)
        return f"Task created: {task['title']} [{task['status']}] (ID: {task['id']})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Update a task's fields")
async def update_task(
    task_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    estimate: Optional[int] = None,
    sort_order: Optional[int] = None,
    milestone_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
) -> str:
    if status and status not in VALID_STATUSES:
        return f"Error: Invalid status. Must be one of: {VALID_STATUSES}"
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if status is not None:
        payload["status"] = status
    if priority is not None:
        payload["priority"] = priority
    if estimate is not None:
        payload["estimate"] = estimate
    if sort_order is not None:
        payload["sort_order"] = sort_order
    if milestone_id is not None:
        payload["milestone_id"] = None if milestone_id == "" else milestone_id
    if assignee_id is not None:
        payload["assignee_id"] = None if assignee_id == "" else assignee_id
    if not payload:
        return "Error: Provide at least one field to update."
    try:
        task = await _api_patch(f"/api/tasks/{task_id}", body=payload)
        return f"Task updated: {task['title']} [{task['status']}] (ID: {task['id']})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="List tasks in a project, optionally filtered by status, milestone, or search query")
async def list_tasks(
    project_id: str,
    status: Optional[str] = None,
    milestone_id: Optional[str] = None,
    q: Optional[str] = None,
    q_any: Optional[list[str]] = None,
    q_all: Optional[list[str]] = None,
    q_not: Optional[list[str]] = None,
    cursor: Optional[str] = None,
    limit: Optional[int] = None,
    response_mode: str = "human",
) -> str:
    mode_error = _validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    if status and status not in VALID_STATUSES:
        return f"Error: Invalid status. Must be one of: {VALID_STATUSES}"
    any_terms = _normalize_terms(q_any)
    all_terms = _normalize_terms(q_all)
    not_terms = _normalize_terms(q_not)

    params: dict[str, Any] = {
        "status": status,
        "milestone_id": milestone_id,
        "q": q,
        "q_any": any_terms,
        "q_all": all_terms,
        "q_not": not_terms,
        "cursor": cursor,
        "limit": limit,
    }
    try:
        result = await _api_get(f"/api/projects/{project_id}/tasks", params=params)
        tasks = result.get("items", [])
        next_cursor = result.get("next_cursor")
        has_more = bool(result.get("has_more", False))
        effective_limit = limit if limit is not None else 50
        if not tasks and response_mode == "human":
            return "No tasks found."

        human_lines = [f"- [{task['status']}] {task['title']} (ID: {task['id']})" for task in tasks]
        human_header = f"Tasks ({len(tasks)}):\n"
        human_footer = f"\n\nnext_cursor: {next_cursor}" if next_cursor else ""
        human_text = human_header + "\n".join(human_lines) + human_footer if tasks else "No tasks found."

        envelope = _json_envelope(
            tool="list_tasks",
            data={
                "items": [_task_to_dict(task) for task in tasks],
                "pagination": {
                    "next_cursor": next_cursor,
                    "has_more": has_more,
                    "limit": effective_limit,
                },
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
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Delete a task and clean up its dependencies")
async def delete_task(task_id: str) -> str:
    task_title = ""
    try:
        task = await _api_get(f"/api/tasks/{task_id}")
        task_title = task.get("title", "")
    except Exception:
        pass

    try:
        await _api_delete(f"/api/tasks/{task_id}")
        if task_title:
            return f"Task deleted: '{task_title}' (ID: {task_id})"
        return f"Task deleted (ID: {task_id})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Join a team using an invite code")
async def join_team(invite_code: str) -> str:
    try:
        user = await _api_post("/api/teams/join", body={"invite_code": invite_code})
        return (
            f"Successfully joined team (ID: {user['team_id']}). "
            "Re-authenticate to get a new token with the updated team."
        )
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Update multiple tasks in a single transaction")
async def batch_update_tasks(updates: list[dict]) -> str:
    if not updates:
        return "Error: No updates provided."

    normalized_updates: list[dict[str, Any]] = []
    for item in updates:
        normalized = dict(item)
        if normalized.get("status") and normalized["status"] not in VALID_STATUSES:
            return f"Error: Invalid status '{normalized['status']}' for task {normalized.get('id', '(unknown)')}."
        if normalized.get("milestone_id") == "":
            normalized["milestone_id"] = None
        if normalized.get("assignee_id") == "":
            normalized["assignee_id"] = None
        normalized_updates.append(normalized)

    try:
        updated_tasks = await _api_patch("/api/tasks/batch", body={"updates": normalized_updates})
        lines = [
            f"- {task['title']} [{task['status']}] (ID: {task['id']})"
            for task in updated_tasks
        ]
        return f"Updated {len(updated_tasks)} tasks:\n" + "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Get a compact project snapshot to orient yourself at session start")
async def get_session_context(project_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            context = await _api_get(f"/api/projects/{project_id}/session-context", client=client)
            facts_page = await _api_get(f"/api/projects/{project_id}/facts", params={"limit": 10}, client=client)
            skills_page = await _api_get("/api/skills", params={"project_id": project_id, "limit": 10}, client=client)
            team_members = await _api_get("/api/teams/members", client=client)
            pending_msgs = await _api_get("/api/a2a/messages", params={"unread_only": True}, client=client)
            recent_read_msgs: list[dict[str, Any]] = []
            if not pending_msgs:
                all_msgs = await _api_get("/api/a2a/messages", params={"unread_only": False}, client=client)
                recent_read_msgs = [message for message in all_msgs if message.get("read")][:5]
    except Exception as exc:
        return f"Error: {exc}"

    project = context["project"]
    in_progress_tasks = context.get("in_progress_tasks", [])
    todo_tasks = context.get("todo_tasks", [])
    recent_decisions = context.get("recent_decisions", [])
    facts = facts_page.get("items", [])
    skills = skills_page.get("items", [])

    lines = [f"# Project: {project['name']}", f"Description: {project.get('description') or '(none)'}"]

    lines.append(f"\n## In-Progress Tasks ({len(in_progress_tasks)})")
    for task in in_progress_tasks:
        lines.append(f"  - {task['title']} (ID: {task['id']})")

    lines.append(f"\n## Todo Tasks ({len(todo_tasks)})")
    for task in todo_tasks:
        priority = f" [{task['priority']}]" if task.get("priority") else ""
        lines.append(f"  - {task['title']}{priority} (ID: {task['id']})")

    lines.append(f"\n## Recent Decisions ({len(recent_decisions)})")
    for decision in recent_decisions:
        lines.append(f"  - {decision['title']} (by {decision.get('author_type')}, ID: {decision['id']})")
        if decision.get("rationale"):
            lines.append(f"    Rationale: {_preview(decision['rationale'], 120)}")

    if facts:
        lines.append(f"\n## Project Facts ({len(facts)}) — conventions, constraints, context")
        for fact in facts:
            category = f" [{fact['category']}]" if fact.get("category") else ""
            lines.append(f"  - {fact['title']}{category}")
            if fact.get("body"):
                lines.append(f"    {_preview(fact['body'], 150)}")

    if skills:
        lines.append(f"\n## Skills ({len(skills)}) — call list_skills() or get_skill(id) for full content")
        for skill in skills:
            scope = "team-wide" if not skill.get("project_id") else "project"
            category = f" [{skill['category']}]" if skill.get("category") else ""
            tags = f" tags:{','.join(skill['tags'])}" if skill.get("tags") else ""
            lines.append(f"  - {skill['title']}{category}{tags} ({scope}) (ID: {skill['id']})")

    lines.append(f"\n## Team Members ({len(team_members)})")
    for member in team_members:
        card_parts = []
        if member.get("role"):
            card_parts.append(f"role:{member['role']}")
        if member.get("skills"):
            card_parts.append(f"skills:{','.join(member['skills'])}")
        card_str = f"  ({', '.join(card_parts)})" if card_parts else ""
        lines.append(
            f"  - {member['name']} <{member['email']}> [{member['user_type']}]{card_str} (ID: {member['id']})"
        )
        if member.get("description"):
            lines.append(f"    {member['description']}")

    if pending_msgs:
        lines.append(f"\n## ⚠ Pending Messages ({len(pending_msgs)}) — call get_pending_messages() to read")
        for msg in pending_msgs:
            sender_name = msg.get("sender_name") or msg.get("sender_id")
            subject_str = f" — {msg['subject']}" if msg.get("subject") else ""
            lines.append(f"  - [{msg['message_type']}]{subject_str} from {sender_name} (ID: {msg['id']})")
    elif recent_read_msgs:
        lines.append("\n## Recent Messages ({}) — all read. Call get_pending_messages(include_read=true) to review.".format(len(recent_read_msgs)))
        for msg in recent_read_msgs:
            sender_name = msg.get("sender_name") or msg.get("sender_id")
            subject_str = f" — {msg['subject']}" if msg.get("subject") else ""
            lines.append(
                f"  - [{msg['message_type']}]{subject_str} from {sender_name} ({_format_timestamp(msg.get('created_at'))})"
            )

    return "\n".join(lines)


@mcp_server.tool(description="Record a technical or architectural decision for a project")
async def record_decision(
    project_id: str,
    title: str,
    rationale: str = "",
    task_id: Optional[str] = None,
) -> str:
    payload: dict[str, Any] = {
        "title": title,
        "rationale": rationale or None,
        "author_type": "agent",
        "task_id": task_id,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    try:
        decision = await _api_post(f"/api/projects/{project_id}/decisions", body=payload)
        return f"Decision recorded: '{decision['title']}' (ID: {decision['id']})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="List decisions for a project, optionally filtered by search query")
async def list_decisions(
    project_id: str,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    try:
        result = await _api_get(
            f"/api/projects/{project_id}/decisions",
            params={"q": q, "cursor": cursor, "limit": limit},
        )
        decisions = result.get("items", [])
        next_cursor = result.get("next_cursor")
        if not decisions:
            return "No decisions found."
        lines = []
        for decision in decisions:
            task_str = f" (task: {decision['task_id']})" if decision.get("task_id") else ""
            lines.append(f"- {decision['title']}{task_str} (ID: {decision['id']})")
            if decision.get("rationale"):
                lines.append(f"  {_preview(decision['rationale'], 200)}")
        footer = f"\n\nnext_cursor: {next_cursor}" if next_cursor else ""
        return f"Decisions ({len(decisions)}):\n" + "\n".join(lines) + footer
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Delete a decision")
async def delete_decision(decision_id: str) -> str:
    title = ""
    try:
        decision = await _api_get(f"/api/decisions/{decision_id}")
        title = decision.get("title", "")
    except Exception:
        pass
    try:
        await _api_delete(f"/api/decisions/{decision_id}")
        if title:
            return f"Decision deleted: '{title}' (ID: {decision_id})"
        return f"Decision deleted (ID: {decision_id})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Get full context for a task: task details and decisions")
async def get_task_context(task_id: str, response_mode: str = "human") -> str:
    mode_error = _validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    try:
        context = await _api_get(f"/api/tasks/{task_id}/context")
        task = context["task"]
        decisions = context.get("decisions", [])
    except Exception as exc:
        return f"Error: {exc}"

    lines = [
        f"# Task: {task['title']}",
        f"Status: {task['status']}",
        f"Priority: {task.get('priority') or 'not set'}",
        f"Estimate: {task.get('estimate') or 'not set'}",
        f"ID: {task['id']}",
        f"\nDescription:\n{task.get('description') or '(none)'}",
    ]
    lines.append(f"\n## Decisions ({len(decisions)})")
    for decision in decisions:
        lines.append(f"  - {decision['title']} (ID: {decision['id']})")
        if decision.get("rationale"):
            lines.append(f"    {decision['rationale']}")
    human_text = "\n".join(lines)

    envelope = _json_envelope(
        tool="get_task_context",
        data={
            "task": _task_to_dict(task),
            "decisions": [_decision_to_dict(decision) for decision in decisions],
        },
        query={"task_id": task_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


@mcp_server.tool(description="Create a milestone in a project")
async def create_milestone(
    project_id: str,
    title: str,
    description: str = "",
    due_date: Optional[str] = None,
    status: str = "planned",
) -> str:
    valid_statuses = {"planned", "in_progress", "completed", "cancelled"}
    if status not in valid_statuses:
        return f"Error: Invalid status. Must be one of: {valid_statuses}"
    payload: dict[str, Any] = {
        "title": title,
        "description": description or None,
        "due_date": due_date,
        "status": status,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    try:
        milestone = await _api_post(f"/api/projects/{project_id}/milestones", body=payload)
        due_str = f", due {milestone['due_date']}" if milestone.get("due_date") else ""
        return f"Milestone created: '{milestone['title']}' [{milestone['status']}]{due_str} (ID: {milestone['id']})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Update a milestone's fields")
async def update_milestone(
    milestone_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    due_date: Optional[str] = None,
    status: Optional[str] = None,
) -> str:
    valid_statuses = {"planned", "in_progress", "completed", "cancelled"}
    if status and status not in valid_statuses:
        return f"Error: Invalid status. Must be one of: {valid_statuses}"
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if due_date is not None:
        payload["due_date"] = due_date
    if status is not None:
        payload["status"] = status
    if not payload:
        return "Error: Provide at least one field to update."
    try:
        milestone = await _api_patch(f"/api/milestones/{milestone_id}", body=payload)
        due_str = f", due {milestone['due_date']}" if milestone.get("due_date") else ""
        return f"Milestone updated: '{milestone['title']}' [{milestone['status']}]{due_str} (ID: {milestone['id']})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Get a project summary: task counts and milestones with progress")
async def get_project_summary(project_id: str) -> str:
    try:
        summary = await _api_get(f"/api/projects/{project_id}/summary")
    except Exception as exc:
        return f"Error: {exc}"

    project = summary["project"]
    counts = summary.get("task_counts", {})
    milestones = summary.get("milestones", [])

    lines = [
        f"# {project['name']} — Summary",
        "\n## Overall Tasks",
        (
            f"  todo: {counts.get('todo', 0)}  "
            f"in_progress: {counts.get('in_progress', 0)}  "
            f"blocked: {counts.get('blocked', 0)}  "
            f"done: {counts.get('done', 0)}"
        ),
        f"  total: {sum(int(v) for v in counts.values())}",
    ]

    lines.append(f"\n## Milestones ({len(milestones)})")
    for milestone in milestones:
        due_str = f" (due {milestone['due_date']})" if milestone.get("due_date") else ""
        lines.append(f"  [{milestone['status']}] {milestone['title']}{due_str} (ID: {milestone['id']})")
        task_counts = milestone.get("task_counts", {})
        if task_counts:
            total_tasks = sum(int(v) for v in task_counts.values())
            done_tasks = int(task_counts.get("done", 0))
            lines.append(
                "    Tasks: "
                f"{done_tasks}/{total_tasks} done  |  "
                f"todo:{task_counts.get('todo', 0)} "
                f"in_progress:{task_counts.get('in_progress', 0)} "
                f"blocked:{task_counts.get('blocked', 0)}"
            )
    return "\n".join(lines)


@mcp_server.tool()
async def update_my_card(
    description: str | None = None,
    skills: list[str] | None = None,
    role: str | None = None,
) -> str:
    payload: dict[str, Any] = {}
    if description is not None:
        payload["description"] = description
    if skills is not None:
        payload["skills"] = skills
    if role is not None:
        payload["role"] = role
    if not payload:
        return "Error: Provide at least one field to update."
    try:
        user = await _api_patch("/api/auth/me/card", body=payload)
    except Exception as exc:
        return f"Error: {exc}"

    parts = []
    if user.get("description"):
        parts.append(f"Description: {user['description']}")
    if user.get("skills"):
        parts.append(f"Skills: {', '.join(user['skills'])}")
    if user.get("role"):
        parts.append(f"Role: {user['role']}")
    return f"Agent card updated for {user['name']}.\n" + "\n".join(parts)


@mcp_server.tool()
async def discover_agents() -> str:
    try:
        agents = await _api_get("/api/a2a/agents")
    except Exception as exc:
        return f"Error: {exc}"
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


@mcp_server.tool()
async def send_message(
    recipient_id: str,
    body: str,
    message_type: str = "info",
    subject: Optional[str] = None,
) -> str:
    payload: dict[str, Any] = {
        "recipient_id": recipient_id,
        "message_type": message_type,
        "subject": subject,
        "body": body,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    try:
        message = await _api_post("/api/a2a/messages", body=payload)
    except Exception as exc:
        return f"Error: {exc}"

    sender_name = message.get("sender_name") or "you"
    recipient_name = message.get("recipient_name") or recipient_id
    subject_line = f"Subject: {subject}\n" if subject else ""
    return (
        f"Message sent to {recipient_name} [{message['message_type']}].\n"
        f"From: {sender_name}\n"
        f"{subject_line}"
        f"{_preview(message['body'], 200)}"
    )


@mcp_server.tool()
async def get_pending_messages(
    mark_as_read: bool = False,
    include_read: bool = False,
) -> str:
    try:
        messages = await _api_get("/api/a2a/messages", params={"unread_only": not include_read})
    except Exception as exc:
        return f"Error: {exc}"

    if not messages:
        return "No unread messages." if not include_read else "No messages."

    marked_count = 0
    if mark_as_read:
        unread_messages = [message for message in messages if not message.get("read")]
        for message in unread_messages:
            try:
                await _api_patch(f"/api/a2a/messages/{message['id']}/read")
                message["read"] = True
                marked_count += 1
            except Exception:
                continue

    label = "Recent messages" if include_read else "Unread messages"
    lines = [f"# {label} ({len(messages)})"]
    for message in messages:
        subject = f" — {message['subject']}" if message.get("subject") else ""
        read_str = "  [read]" if message.get("read") else ""
        sender_name = message.get("sender_name") or message.get("sender_id")
        lines.append(f"\n## [{message['message_type']}]{subject}{read_str}")
        lines.append(
            "  From: "
            f"{sender_name}  |  ID: {message['id']}  |  {_format_timestamp(message.get('created_at'))}"
        )
        lines.append(f"  {message['body']}")
    if mark_as_read and marked_count > 0:
        lines.append(f"\n(Marked {marked_count} message(s) as read)")
    return "\n".join(lines)


@mcp_server.tool()
async def list_team_members() -> str:
    try:
        members = await _api_get("/api/teams/members")
    except Exception as exc:
        return f"Error: {exc}"
    lines = [f"# Team Members ({len(members)})"]
    for member in members:
        role = f" [{member['role']}]" if member.get("role") else ""
        lines.append(
            f"  {member['user_type'].upper()} {member['name']}{role} <{member['email']}> (ID: {member['id']})"
        )
    return "\n".join(lines)


@mcp_server.tool(description="Record a durable project fact")
async def create_fact(
    project_id: str,
    title: str,
    body: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "category": category,
        "author_type": "agent",
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    try:
        fact = await _api_post(f"/api/projects/{project_id}/facts", body=payload)
    except Exception as exc:
        return f"Error: {exc}"
    category_str = f" [{fact['category']}]" if fact.get("category") else ""
    return f"Fact recorded{category_str}: {fact['title']} (ID: {fact['id']})"


@mcp_server.tool(description="List all facts for a project. Facts are durable knowledge — conventions, constraints, and context.")
async def list_facts(project_id: str, q: Optional[str] = None) -> str:
    try:
        result = await _api_get(f"/api/projects/{project_id}/facts", params={"q": q, "limit": 100})
        facts = result.get("items", [])
    except Exception as exc:
        return f"Error: {exc}"
    if not facts:
        return "No facts recorded yet."

    lines = [f"# Project Facts ({len(facts)})"]
    for fact in facts:
        category_str = f" [{fact['category']}]" if fact.get("category") else ""
        lines.append(f"\n- **{fact['title']}**{category_str} (ID: {fact['id']})")
        if fact.get("body"):
            lines.append(f"  {_preview(fact['body'], 200)}")
    return "\n".join(lines)


@mcp_server.tool(description="Publish a reusable skill that other agents can consume")
async def create_skill(
    title: str,
    body: str,
    project_id: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "category": category,
        "tags": tags,
        "author_type": "agent",
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    try:
        if project_id:
            skill = await _api_post(f"/api/projects/{project_id}/skills", body=payload)
        else:
            skill = await _api_post("/api/skills", body=payload)
    except Exception as exc:
        return f"Error: {exc}"
    scope = f"project {project_id}" if project_id else "team-wide"
    category_str = f" [{skill['category']}]" if skill.get("category") else ""
    return f"Skill published{category_str}: '{skill['title']}' ({scope}) (ID: {skill['id']})"


@mcp_server.tool(description="List skills available to your team, optionally filtered by project, category, or search query")
async def list_skills(
    project_id: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
) -> str:
    try:
        result = await _api_get(
            "/api/skills",
            params={"project_id": project_id, "category": category, "q": q, "limit": 50},
        )
        skills = result.get("items", [])
    except Exception as exc:
        return f"Error: {exc}"
    if not skills:
        return "No skills found."

    lines = [f"# Skills ({len(skills)})"]
    for skill in skills:
        scope = f"project:{skill['project_id']}" if skill.get("project_id") else "team-wide"
        category_str = f" [{skill['category']}]" if skill.get("category") else ""
        tags_str = f" tags:{','.join(skill['tags'])}" if skill.get("tags") else ""
        lines.append(f"\n- **{skill['title']}**{category_str}{tags_str} ({scope}) (ID: {skill['id']})")
        if skill.get("body"):
            lines.append(f"  {_preview(skill['body'], 200)}")
    return "\n".join(lines)


@mcp_server.tool(description="Get the full content of a skill by ID")
async def get_skill(skill_id: str) -> str:
    try:
        skill = await _api_get(f"/api/skills/{skill_id}")
    except Exception as exc:
        return f"Error: {exc}"
    scope = f"project:{skill['project_id']}" if skill.get("project_id") else "team-wide"
    category_str = f"Category: {skill['category']}\n" if skill.get("category") else ""
    tags_str = f"Tags: {', '.join(skill['tags'])}\n" if skill.get("tags") else ""
    return (
        f"# {skill['title']}\n"
        f"ID: {skill['id']}\n"
        f"Scope: {scope}\n"
        f"{category_str}{tags_str}"
        f"Author: {skill['author_type']} ({skill['author_id']})\n"
        f"\n{skill['body']}"
    )


@mcp_server.tool(description="Update a skill's content")
async def update_skill(
    skill_id: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if category is not None:
        payload["category"] = category
    if tags is not None:
        payload["tags"] = tags
    if not payload:
        return "Error: Provide at least one field to update."
    try:
        skill = await _api_patch(f"/api/skills/{skill_id}", body=payload)
        return f"Skill updated: '{skill['title']}' (ID: {skill['id']})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Delete a skill")
async def delete_skill(skill_id: str) -> str:
    title = ""
    try:
        skill = await _api_get(f"/api/skills/{skill_id}")
        title = skill.get("title", "")
    except Exception:
        pass
    try:
        await _api_delete(f"/api/skills/{skill_id}")
        if title:
            return f"Skill deleted: '{title}' (ID: {skill_id})"
        return f"Skill deleted (ID: {skill_id})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Add a comment to a task")
async def add_task_comment(task_id: str, body: str) -> str:
    task_title = ""
    try:
        task = await _api_get(f"/api/tasks/{task_id}")
        task_title = task.get("title", "")
    except Exception:
        pass
    try:
        comment = await _api_post(f"/api/tasks/{task_id}/comments", body={"body": body})
    except Exception as exc:
        return f"Error: {exc}"
    if task_title:
        return f"Comment added to '{task_title}' (comment ID: {comment['id']})"
    return f"Comment added (comment ID: {comment['id']})"


@mcp_server.tool(description="List comments on a task")
async def list_task_comments(task_id: str) -> str:
    task_title = "task"
    try:
        task = await _api_get(f"/api/tasks/{task_id}")
        task_title = task.get("title") or task_title
    except Exception:
        pass
    try:
        comments = await _api_get(f"/api/tasks/{task_id}/comments")
    except Exception as exc:
        return f"Error: {exc}"
    if not comments:
        return f"No comments on '{task_title}'."
    lines = [f"# Comments on '{task_title}' ({len(comments)})"]
    for comment in comments:
        author_name = comment.get("author_name") or comment.get("author_id")
        lines.append(f"\n**{author_name}** — {_format_timestamp(comment.get('created_at'))} (ID: {comment['id']})")
        lines.append(comment["body"])
    return "\n".join(lines)


@mcp_server.tool(description="Create multiple tasks in a project in a single call. Useful for bootstrapping a milestone or feature with several tasks at once.")
async def batch_create_tasks(project_id: str, tasks: list[dict]) -> str:
    if not tasks:
        return "Error: No tasks provided."
    created: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, task in enumerate(tasks):
        title = task.get("title")
        if not title:
            errors.append(f"Task {index}: missing required field 'title'")
            continue
        status = task.get("status", "todo")
        if status not in VALID_STATUSES:
            errors.append(f"Task {index} ({title}): invalid status '{status}'")
            continue
        payload: dict[str, Any] = {
            "title": title,
            "description": task.get("description", ""),
            "status": status,
            "priority": task.get("priority"),
            "estimate": task.get("estimate"),
            "milestone_id": None if task.get("milestone_id") == "" else task.get("milestone_id"),
            "assignee_id": None if task.get("assignee_id") == "" else task.get("assignee_id"),
            "sort_order": task.get("sort_order"),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        try:
            created_task = await _api_post(f"/api/projects/{project_id}/tasks", body=payload)
            created.append(created_task)
        except Exception as exc:
            errors.append(f"Task {index} ({title}): {exc}")

    lines = [f"Created {len(created)}/{len(tasks)} tasks in project {project_id}:"]
    for task in created:
        lines.append(f"  - {task['title']} [{task['status']}] (ID: {task['id']})")
    if errors:
        lines.append(f"\nErrors ({len(errors)}):")
        for error in errors:
            lines.append(f"  - {error}")
    return "\n".join(lines)


@mcp_server.tool(description="Update a project's name or description.")
async def update_project(project_id: str, name: Optional[str] = None, description: Optional[str] = None) -> str:
    if name is None and description is None:
        return "Error: Provide at least one field to update (name or description)."
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    try:
        project = await _api_patch(f"/api/projects/{project_id}", body=payload)
        return f"Project updated: {project['name']} (ID: {project['id']})"
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Add a dependency: task_id is blocked by depends_on_id")
async def add_dependency(task_id: str, depends_on_id: str) -> str:
    try:
        task = await _api_get(f"/api/tasks/{task_id}")
        dep_task = await _api_get(f"/api/tasks/{depends_on_id}")
        await _api_post(f"/api/tasks/{task_id}/dependencies", body={"depends_on_id": depends_on_id})
        return f"Dependency added: '{task['title']}' is now blocked by '{dep_task['title']}'."
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Remove a dependency between two tasks")
async def remove_dependency(task_id: str, depends_on_id: str) -> str:
    task_title = ""
    try:
        task = await _api_get(f"/api/tasks/{task_id}")
        task_title = task.get("title", "")
    except Exception:
        pass
    try:
        await _api_delete(f"/api/tasks/{task_id}/dependencies/{depends_on_id}")
        if task_title:
            return f"Dependency removed from task '{task_title}'."
        return f"Dependency removed from task {task_id}."
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="List all dependencies (blocked-by tasks) for a given task")
async def list_dependencies(task_id: str) -> str:
    try:
        task = await _api_get(f"/api/tasks/{task_id}")
        dependencies = await _api_get(f"/api/tasks/{task_id}/dependencies")
    except Exception as exc:
        return f"Error: {exc}"
    if not dependencies:
        return f"Task '{task['title']}' has no dependencies."
    lines = [f"'{task['title']}' is blocked by:"]
    for dep in dependencies:
        lines.append(f"  - [{dep['status']}] {dep['title']} (ID: {dep['id']})")
    return "\n".join(lines)


@mcp_server.tool(description="Get recent changes in a project since a given timestamp")
async def get_changes_since(project_id: str, since: str) -> str:
    try:
        changes = await _api_get(f"/api/projects/{project_id}/changes", params={"since": since})
    except Exception as exc:
        return f"Error: {exc}"
    if changes.get("total", 0) == 0:
        return f"No changes since {since}."

    lines = [f"# Changes since {changes.get('since', since)} ({changes.get('total', 0)} total)"]
    for group in changes.get("groups", []):
        entity_type = group.get("entity_type", "unknown")
        group_changes = group.get("changes", [])
        lines.append(f"\n## {entity_type.title()} ({len(group_changes)} changes)")
        for entry in group_changes:
            actor_name = entry.get("actor_name") or "system"
            title_str = f" '{entry['entity_title']}'" if entry.get("entity_title") else ""
            lines.append(
                f"  - [{entry['action']}]{title_str} by {actor_name} at {_format_timestamp(entry.get('created_at'))}"
            )
            if entry.get("action") == "updated" and entry.get("new_values"):
                changed = ", ".join(f"{key}: {value}" for key, value in entry["new_values"].items())
                lines.append(f"    → {_preview(changed, 200)}")
    if changes.get("truncated"):
        lines.append("\n(Showing first 200 changes. Use a more recent 'since' to paginate.)")
    return "\n".join(lines)


@mcp_server.tool(description="Search across tasks, decisions, facts, and skills in a single call")
async def search(
    project_id: str,
    q: str,
    limit: int = 5,
) -> str:
    if not q or not q.strip():
        return "Error: Search query 'q' is required."
    per_entity_limit = max(1, min(limit, 20))
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            await _api_get(f"/api/projects/{project_id}", client=client)
            tasks_page = await _api_get(
                f"/api/projects/{project_id}/tasks",
                params={"q": q, "limit": per_entity_limit},
                client=client,
            )
            decisions_page = await _api_get(
                f"/api/projects/{project_id}/decisions",
                params={"q": q, "limit": per_entity_limit},
                client=client,
            )
            facts_page = await _api_get(
                f"/api/projects/{project_id}/facts",
                params={"q": q, "limit": per_entity_limit},
                client=client,
            )
            skills_page = await _api_get(
                "/api/skills",
                params={"project_id": project_id, "q": q, "limit": per_entity_limit},
                client=client,
            )
    except Exception as exc:
        return f"Error: {exc}"

    tasks = tasks_page.get("items", [])
    decisions = decisions_page.get("items", [])
    facts = facts_page.get("items", [])
    skills = skills_page.get("items", [])

    total = len(tasks) + len(decisions) + len(facts) + len(skills)
    if total == 0:
        return f"No results for '{q}'."

    lines = [f"# Search results for '{q}' ({total} hits)"]
    if tasks:
        lines.append(f"\n## Tasks ({len(tasks)})")
        for task in tasks:
            lines.append(f"  - [{task['status']}] {task['title']} (ID: {task['id']})")
            if task.get("description"):
                lines.append(f"    {_preview(task['description'])}")

    if decisions:
        lines.append(f"\n## Decisions ({len(decisions)})")
        for decision in decisions:
            lines.append(f"  - {decision['title']} (ID: {decision['id']})")
            if decision.get("rationale"):
                lines.append(f"    {_preview(decision['rationale'])}")

    if facts:
        lines.append(f"\n## Facts ({len(facts)})")
        for fact in facts:
            category = f" [{fact['category']}]" if fact.get("category") else ""
            lines.append(f"  - {fact['title']}{category} (ID: {fact['id']})")
            if fact.get("body"):
                lines.append(f"    {_preview(fact['body'])}")

    if skills:
        lines.append(f"\n## Skills ({len(skills)})")
        for skill in skills:
            scope = "team-wide" if not skill.get("project_id") else "project"
            lines.append(f"  - {skill['title']} ({scope}) (ID: {skill['id']})")
            if skill.get("body"):
                lines.append(f"    {_preview(skill['body'])}")
    return "\n".join(lines)
