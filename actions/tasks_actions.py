from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


async def tasks_action_list(
    *,
    api_get: Any,
    require_fields: Any,
    validate_response_mode: Any,
    normalize_terms: Any,
    json_envelope: Any,
    task_to_dict: Any,
    valid_task_statuses: set[str],
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    milestone_id: Optional[str] = None,
    q: Optional[str] = None,
    q_any: Optional[list[str]] = None,
    q_all: Optional[list[str]] = None,
    q_not: Optional[list[str]] = None,
    cursor: Optional[str] = None,
    limit: Optional[int] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("list", project_id=project_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    if status and status not in valid_task_statuses:
        return f"Error: Invalid status. Must be one of: {sorted(valid_task_statuses)}"

    any_terms = normalize_terms(q_any)
    all_terms = normalize_terms(q_all)
    not_terms = normalize_terms(q_not)
    result = await api_get(
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

    envelope = json_envelope(
        tool="tasks.list",
        data={
            "items": [task_to_dict(item) for item in task_items],
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


async def tasks_action_create(
    *,
    api_post: Any,
    require_fields: Any,
    valid_task_statuses: set[str],
    project_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    estimate: Optional[int] = None,
    sort_order: Optional[int] = None,
    milestone_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("create", project_id=project_id, title=title)
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
    if payload["status"] not in valid_task_statuses:
        return f"Error: Invalid status. Must be one of: {sorted(valid_task_statuses)}"
    payload = {key: value for key, value in payload.items() if value is not None}
    item = await api_post(f"/api/projects/{project_id}/tasks", body=payload)
    return f"Task created: {item['title']} [{item['status']}] (ID: {item['id']})"


async def tasks_action_update(
    *,
    api_patch: Any,
    require_fields: Any,
    valid_task_statuses: set[str],
    task_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    estimate: Optional[int] = None,
    sort_order: Optional[int] = None,
    milestone_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("update", task_id=task_id)
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
    if "status" in payload and payload["status"] not in valid_task_statuses:
        return f"Error: Invalid status. Must be one of: {sorted(valid_task_statuses)}"
    if not payload:
        return "Error: action 'update' requires at least one mutable field."
    item = await api_patch(f"/api/tasks/{task_id}", body=payload)
    return f"Task updated: {item['title']} [{item['status']}] (ID: {item['id']})"


async def tasks_action_delete(
    *,
    api_get: Any,
    api_delete: Any,
    require_fields: Any,
    task_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("delete", task_id=task_id)
    if error:
        return error
    task_title = ""
    try:
        task = await api_get(f"/api/tasks/{task_id}")
        task_title = task.get("title", "")
    except Exception:
        pass
    await api_delete(f"/api/tasks/{task_id}")
    if task_title:
        return f"Task deleted: '{task_title}' (ID: {task_id})"
    return f"Task deleted (ID: {task_id})"


async def tasks_action_context(
    *,
    api_get: Any,
    require_fields: Any,
    task_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("context", task_id=task_id)
    if error:
        return error
    context_payload = await api_get(f"/api/tasks/{task_id}/context")
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


async def tasks_action_batch_create(
    *,
    api_post: Any,
    require_fields: Any,
    valid_task_statuses: set[str],
    project_id: Optional[str] = None,
    items: Optional[list[dict[str, Any]]] = None,
    **_: Any,
) -> str:
    error = require_fields("batch_create", project_id=project_id)
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
        if item_status not in valid_task_statuses:
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
            created_item = await api_post(f"/api/projects/{project_id}/tasks", body=payload)
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


async def tasks_action_batch_update(
    *,
    api_patch: Any,
    valid_task_statuses: set[str],
    updates: Optional[list[TaskBatchUpdateItem]] = None,
    **_: Any,
) -> str:
    if not updates:
        return "Error: action 'batch_update' requires non-empty updates."
    normalized_updates: list[dict[str, Any]] = []
    for item in updates:
        normalized = item.model_dump(mode="json", exclude_unset=True)
        if "id" not in normalized:
            return "Error: Each updates item must include 'id'."
        if "task_id" in normalized:
            return "Error: Each updates item must include updates[].id."
        if normalized.get("status") and normalized["status"] not in valid_task_statuses:
            return f"Error: Invalid status '{normalized['status']}' for task {normalized.get('id', '(unknown)')}."
        if normalized.get("milestone_id") == "":
            normalized["milestone_id"] = None
        if normalized.get("assignee_id") == "":
            normalized["assignee_id"] = None
        normalized_updates.append(normalized)
    updated_items = await api_patch("/api/tasks/batch", body={"updates": normalized_updates})
    lines = [f"- {item['title']} [{item['status']}] (ID: {item['id']})" for item in updated_items]
    return f"Updated {len(updated_items)} tasks:\n" + "\n".join(lines)


async def tasks_action_add_dependency(
    *,
    api_get: Any,
    api_post: Any,
    require_fields: Any,
    task_id: Optional[str] = None,
    depends_on_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("add_dependency", task_id=task_id, depends_on_id=depends_on_id)
    if error:
        return error
    task = await api_get(f"/api/tasks/{task_id}")
    dependency = await api_get(f"/api/tasks/{depends_on_id}")
    await api_post(f"/api/tasks/{task_id}/dependencies", body={"depends_on_id": depends_on_id})
    return f"Dependency added: '{task['title']}' is now blocked by '{dependency['title']}'."


async def tasks_action_remove_dependency(
    *,
    api_get: Any,
    api_delete: Any,
    require_fields: Any,
    task_id: Optional[str] = None,
    depends_on_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("remove_dependency", task_id=task_id, depends_on_id=depends_on_id)
    if error:
        return error
    task_title = ""
    try:
        task = await api_get(f"/api/tasks/{task_id}")
        task_title = task.get("title", "")
    except Exception:
        pass
    await api_delete(f"/api/tasks/{task_id}/dependencies/{depends_on_id}")
    if task_title:
        return f"Dependency removed from task '{task_title}'."
    return f"Dependency removed from task {task_id}."


async def tasks_action_list_dependencies(
    *,
    api_get: Any,
    require_fields: Any,
    task_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("list_dependencies", task_id=task_id)
    if error:
        return error
    task = await api_get(f"/api/tasks/{task_id}")
    dependencies = await api_get(f"/api/tasks/{task_id}/dependencies")
    if not dependencies:
        return f"Task '{task['title']}' has no dependencies."
    lines = [f"'{task['title']}' is blocked by:"]
    for item in dependencies:
        lines.append(f"  - [{item['status']}] {item['title']} (ID: {item['id']})")
    return "\n".join(lines)


async def tasks_action_add_comment(
    *,
    api_get: Any,
    api_post: Any,
    require_fields: Any,
    task_id: Optional[str] = None,
    comment_body: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("add_comment", task_id=task_id, comment_body=comment_body)
    if error:
        return error
    task_title = ""
    try:
        task = await api_get(f"/api/tasks/{task_id}")
        task_title = task.get("title", "")
    except Exception:
        pass
    comment = await api_post(f"/api/tasks/{task_id}/comments", body={"body": comment_body})
    if task_title:
        return f"Comment added to '{task_title}' (comment ID: {comment['id']})"
    return f"Comment added (comment ID: {comment['id']})"


async def tasks_action_list_comments(
    *,
    api_get: Any,
    require_fields: Any,
    format_timestamp: Any,
    task_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("list_comments", task_id=task_id)
    if error:
        return error
    task_title = "task"
    try:
        task = await api_get(f"/api/tasks/{task_id}")
        task_title = task.get("title") or task_title
    except Exception:
        pass
    comments = await api_get(f"/api/tasks/{task_id}/comments")
    if not comments:
        return f"No comments on '{task_title}'."
    lines = [f"# Comments on '{task_title}' ({len(comments)})"]
    for comment in comments:
        author_name = comment.get("author_name") or comment.get("author_id")
        lines.append(f"\n**{author_name}** — {format_timestamp(comment.get('created_at'))} (ID: {comment['id']})")
        lines.append(comment["body"])
    return "\n".join(lines)


TASKS_CORE_ACTION_HANDLERS = {
    "list": tasks_action_list,
    "create": tasks_action_create,
    "update": tasks_action_update,
    "delete": tasks_action_delete,
    "context": tasks_action_context,
    "batch_create": tasks_action_batch_create,
    "batch_update": tasks_action_batch_update,
}


TASKS_RELATIONSHIP_ACTION_HANDLERS = {
    "add_dependency": tasks_action_add_dependency,
    "remove_dependency": tasks_action_remove_dependency,
    "list_dependencies": tasks_action_list_dependencies,
    "add_comment": tasks_action_add_comment,
    "list_comments": tasks_action_list_comments,
}
