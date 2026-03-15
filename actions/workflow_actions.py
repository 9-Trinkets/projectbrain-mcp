from __future__ import annotations

from typing import Any, Optional


def _format_workflow(workflow: dict[str, Any]) -> str:
    stages = workflow.get("stages", [])
    if not stages:
        return f"Workflow ID: {workflow['id']} — no stages defined."
    lines = [f"Workflow ID: {workflow['id']} ({len(stages)} stages):"]
    for stage in stages:
        role_str = f" [role: {stage['role_constraint']}]" if stage.get("role_constraint") else ""
        lines.append(f"  {stage['position']}. {stage['name']}{role_str} (ID: {stage['id']})")
    return "\n".join(lines)


async def projects_action_get_workflow(
    *,
    api_get: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    project_id: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("get_workflow", project_id=project_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    workflow = await api_get(f"/api/projects/{project_id}/workflow")
    human_text = _format_workflow(workflow)
    envelope = json_envelope(
        tool="projects.get_workflow",
        data={"item": workflow},
        query={"project_id": project_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def projects_action_add_workflow_stage(
    *,
    api_post: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    project_id: Optional[str] = None,
    stage_name: Optional[str] = None,
    role_constraint: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("add_workflow_stage", project_id=project_id, stage_name=stage_name)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    payload: dict[str, Any] = {"name": stage_name}
    if role_constraint is not None:
        payload["role_constraint"] = role_constraint
    stage = await api_post(f"/api/projects/{project_id}/workflow/stages", body=payload)
    role_str = f" [role: {stage['role_constraint']}]" if stage.get("role_constraint") else ""
    human_text = f"Stage added: {stage['name']}{role_str} at position {stage['position']} (ID: {stage['id']})"
    envelope = json_envelope(
        tool="projects.add_workflow_stage",
        data={"item": stage},
        query={"project_id": project_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def projects_action_update_workflow_stage(
    *,
    api_patch: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    stage_id: Optional[str] = None,
    stage_name: Optional[str] = None,
    role_constraint: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("update_workflow_stage", stage_id=stage_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    payload: dict[str, Any] = {}
    if stage_name is not None:
        payload["name"] = stage_name
    if role_constraint is not None:
        payload["role_constraint"] = role_constraint
    if not payload:
        return "Error: action 'update_workflow_stage' requires at least one of: stage_name, role_constraint."
    stage = await api_patch(f"/api/workflow-stages/{stage_id}", body=payload)
    role_str = f" [role: {stage['role_constraint']}]" if stage.get("role_constraint") else ""
    human_text = f"Stage updated: {stage['name']}{role_str} (ID: {stage['id']})"
    envelope = json_envelope(
        tool="projects.update_workflow_stage",
        data={"item": stage},
        query={"stage_id": stage_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def projects_action_delete_workflow_stage(
    *,
    api_get: Any,
    api_delete: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    stage_id: Optional[str] = None,
    migrate_to_stage_id: Optional[str] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("delete_workflow_stage", stage_id=stage_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    stage_name = ""
    try:
        existing = await api_get(f"/api/workflow-stages/{stage_id}")
        stage_name = existing.get("name", "")
    except Exception:
        pass
    params: dict[str, Any] = {}
    if migrate_to_stage_id is not None:
        params["migrate_to_stage_id"] = migrate_to_stage_id
    await api_delete(f"/api/workflow-stages/{stage_id}", params=params if params else None)
    human_text = (
        f"Stage deleted: '{stage_name}' (ID: {stage_id})"
        if stage_name
        else f"Stage deleted (ID: {stage_id})"
    )
    if migrate_to_stage_id:
        human_text += f"; tasks migrated to stage {migrate_to_stage_id}"
    envelope = json_envelope(
        tool="projects.delete_workflow_stage",
        data={"deleted": True, "stage_id": stage_id},
        query={"stage_id": stage_id},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


async def projects_action_reorder_workflow_stages(
    *,
    api_post: Any,
    require_fields: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    project_id: Optional[str] = None,
    stage_ids: Optional[list[str]] = None,
    response_mode: str = "human",
    **_: Any,
) -> str:
    error = require_fields("reorder_workflow_stages", project_id=project_id)
    if error:
        return error
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    if not stage_ids:
        return "Error: action 'reorder_workflow_stages' requires non-empty stage_ids."
    normalized_ids = [sid.strip() for sid in stage_ids if sid and sid.strip()]
    if not normalized_ids:
        return "Error: action 'reorder_workflow_stages' requires non-empty stage_ids."
    workflow = await api_post(
        f"/api/projects/{project_id}/workflow/stages/reorder",
        body={"stage_ids": normalized_ids},
    )
    human_text = _format_workflow(workflow)
    envelope = json_envelope(
        tool="projects.reorder_workflow_stages",
        data={"item": workflow},
        query={"project_id": project_id, "stage_ids": normalized_ids},
    )
    if response_mode == "json":
        return envelope
    if response_mode == "both":
        return f"{human_text}\n\n---\n{envelope}"
    return human_text


PROJECTS_WORKFLOW_ACTION_HANDLERS = {
    "get_workflow": projects_action_get_workflow,
    "add_workflow_stage": projects_action_add_workflow_stage,
    "update_workflow_stage": projects_action_update_workflow_stage,
    "delete_workflow_stage": projects_action_delete_workflow_stage,
    "reorder_workflow_stages": projects_action_reorder_workflow_stages,
}
