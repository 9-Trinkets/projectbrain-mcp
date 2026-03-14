from __future__ import annotations

from typing import Any, Optional


async def tasks_action_list_milestones(
    *,
    api_get: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    milestone_to_dict: Any,
    project_id: Optional[str] = None,
    q: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("list_milestones", project_id=project_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    milestone_items = await api_get(
        f"/api/projects/{project_id}/milestones",
        params={"q": q},
    )
    if not milestone_items and response_mode == "human":
        return "No milestones found."
    human_lines = []
    for item in milestone_items:
        due_str = f" (due {item['due_date']})" if item.get("due_date") else ""
        human_lines.append(f"- [{item['status']}] {item['title']}{due_str} (ID: {item['id']})")
    human_text = (
        f"Milestones ({len(milestone_items)}):\n" + "\n".join(human_lines)
        if milestone_items
        else "No milestones found."
    )
    envelope = json_envelope(
        tool="tasks.list_milestones",
        data={"items": [milestone_to_dict(item) for item in milestone_items]},
        query={"project_id": project_id, "q": q},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def tasks_action_get_milestone(
    *,
    api_get: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    milestone_to_dict: Any,
    milestone_id: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("get_milestone", milestone_id=milestone_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    item = await api_get(f"/api/milestones/{milestone_id}")
    due_str = item.get("due_date") or "(none)"
    description_str = item.get("description") or "(none)"
    human_text = (
        f"# Milestone: {item['title']}\n"
        f"ID: {item['id']}\n"
        f"Project: {item['project_id']}\n"
        f"Status: {item['status']}\n"
        f"Due date: {due_str}\n"
        f"Position: {item.get('position')}\n"
        f"\nDescription:\n{description_str}"
    )
    envelope = json_envelope(
        tool="tasks.get_milestone",
        data={"item": milestone_to_dict(item)},
        query={"milestone_id": milestone_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def tasks_action_create_milestone(
    *,
    api_post: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    milestone_to_dict: Any,
    valid_milestone_statuses: set[str],
    project_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    due_date: Optional[str] = None,
    status: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("create_milestone", project_id=project_id, title=title)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    milestone_status = status or "planned"
    if milestone_status not in valid_milestone_statuses:
        return f"Error: Invalid status. Must be one of: {sorted(valid_milestone_statuses)}"
    payload: dict[str, Any] = {
        "title": title,
        "status": milestone_status,
    }
    if description is not None:
        payload["description"] = description
    if due_date is not None:
        payload["due_date"] = None if due_date == "" else due_date
    item = await api_post(f"/api/projects/{project_id}/milestones", body=payload)
    due_str = f" due:{item['due_date']}" if item.get("due_date") else ""
    human_text = f"Milestone created: {item['title']} [{item['status']}] (ID: {item['id']}){due_str}"
    envelope = json_envelope(
        tool="tasks.create_milestone",
        data={"item": milestone_to_dict(item)},
        query={"project_id": project_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def tasks_action_update_milestone(
    *,
    api_patch: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    milestone_to_dict: Any,
    valid_milestone_statuses: set[str],
    milestone_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    due_date: Optional[str] = None,
    status: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("update_milestone", milestone_id=milestone_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if due_date is not None:
        payload["due_date"] = None if due_date == "" else due_date
    if status is not None:
        if status not in valid_milestone_statuses:
            return f"Error: Invalid status. Must be one of: {sorted(valid_milestone_statuses)}"
        payload["status"] = status
    if not payload:
        return "Error: action 'update_milestone' requires at least one mutable field."
    item = await api_patch(f"/api/milestones/{milestone_id}", body=payload)
    human_text = f"Milestone updated: {item['title']} [{item['status']}] (ID: {item['id']})"
    envelope = json_envelope(
        tool="tasks.update_milestone",
        data={"item": milestone_to_dict(item)},
        query={"milestone_id": milestone_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def tasks_action_delete_milestone(
    *,
    api_get: Any,
    api_delete: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    milestone_id: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("delete_milestone", milestone_id=milestone_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    milestone_title = ""
    try:
        existing_milestone = await api_get(f"/api/milestones/{milestone_id}")
        milestone_title = existing_milestone.get("title", "")
    except Exception:
        pass
    await api_delete(f"/api/milestones/{milestone_id}")
    human_text = (
        f"Milestone deleted: '{milestone_title}' (ID: {milestone_id})"
        if milestone_title
        else f"Milestone deleted (ID: {milestone_id})"
    )
    envelope = json_envelope(
        tool="tasks.delete_milestone",
        data={"deleted": True, "milestone_id": milestone_id},
        query={"milestone_id": milestone_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def tasks_action_reorder_milestones(
    *,
    api_post: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    project_id: Optional[str] = None,
    milestone_ids: Optional[list[str]] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("reorder_milestones", project_id=project_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    if not milestone_ids:
        return "Error: action 'reorder_milestones' requires non-empty milestone_ids."
    normalized_ids = [mid.strip() for mid in milestone_ids if mid and mid.strip()]
    if not normalized_ids:
        return "Error: action 'reorder_milestones' requires non-empty milestone_ids."
    await api_post(
        f"/api/projects/{project_id}/milestones/reorder",
        body={"milestone_ids": normalized_ids},
    )
    human_text = f"Milestones reordered ({len(normalized_ids)} IDs) for project {project_id}."
    envelope = json_envelope(
        tool="tasks.reorder_milestones",
        data={"ok": True, "project_id": project_id, "milestone_ids": normalized_ids},
        query={"project_id": project_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


TASKS_MILESTONE_ACTION_HANDLERS = {
    "list_milestones": tasks_action_list_milestones,
    "get_milestone": tasks_action_get_milestone,
    "create_milestone": tasks_action_create_milestone,
    "update_milestone": tasks_action_update_milestone,
    "delete_milestone": tasks_action_delete_milestone,
    "reorder_milestones": tasks_action_reorder_milestones,
}
