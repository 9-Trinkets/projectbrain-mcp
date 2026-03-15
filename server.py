import json
from datetime import datetime
from typing import Any, Optional

import httpx
from actions.collab_actions import COLLABORATION_ACTION_HANDLERS as COLLAB_MODULE_HANDLERS
from actions.context_actions import CONTEXT_ACTION_HANDLERS as CONTEXT_MODULE_HANDLERS
from actions.knowledge_actions import (
    KNOWLEDGE_ACTION_HANDLERS as KNOWLEDGE_MODULE_HANDLERS,
    normalize_knowledge_entity,
    validate_knowledge_entity,
)
from actions.milestone_actions import TASKS_MILESTONE_ACTION_HANDLERS
from actions.tasks_actions import (
    TASKS_CORE_ACTION_HANDLERS,
    TASKS_RELATIONSHIP_ACTION_HANDLERS,
    TaskBatchUpdateItem,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import TransportSecuritySettings

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
VALID_MILESTONE_STATUSES = {"planned", "in_progress", "completed", "cancelled"}
VALID_RESPONSE_MODES = {"human", "json", "both"}
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


def _milestone_to_dict(milestone: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": milestone.get("id"),
        "project_id": milestone.get("project_id"),
        "title": milestone.get("title"),
        "description": milestone.get("description"),
        "due_date": milestone.get("due_date"),
        "status": milestone.get("status"),
        "position": milestone.get("position"),
        "created_at": milestone.get("created_at"),
        "updated_at": milestone.get("updated_at"),
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

@mcp_server.resource(
    "projectbrain://server/overview",
    name="server_overview",
    title="Project Brain MCP Server Overview",
    description="High-level capabilities and discovery metadata for this MCP server.",
    mime_type="application/json",
)
async def server_overview_resource() -> str:
    payload = {
        "name": "ProjectBrain",
        "transport": "streamable-http",
        "discovery_methods": [
            "initialize",
            "notifications/initialized",
            "ping",
            "tools/list",
            "resources/list",
            "resources/templates/list",
            "prompts/list",
        ],
        "tools": ["context", "projects", "tasks", "knowledge", "collaboration"],
        "notes": (
            "Unauthenticated discovery is enabled for list/initialize methods only. "
            "Tool execution and data access require a bearer token."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp_server.resource(
    "projectbrain://playbooks/default-workflow",
    name="default_workflow_playbook",
    title="Project Brain Default Workflow",
    description="Recommended baseline workflow for navigating and executing work in Project Brain.",
    mime_type="text/plain",
)
async def default_workflow_playbook_resource() -> str:
    return (
        "1. projects(action=\"list\")\n"
        "2. context(action=\"session\", project_id=...)\n"
        "3. tasks(action=\"list\", project_id=..., status=\"todo\")\n"
        "4. tasks(action=\"update\", task_id=..., status=\"in_progress\")\n"
        "5. Do the work and record comments/knowledge\n"
        "6. tasks(action=\"update\", task_id=..., status=\"done\")"
    )


@mcp_server.prompt(
    name="project_brain_session_bootstrap",
    title="Project Brain Session Bootstrap",
    description="Prompt template for starting work in a specific project with the context and task tools.",
)
def project_brain_session_bootstrap_prompt(project_id: str) -> str:
    return (
        "Start a focused Project Brain session for this project.\n"
        f"- project_id: {project_id}\n"
        "1) Call context(action=\"session\", project_id=project_id).\n"
        "2) Summarize active priorities and blockers.\n"
        "3) Call tasks(action=\"list\", project_id=project_id, status=\"todo\").\n"
        "4) Recommend the top task to claim next with rationale."
    )


@mcp_server.prompt(
    name="project_brain_task_execution",
    title="Project Brain Task Execution",
    description="Prompt template for planning and executing a task while keeping lifecycle state accurate.",
)
def project_brain_task_execution_prompt(task_id: str, project_id: Optional[str] = None) -> str:
    project_line = f"- project_id: {project_id}\n" if project_id else ""
    return (
        "Execute this Project Brain task methodically.\n"
        f"- task_id: {task_id}\n"
        f"{project_line}"
        "1) Load task context with tasks(action=\"context\", task_id=task_id).\n"
        "2) Move task to in_progress if it is still todo.\n"
        "3) Propose implementation steps and expected validation.\n"
        "4) Add a concise progress comment.\n"
        "5) Mark done only after verification."
    )


@mcp_server.tool(description="Project context and discovery operations")
async def context(
    action: str = "session",
    project_id: Optional[str] = None,
    since: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 5,
    full_tool_mode: bool = False,
) -> str:
    """Actions: session, summary, changes, search, shortlist."""
    try:
        handler = CONTEXT_MODULE_HANDLERS.get(action)
        if handler is None:
            return "Error: action must be one of: session, summary, changes, search, shortlist."
        return await handler(
            api_get=_api_get,
            require_fields=_require_fields,
            preview=_preview,
            format_timestamp=_format_timestamp,
            request_timeout_seconds=settings.request_timeout_seconds,
            project_id=project_id,
            since=since,
            q=q,
            limit=limit,
            full_tool_mode=full_tool_mode,
        )
    except Exception as exc:
        return f"Error: {exc}"


async def _projects_action_list(**_: Any) -> str:
    items = await _api_get("/api/projects/")
    if not items:
        return "No projects found."
    lines = [f"- {item['name']}: {item.get('description') or '(no description)'} (ID: {item['id']})" for item in items]
    return "Projects:\n" + "\n".join(lines)


async def _projects_action_get(*, project_id: Optional[str], **_: Any) -> str:
    error = _require_fields("get", project_id=project_id)
    if error:
        return error
    item = await _api_get(f"/api/projects/{project_id}")
    return (
        f"# {item['name']}\n"
        f"ID: {item['id']}\n"
        f"Description: {item.get('description') or '(none)'}\n"
        f"Team: {item['team_id']}\n"
    )


async def _projects_action_create(*, name: Optional[str], description: Optional[str], **_: Any) -> str:
    error = _require_fields("create", name=name)
    if error:
        return error
    item = await _api_post("/api/projects/", body={"name": name, "description": description or ""})
    return f"Project created: {item['name']} (ID: {item['id']})"


async def _projects_action_update(*, project_id: Optional[str], name: Optional[str], description: Optional[str], **_: Any) -> str:
    error = _require_fields("update", project_id=project_id)
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


_PROJECTS_ACTION_HANDLERS = {
    "list": _projects_action_list,
    "get": _projects_action_get,
    "create": _projects_action_create,
    "update": _projects_action_update,
}


@mcp_server.tool(description="Project CRUD operations")
async def projects(
    action: str = "list",
    project_id: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """Actions: list, get, create, update."""
    try:
        handler = _PROJECTS_ACTION_HANDLERS.get(action)
        if handler is None:
            return "Error: action must be one of: list, get, create, update."
        return await handler(project_id=project_id, name=name, description=description)
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Task operations including dependencies and comments")
async def tasks(
    action: str,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    due_date: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    estimate: Optional[int] = None,
    sort_order: Optional[int] = None,
    milestone_id: Optional[str] = None,
    milestone_ids: Optional[list[str]] = None,
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
    """Actions: list, create, update, delete, context, batch_create, batch_update, add_dependency, remove_dependency, list_dependencies, add_comment, list_comments, list_milestones, get_milestone, create_milestone, update_milestone, delete_milestone, reorder_milestones."""
    try:
        action_args: dict[str, Any] = {
            "api_get": _api_get,
            "api_post": _api_post,
            "api_patch": _api_patch,
            "api_delete": _api_delete,
            "require_fields": _require_fields,
            "validate_response_mode": _validate_response_mode,
            "normalize_terms": _normalize_terms,
            "json_envelope": _json_envelope,
            "task_to_dict": _task_to_dict,
            "milestone_to_dict": _milestone_to_dict,
            "format_timestamp": _format_timestamp,
            "valid_task_statuses": VALID_TASK_STATUSES,
            "valid_milestone_statuses": VALID_MILESTONE_STATUSES,
            "project_id": project_id,
            "task_id": task_id,
            "title": title,
            "description": description,
            "due_date": due_date,
            "status": status,
            "priority": priority,
            "estimate": estimate,
            "sort_order": sort_order,
            "milestone_id": milestone_id,
            "milestone_ids": milestone_ids,
            "assignee_id": assignee_id,
            "q": q,
            "q_any": q_any,
            "q_all": q_all,
            "q_not": q_not,
            "cursor": cursor,
            "limit": limit,
            "response_mode": response_mode,
            "depends_on_id": depends_on_id,
            "comment_body": comment_body,
            "items": items,
            "updates": updates,
        }
        for action_map in (
            TASKS_CORE_ACTION_HANDLERS,
            TASKS_MILESTONE_ACTION_HANDLERS,
            TASKS_RELATIONSHIP_ACTION_HANDLERS,
        ):
            handler = action_map.get(action)
            if handler is not None:
                return await handler(**action_args)
        return (
            "Error: action must be one of: list, create, update, delete, context, batch_create, "
            "batch_update, add_dependency, remove_dependency, list_dependencies, add_comment, list_comments, "
            "list_milestones, get_milestone, create_milestone, update_milestone, delete_milestone, reorder_milestones."
        )
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Decision/fact/skill operations")
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
    normalized_entity = normalize_knowledge_entity(entity)
    entity_error = validate_knowledge_entity(normalized_entity)
    if entity_error:
        return entity_error
    try:
        handler = KNOWLEDGE_MODULE_HANDLERS.get(action)
        if handler is None:
            return "Error: action must be one of: list, get, create, update, delete."
        return await handler(
            api_get=_api_get,
            api_post=_api_post,
            api_patch=_api_patch,
            api_delete=_api_delete,
            require_fields=_require_fields,
            preview=_preview,
            entity=normalized_entity,
            project_id=project_id,
            item_id=item_id,
            title=title,
            body=body,
            rationale=rationale,
            task_id=task_id,
            category=category,
            tags=tags,
            q=q,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:
        return f"Error: {exc}"


@mcp_server.tool(description="Team, messaging, and identity operations")
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
        handler = COLLAB_MODULE_HANDLERS.get(action)
        if handler is None:
            return "Error: action must be one of: list_team_members, discover_agents, send_message, get_messages, update_my_card, join_team."
        return await handler(
            api_get=_api_get,
            api_post=_api_post,
            api_patch=_api_patch,
            require_fields=_require_fields,
            preview=_preview,
            format_timestamp=_format_timestamp,
            recipient_id=recipient_id,
            body=body,
            message_type=message_type,
            subject=subject,
            include_read=include_read,
            mark_as_read=mark_as_read,
            description=description,
            skills=skills,
            role=role,
            invite_code=invite_code,
        )
    except Exception as exc:
        return f"Error: {exc}"
