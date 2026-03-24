import json
from datetime import datetime
from typing import Annotated, Any, Optional

import httpx
from actions.file_actions import FILE_ACTION_HANDLERS
from actions.collab_actions import COLLABORATION_ACTION_HANDLERS as COLLAB_MODULE_HANDLERS
from actions.context_actions import CONTEXT_ACTION_HANDLERS as CONTEXT_MODULE_HANDLERS
from actions.knowledge_actions import (
    KNOWLEDGE_ACTION_HANDLERS as KNOWLEDGE_MODULE_HANDLERS,
    normalize_knowledge_entity,
    validate_knowledge_entity,
)
from actions.milestone_actions import TASKS_MILESTONE_ACTION_HANDLERS
from actions.workflow_actions import PROJECTS_WORKFLOW_ACTION_HANDLERS
from actions.tasks_actions import (
    TASKS_CORE_ACTION_HANDLERS,
    TASKS_RELATIONSHIP_ACTION_HANDLERS,
    TaskBatchUpdateItem,
)
from errors import APIError, MCPError, ValidationError
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

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

_FALLBACK_TASK_STATUSES = {"todo", "in_progress", "blocked", "done", "cancelled"}


async def _get_valid_task_statuses(project_id: Optional[str], task_id: Optional[str] = None) -> set[str]:
    """Fetch workflow statuses for a project; fall back to defaults if unavailable.

    If project_id is absent but task_id is provided, look up the task to resolve project_id.
    """
    resolved_project_id = await _resolve_project_id(project_id) if project_id else None
    if not resolved_project_id and task_id:
        try:
            task = await _api_get(f"/api/tasks/{task_id}")
            resolved_project_id = task.get("project_id")
        except Exception:
            pass
    if not resolved_project_id:
        return _FALLBACK_TASK_STATUSES
    try:
        workflow = await _api_get(f"/api/projects/{resolved_project_id}/workflow")
        statuses = {s["name"] for s in workflow.get("statuses", [])}
        return statuses if statuses else _FALLBACK_TASK_STATUSES
    except Exception:
        return _FALLBACK_TASK_STATUSES


async def _resolve_project_id(value: str) -> str:
    """Resolve a project ID (full UUID, short prefix, or name) to a full UUID."""
    if not value:
        return value
    import re
    # Full UUID — return as-is
    if re.match(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", value, re.I):
        return value

    # Otherwise, fetch projects and search
    projects = await _api_get("/api/projects/")

    # Short hex prefix (e.g. "a84c4871")
    if re.match(r"^[0-9a-f]+$", value, re.I) and len(value) >= 4:
        prefix = value.lower()
        matches = [p for p in projects if p["id"].lower().startswith(prefix)]
        if len(matches) == 1:
            return matches[0]["id"]
        if len(matches) > 1:
            names = ", ".join(f"{m['name']} ({m['id'][:8]})" for m in matches)
            raise ValidationError(f"Ambiguous project ID prefix '{value}'. Matching projects: {names}. Use a longer prefix or the full UUID.", field_name="project_id")

    # Name search
    needle = value.lower()
    matches = [p for p in projects if needle in p["name"].lower()]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) == 0:
        raise ValidationError(f"No project found matching '{value}'.", field_name="project_id")
    names = ", ".join(m["name"] for m in matches)
    raise ValidationError(f"Ambiguous project name '{value}'. Matching projects: {names}. Use the full UUID.", field_name="project_id")
VALID_MILESTONE_STATUSES = {"planned", "in_progress", "completed", "cancelled"}
VALID_RESPONSE_MODES = {"human", "json", "both"}
DEFAULT_TOOL_ANNOTATION_HINTS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}


def _tool_annotations(
    *,
    title: str,
    read_only: Optional[bool] = None,
    destructive: Optional[bool] = None,
    idempotent: Optional[bool] = None,
    open_world: Optional[bool] = None,
) -> ToolAnnotations:
    """Build MCP tool annotations with explicit defaults when hints are omitted."""
    return ToolAnnotations(
        title=title,
        readOnlyHint=DEFAULT_TOOL_ANNOTATION_HINTS["readOnlyHint"] if read_only is None else read_only,
        destructiveHint=DEFAULT_TOOL_ANNOTATION_HINTS["destructiveHint"] if destructive is None else destructive,
        idempotentHint=DEFAULT_TOOL_ANNOTATION_HINTS["idempotentHint"] if idempotent is None else idempotent,
        openWorldHint=DEFAULT_TOOL_ANNOTATION_HINTS["openWorldHint"] if open_world is None else open_world,
    )


def _tool_meta(
    *,
    risk_level: str,
    latency_class: str,
    cost_class: str,
    auth_required: bool = True,
    deprecated: bool = False,
    read_only: Optional[bool] = None,
    idempotent: Optional[bool] = None,
) -> dict[str, Any]:
    """Attach custom planning/safety metadata to tool descriptors."""
    return {
        "risk_level": risk_level,
        "latency_class": latency_class,
        "cost_class": cost_class,
        "auth_required": auth_required,
        "deprecated": deprecated,
        "read_only": DEFAULT_TOOL_ANNOTATION_HINTS["readOnlyHint"] if read_only is None else read_only,
        "idempotent": DEFAULT_TOOL_ANNOTATION_HINTS["idempotentHint"] if idempotent is None else idempotent,
        "annotation_defaults": dict(DEFAULT_TOOL_ANNOTATION_HINTS),
    }

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
        raise ValidationError("Not authenticated. Provide a valid bearer token.", status_code=401)

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
        raise APIError(f"Unable to reach API service: {exc}") from exc

    if response.status_code >= 400:
        detail = ""
        try:
            detail = _error_detail(response.json())
        except ValueError:
            detail = response.text.strip()
        if not detail:
            detail = f"Request failed with status {response.status_code}"
        raise APIError(detail, status_code=response.status_code)

    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise APIError("API returned a non-JSON response.") from exc


async def _api_get(path: str, *, params: Optional[dict[str, Any]] = None, client: Optional[httpx.AsyncClient] = None) -> Any:
    return await _api_request("GET", path, params=params, client=client)


async def _api_post(path: str, *, body: Optional[dict[str, Any]] = None, client: Optional[httpx.AsyncClient] = None) -> Any:
    return await _api_request("POST", path, json_body=body, client=client)


async def _api_patch(path: str, *, body: Optional[dict[str, Any]] = None, client: Optional[httpx.AsyncClient] = None) -> Any:
    return await _api_request("PATCH", path, json_body=body, client=client)


async def _api_delete(path: str, *, params: Optional[dict[str, Any]] = None, client: Optional[httpx.AsyncClient] = None) -> None:
    await _api_request("DELETE", path, params=params, client=client)


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


def _json_error_envelope(tool: str, error: MCPError, query: Optional[dict] = None) -> str:
    payload: dict[str, object] = {
        "ok": False,
        "data": None,
        "meta": {"tool": tool, "response_mode": "json"},
        "error": {
            "type": error.error_type,
            "message": error.message,
            "code": error.status_code,
        },
    }
    if query is not None:
        payload["meta"]["query"] = query
    return json.dumps(payload, ensure_ascii=False)


@mcp_server.resource(
    "projectbrain://server/overview",
    name="server_overview",
    title="ProjectBrain MCP Server Overview",
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
        "tools": ["context", "projects", "tasks", "knowledge", "files", "collaboration"],
        "notes": (
            "Unauthenticated discovery is enabled for list/initialize methods only. "
            "Tool execution and data access require a bearer token."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp_server.resource(
    "projectbrain://playbooks/default-workflow",
    name="default_workflow_playbook",
    title="ProjectBrain Default Workflow",
    description="Recommended baseline workflow for navigating and executing work in ProjectBrain.",
    mime_type="text/plain",
)
async def default_workflow_playbook_resource() -> str:
    return (
        "ProjectBrain Default Workflow\n"
        "==============================\n"
        "\n"
        "Getting oriented\n"
        "─────────────────\n"
        "1. projects(action=\"list\")                          — find your project\n"
        "2. context(action=\"session\", project_id=...)        — load priorities, blockers, recent activity\n"
        "3. tasks(action=\"list\", project_id=..., status=...) — see what needs doing\n"
        "\n"
        "Doing work\n"
        "───────────\n"
        "4. tasks(action=\"update\", task_id=..., status=\"in_progress\")  — claim the task\n"
        "5. Do the work\n"
        "6. Log meaningful progress as comments — not just the final result\n"
        "   tasks(action=\"add_comment\", task_id=..., comment_body=...)\n"
        "\n"
        "Saving output\n"
        "──────────────\n"
        "7. Save your primary output as a file before finishing:\n"
        "   - First-pass   → files(action=\"create\", file_type=draft|spec|report|code, ...)\n"
        "   - Revision     → files(action=\"add_version\", file_id=..., body=...)  ← never create a new file\n"
        "   - Code review  → files(action=\"create\", file_type=review, ...)\n"
        "\n"
        "Capturing knowledge (three types)\n"
        "───────────────────────────────────\n"
        "8. Save reusable findings to the knowledge base:\n"
        "   Fact     → an observation or finding worth remembering\n"
        "   knowledge(action=\"create\", entity=\"fact\", project_id=..., content=...)\n"
        "\n"
        "   Decision → a choice made with rationale, so future agents don't re-litigate it\n"
        "   knowledge(action=\"create\", entity=\"decision\", project_id=..., title=..., rationale=...)\n"
        "\n"
        "   Skill    → a reusable procedure or how-to that other agents can follow\n"
        "   knowledge(action=\"create\", entity=\"skill\", project_id=..., title=..., body=...)\n"
        "\n"
        "Finishing\n"
        "──────────\n"
        "9. tasks(action=\"update\", task_id=..., status=\"done\")\n"
        "   Or advance via next_action_policy if automated handoff is configured.\n"
    )


@mcp_server.prompt(
    name="project_brain_session_bootstrap",
    title="ProjectBrain Session Bootstrap",
    description="Prompt template for starting work in a specific project with the context and task tools.",
)
def project_brain_session_bootstrap_prompt(project_id: str) -> str:
    return (
        "Given you are starting a focused ProjectBrain session\n"
        f"And the target project_id is {project_id}\n"
        "When you initialize the session\n"
        "Then you must call context(action=\"session\", project_id=project_id)\n"
        "And you must summarize active priorities and blockers\n"
        "And you must call tasks(action=\"list\", project_id=project_id, status=\"todo\")\n"
        "And you must output a recommendation for the top task to claim next, including your rationale"
    )


@mcp_server.prompt(
    name="project_brain_task_execution",
    title="ProjectBrain Task Execution",
    description="Prompt template for planning and executing a task while keeping lifecycle state accurate.",
)
def project_brain_task_execution_prompt(task_id: str, project_id: Optional[str] = None) -> str:
    project_line = f"And the project_id is {project_id}\n" if project_id else ""
    return (
        "Given you are executing a ProjectBrain task\n"
        f"And the task_id is {task_id}\n"
        f"{project_line}"
        "When you begin work on the task\n"
        "Then you must load task context using tasks(action=\"context\", task_id=task_id)\n"
        "And you must update the task status to \"in_progress\" if it is currently \"todo\"\n"
        "And you must output proposed implementation steps and expected validation\n"
        "And you must add a concise progress comment using the tasks tool\n"
        "And you must mark the task as \"done\" ONLY after verifying the changes"
    )


@mcp_server.tool(
    description="Project context and discovery operations",
    annotations=_tool_annotations(
        title="Project Context",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    ),
    meta=_tool_meta(
        risk_level="low",
        latency_class="network",
        cost_class="low",
        auth_required=True,
        deprecated=False,
        read_only=True,
        idempotent=True,
    ),
)
async def context(
    action: Annotated[str, Field(description="Context action: session, summary, changes, search, or shortlist.")] = "session",
    project_id: Annotated[Optional[str], Field(description="Project UUID used by session/summary/changes/search actions.")] = None,
    since: Annotated[Optional[str], Field(description="ISO-8601 timestamp used by changes action to bound results.")] = None,
    q: Annotated[Optional[str], Field(description="Search query used by search/shortlist actions.")] = None,
    limit: Annotated[int, Field(description="Maximum number of results to return for list-like context actions.")] = 5,
    full_tool_mode: Annotated[bool, Field(description="When true, shortlist includes full operation catalog instead of top-ranked subset.")] = False,
    task_id: Annotated[Optional[str], Field(description="Optional task UUID for context-sensitive knowledge retrieval.")] = None,
    intent: Annotated[Optional[str], Field(description="Optional user intent for semantic knowledge retrieval.")] = None,
    knowledge_limit: Annotated[int, Field(description="Max number of knowledge items to return when intent is provided.")] = 5,
    response_mode: Annotated[str, Field(description="Output format: human, json, or both (where supported).")] = "human",
) -> str:
    """Actions: session, summary, changes, search, shortlist."""
    try:
        handler = CONTEXT_MODULE_HANDLERS.get(action)
        if handler is None:
            return "Error: action must be one of: session, summary, changes, search, shortlist."
        resolved_project_id = await _resolve_project_id(project_id) if project_id else None
        return await handler(
            api_get=_api_get,
            require_fields=_require_fields,
            preview=_preview,
            format_timestamp=_format_timestamp,
            json_envelope=_json_envelope,
            json_error_envelope=_json_error_envelope,
            request_timeout_seconds=settings.request_timeout_seconds,
            project_id=resolved_project_id,
            since=since,
            q=q,
            limit=limit,
            full_tool_mode=full_tool_mode,
            task_id=task_id,
            intent=intent,
            knowledge_limit=knowledge_limit,
            response_mode=response_mode,
        )
    except MCPError as exc:
        if response_mode == "json":
            return _json_error_envelope("context", exc)
        return f"Error: {exc.message}"
    except Exception as exc:
        if response_mode == "json":
            return _json_error_envelope("context", MCPError(str(exc)))
        return f"Error: {exc}"


async def _projects_action_list(*, response_mode: str, json_envelope: Any, **_: Any) -> str:
    items = await _api_get("/api/projects/")
    if response_mode == "json":
        return json_envelope("projects", {"items": items}, query={"action": "list"})
    if not items:
        return "No projects found."
    lines = [f"- {item['name']}: {item.get('description') or '(no description)'} (ID: {item['id']})" for item in items]
    return "Projects:\n" + "\n".join(lines)


async def _projects_action_get(*, project_id: Optional[str], response_mode: str, json_envelope: Any, **_: Any) -> str:
    error = _require_fields("get", project_id=project_id)
    if error:
        return error
    item = await _api_get(f"/api/projects/{project_id}")
    if response_mode == "json":
        return json_envelope("projects", item, query={"action": "get", "project_id": project_id})
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


@mcp_server.tool(
    description="Project CRUD and workflow management operations",
    annotations=_tool_annotations(
        title="Projects",
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=False,
    ),
    meta=_tool_meta(
        risk_level="medium",
        latency_class="network",
        cost_class="low",
        auth_required=True,
        deprecated=False,
        read_only=False,
        idempotent=False,
    ),
)
async def projects(
    action: Annotated[str, Field(description="Project action: list, get, create, update, get_workflow, add_workflow_stage, update_workflow_stage, delete_workflow_stage, or reorder_workflow_stages.")] = "list",
    project_id: Annotated[Optional[str], Field(description="Project UUID required for get, update, and workflow actions.")] = None,
    name: Annotated[Optional[str], Field(description="Project name used by create and update actions.")] = None,
    description: Annotated[Optional[str], Field(description="Project description used by create and update actions.")] = None,
    stage_id: Annotated[Optional[str], Field(description="Workflow stage UUID used by update_workflow_stage and delete_workflow_stage actions.")] = None,
    stage_name: Annotated[Optional[str], Field(description="Stage name used by add_workflow_stage and update_workflow_stage actions.")] = None,
    role_constraint: Annotated[Optional[str], Field(description="Role constraint for the stage used by add_workflow_stage and update_workflow_stage actions.")] = None,
    stage_ids: Annotated[Optional[list[str]], Field(description="Ordered stage UUID list used by reorder_workflow_stages action.")] = None,
    migrate_to_stage_id: Annotated[Optional[str], Field(description="Stage UUID to migrate tasks to when deleting a stage with tasks.")] = None,
    response_mode: Annotated[str, Field(description="Output format: human, json, or both (where supported).")] = "human",
) -> str:
    """Actions: list, get, create, update, get_workflow, add_workflow_stage, update_workflow_stage, delete_workflow_stage, reorder_workflow_stages."""
    try:
        resolved_project_id = await _resolve_project_id(project_id) if project_id else None
        action_args: dict[str, Any] = {
            "api_get": _api_get,
            "api_post": _api_post,
            "api_patch": _api_patch,
            "api_delete": _api_delete,
            "require_fields": _require_fields,
            "validate_response_mode": _validate_response_mode,
            "json_envelope": _json_envelope,
            "json_error_envelope": _json_error_envelope,
            "project_id": resolved_project_id,
            "name": name,
            "description": description,
            "stage_id": stage_id,
            "stage_name": stage_name,
            "role_constraint": role_constraint,
            "stage_ids": stage_ids,
            "migrate_to_stage_id": migrate_to_stage_id,
            "response_mode": response_mode,
        }
        core_handler = _PROJECTS_ACTION_HANDLERS.get(action)
        if core_handler is not None:
            return await core_handler(**action_args)
        workflow_handler = PROJECTS_WORKFLOW_ACTION_HANDLERS.get(action)
        if workflow_handler is not None:
            return await workflow_handler(**action_args)
        return "Error: action must be one of: list, get, create, update, get_workflow, add_workflow_stage, update_workflow_stage, delete_workflow_stage, reorder_workflow_stages."
    except MCPError as exc:
        if response_mode == "json":
            return _json_error_envelope("projects", exc)
        return f"Error: {exc.message}"
    except Exception as exc:
        if response_mode == "json":
            return _json_error_envelope("projects", MCPError(str(exc)))
        return f"Error: {exc}"


@mcp_server.tool(
    description="Task operations including dependencies and comments",
    annotations=_tool_annotations(
        title="Tasks",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    ),
    meta=_tool_meta(
        risk_level="high",
        latency_class="network",
        cost_class="medium",
        auth_required=True,
        deprecated=False,
        read_only=False,
        idempotent=False,
    ),
)
async def tasks(
    action: Annotated[
        str,
        Field(
            description=(
                "Task action to execute, for example list/create/update/delete/context, "
                "batch operations, dependency operations, comment operations, and milestone operations."
            )
        ),
    ],
    project_id: Annotated[Optional[str], Field(description="Project UUID used by project-scoped task and milestone actions.")] = None,
    task_id: Annotated[Optional[str], Field(description="Task UUID used by task-scoped actions such as update/delete/context/comments/dependencies.")] = None,
    title: Annotated[Optional[str], Field(description="Task or milestone title for create/update actions.")] = None,
    description: Annotated[Optional[str], Field(description="Task or milestone description for create/update actions.")] = None,
    due_date: Annotated[Optional[str], Field(description="Milestone due date (ISO-8601 date string) for create_milestone/update_milestone actions.")] = None,
    status: Annotated[Optional[str], Field(description="Target task or milestone status value for create/update operations.")] = None,
    priority: Annotated[Optional[str], Field(description="Task priority value for create/update operations.")] = None,
    estimate: Annotated[Optional[int], Field(description="Task estimate value for create/update operations.")] = None,
    sort_order: Annotated[Optional[int], Field(description="Task sort order value for create/update operations.")] = None,
    milestone_id: Annotated[Optional[str], Field(description="Milestone UUID for filtering, assignment, retrieval, update, or deletion actions.")] = None,
    milestone_ids: Annotated[Optional[list[str]], Field(description="Ordered milestone UUID list used by reorder_milestones action.")] = None,
    assignee_id: Annotated[Optional[str], Field(description="Assignee UUID for task create/update actions.")] = None,
    q: Annotated[Optional[str], Field(description="Search text used by list/list_milestones actions.")] = None,
    q_any: Annotated[Optional[list[str]], Field(description="Task list filter: match tasks containing any of these terms.")] = None,
    q_all: Annotated[Optional[list[str]], Field(description="Task list filter: match tasks containing all of these terms.")] = None,
    q_not: Annotated[Optional[list[str]], Field(description="Task list filter: exclude tasks containing any of these terms.")] = None,
    cursor: Annotated[Optional[str], Field(description="Pagination cursor used by task list action.")] = None,
    limit: Annotated[Optional[int], Field(description="Maximum number of results to return for list actions.")] = None,
    response_mode: Annotated[str, Field(description="Output format: human, json, or both (where supported).")] = "human",
    depends_on_id: Annotated[Optional[str], Field(description="Dependency task UUID used by add_dependency/remove_dependency actions.")] = None,
    comment_body: Annotated[Optional[str], Field(description="Comment body text used by add_comment action. When handing off work to the next agent, prefer the envelope format over raw JSON: put structured directives as 'LABEL: token-list' lines above a '---' separator, and place the human-readable summary below it. Example: 'TASK: abc-123\\nOUTCOME: approved\\n---\\nDesign approved. Ready for implementation.' Plain text comments are always accepted.")] = None,
    items: Annotated[Optional[list[dict[str, Any]]], Field(description="Task payload list used by batch_create action.")] = None,
    updates: Annotated[Optional[list[TaskBatchUpdateItem]], Field(description="Structured update payload list used by batch_update action.")] = None,
) -> str:
    """Actions: list, create, update, delete, context, get_my_tasks, batch_create, batch_update, add_dependency, remove_dependency, list_dependencies, add_comment, list_comments, list_milestones, get_milestone, create_milestone, update_milestone, delete_milestone, reorder_milestones."""
    try:
        resolved_project_id = await _resolve_project_id(project_id) if project_id else None
        action_args: dict[str, Any] = {
            "api_get": _api_get,
            "api_post": _api_post,
            "api_patch": _api_patch,
            "api_delete": _api_delete,
            "require_fields": _require_fields,
            "validate_response_mode": _validate_response_mode,
            "normalize_terms": _normalize_terms,
            "json_envelope": _json_envelope,
            "json_error_envelope": _json_error_envelope,
            "task_to_dict": _task_to_dict,
            "milestone_to_dict": _milestone_to_dict,
            "format_timestamp": _format_timestamp,
            "valid_task_statuses": await _get_valid_task_statuses(resolved_project_id, task_id),
            "valid_milestone_statuses": VALID_MILESTONE_STATUSES,
            "project_id": resolved_project_id,
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
    except MCPError as exc:
        if response_mode == "json":
            return _json_error_envelope("tasks", exc)
        return f"Error: {exc.message}"
    except Exception as exc:
        if response_mode == "json":
            return _json_error_envelope("tasks", MCPError(str(exc)))
        return f"Error: {exc}"


@mcp_server.tool(
    description="Decision/fact/skill operations",
    annotations=_tool_annotations(
        title="Knowledge",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    ),
    meta=_tool_meta(
        risk_level="medium",
        latency_class="network",
        cost_class="low",
        auth_required=True,
        deprecated=False,
        read_only=False,
        idempotent=False,
    ),
)
async def knowledge(
    entity: Annotated[str, Field(description="Knowledge entity type: decision, fact, or skill.")],
    action: Annotated[str, Field(description="Knowledge action: list, get, create, update, or delete.")],
    project_id: Annotated[Optional[str], Field(description="Project UUID for entity-scoped knowledge operations.")] = None,
    item_id: Annotated[Optional[str], Field(description="Knowledge item UUID used by get/update/delete actions.")] = None,
    title: Annotated[Optional[str], Field(description="Knowledge item title used by create/update actions.")] = None,
    body: Annotated[Optional[str], Field(description="Knowledge item body content used by create/update actions.")] = None,
    rationale: Annotated[Optional[str], Field(description="Decision rationale text used by decision create/update actions.")] = None,
    task_id: Annotated[Optional[str], Field(description="Related task UUID linked to knowledge entries.")] = None,
    category: Annotated[Optional[str], Field(description="Category label for knowledge classification.")] = None,
    tags: Annotated[Optional[list[str]], Field(description="Tag list for filtering and classification.")] = None,
    q: Annotated[Optional[str], Field(description="Search query for list action filtering.")] = None,
    cursor: Annotated[Optional[str], Field(description="Pagination cursor for list action.")] = None,
    limit: Annotated[Optional[int], Field(description="Maximum items to return for list action.")] = None,
    response_mode: Annotated[str, Field(description="Output format: human, json, or both (where supported).")] = "human",
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
        resolved_project_id = await _resolve_project_id(project_id) if project_id else None
        return await handler(
            api_get=_api_get,
            api_post=_api_post,
            api_patch=_api_patch,
            api_delete=_api_delete,
            require_fields=_require_fields,
            validate_response_mode=_validate_response_mode,
            preview=_preview,
            json_envelope=_json_envelope,
            json_error_envelope=_json_error_envelope,
            entity=normalized_entity,
            project_id=resolved_project_id,
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
            response_mode=response_mode,
        )
    except MCPError as exc:
        if response_mode == "json":
            return _json_error_envelope("knowledge", exc)
        return f"Error: {exc.message}"
    except Exception as exc:
        if response_mode == "json":
            return _json_error_envelope("knowledge", MCPError(str(exc)))
        return f"Error: {exc}"


@mcp_server.tool(
    description="Versioned file operations: documents, drafts, specs, reports, and any typed content linked to project entities",
    annotations=_tool_annotations(
        title="Files",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    ),
    meta=_tool_meta(
        risk_level="medium",
        latency_class="network",
        cost_class="low",
        auth_required=True,
        deprecated=False,
        read_only=False,
        idempotent=False,
    ),
)
async def files(
    action: Annotated[str, Field(description="File action: list, get, create, add_version, list_versions, or delete.")],
    project_id: Annotated[Optional[str], Field(description="Project UUID for list and create actions.")] = None,
    file_id: Annotated[Optional[str], Field(description="File UUID for get, add_version, list_versions, and delete actions.")] = None,
    file_type: Annotated[Optional[str], Field(description="File type: draft, spec, report, review, or code. Used by create and list actions.")] = None,
    title: Annotated[Optional[str], Field(description="File title for create action.")] = None,
    body: Annotated[Optional[str], Field(description="File content body for create and add_version actions.")] = None,
    entity_type: Annotated[Optional[str], Field(description="Polymorphic entity type (e.g. task, milestone) to link the file to.")] = None,
    entity_id: Annotated[Optional[str], Field(description="UUID of the linked entity (must pair with entity_type).")] = None,
    version: Annotated[Optional[int], Field(description="Specific version number to retrieve in get action; omit for latest.")] = None,
    response_mode: Annotated[str, Field(description="Output format: human, json, or both (where supported).")] = "human",
) -> str:
    """Actions: list, get, create, add_version, list_versions, delete."""
    try:
        handler = FILE_ACTION_HANDLERS.get(action)
        if handler is None:
            return "Error: action must be one of: list, get, create, add_version, list_versions, delete."
        resolved_project_id = await _resolve_project_id(project_id) if project_id else None
        return await handler(
            api_get=_api_get,
            api_post=_api_post,
            api_delete=_api_delete,
            require_fields=_require_fields,
            validate_response_mode=_validate_response_mode,
            json_envelope=_json_envelope,
            json_error_envelope=_json_error_envelope,
            project_id=resolved_project_id,
            file_id=file_id,
            file_type=file_type,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
            version=version,
            response_mode=response_mode,
        )
    except MCPError as exc:
        if response_mode == "json":
            return _json_error_envelope("files", exc)
        return f"Error: {exc.message}"
    except Exception as exc:
        if response_mode == "json":
            return _json_error_envelope("files", MCPError(str(exc)))
        return f"Error: {exc}"


@mcp_server.tool(
    description="Team, messaging, and identity operations",
    annotations=_tool_annotations(
        title="Collaboration",
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=False,
    ),
    meta=_tool_meta(
        risk_level="medium",
        latency_class="network",
        cost_class="low",
        auth_required=True,
        deprecated=False,
        read_only=False,
        idempotent=False,
    ),
)
async def collaboration(
    action: Annotated[
        str,
        Field(
            description=(
                "Collaboration action: list_team_members, discover_agents, get_agent_activity, "
                "send_message, get_messages, update_my_card, or join_team."
            )
        ),
    ],
    agent_id: Annotated[Optional[str], Field(description="Target agent UUID for get_agent_activity. Defaults to current user if omitted.")] = None,
    project_id: Annotated[Optional[str], Field(description="Optional project UUID to filter activity.")] = None,
    since: Annotated[Optional[str], Field(description="ISO 8601 timestamp to filter activity.")] = None,
    limit: Annotated[int, Field(description="Maximum activity items to return.")] = 20,
    recipient_id: Annotated[Optional[str], Field(description="Recipient member UUID used by send_message action.")] = None,
    body: Annotated[Optional[str], Field(description="Message body or profile description depending on action. For send_message, prefer the envelope format over raw JSON: put structured directives as 'LABEL: token-list' lines above a '---' separator, and place the human-readable text below it. Example: 'TASK: abc-123\\nSTAGE: design\\n---\\nTask handed off to you in stage design.' Messages without '---' are accepted as plain body.")] = None,
    message_type: Annotated[str, Field(description="Message type label for send_message action (for example: info).")] = "info",
    subject: Annotated[Optional[str], Field(description="Optional message subject for send_message action.")] = None,
    include_read: Annotated[bool, Field(description="When true, include previously read messages in get_messages action.")] = False,
    mark_as_read: Annotated[bool, Field(description="When true, mark fetched messages as read in get_messages action.")] = False,
    description: Annotated[Optional[str], Field(description="Agent/member profile description for update_my_card action.")] = None,
    invite_code: Annotated[Optional[str], Field(description="Team invite code used by join_team action.")] = None,
    response_mode: Annotated[str, Field(description="Output format: human, json, or both (where supported).")] = "human",
) -> str:
    """Actions: list_team_members, discover_agents, get_agent_activity, send_message, get_messages, update_my_card, join_team."""
    try:
        handler = COLLAB_MODULE_HANDLERS.get(action)
        if handler is None:
            return "Error: action must be one of: list_team_members, discover_agents, get_agent_activity, send_message, get_messages, update_my_card, join_team."
        resolved_project_id = await _resolve_project_id(project_id) if project_id else None
        return await handler(
            api_get=_api_get,
            api_post=_api_post,
            api_patch=_api_patch,
            require_fields=_require_fields,
            validate_response_mode=_validate_response_mode,
            preview=_preview,
            format_timestamp=_format_timestamp,
            json_envelope=_json_envelope,
            json_error_envelope=_json_error_envelope,
            agent_id=agent_id,
            project_id=resolved_project_id,
            since=since,
            limit=limit,
            recipient_id=recipient_id,
            body=body,
            message_type=message_type,
            subject=subject,
            include_read=include_read,
            mark_as_read=mark_as_read,
            description=description,
            invite_code=invite_code,
            response_mode=response_mode,
        )
    except MCPError as exc:
        if response_mode == "json":
            return _json_error_envelope("collaboration", exc)
        return f"Error: {exc.message}"
    except Exception as exc:
        if response_mode == "json":
            return _json_error_envelope("collaboration", MCPError(str(exc)))
        return f"Error: {exc}"
