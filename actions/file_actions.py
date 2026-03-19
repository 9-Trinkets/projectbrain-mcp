from __future__ import annotations

from typing import Any, Optional


async def file_action_list(
    *,
    api_get: Any,
    require_fields: Any,
    project_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    file_type: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("list", project_id=project_id)
    if error:
        return error
    params: dict[str, Any] = {}
    if entity_type:
        params["entity_type"] = entity_type
    if entity_id:
        params["entity_id"] = entity_id
    if file_type:
        params["type"] = file_type
    items = await api_get(f"/api/projects/{project_id}/files", params=params or None)
    if not items:
        return "No files found."
    lines = [
        f"- [{f['type']}] v{f.get('latest_version') or '?'} {f['title']} (ID: {f['id']})"
        for f in items
    ]
    return f"Files ({len(items)}):\n" + "\n".join(lines)


async def file_action_get(
    *,
    api_get: Any,
    require_fields: Any,
    file_id: Optional[str] = None,
    version: Optional[int] = None,
    **_: Any,
) -> str:
    error = require_fields("get", file_id=file_id)
    if error:
        return error
    params = {"version": version} if version is not None else None
    file = await api_get(f"/api/files/{file_id}", params=params)
    lines = [
        f"# {file['title']}",
        f"Type: {file['type']}",
        f"ID: {file['id']}",
        f"Project: {file['project_id']}",
        f"Version: {file.get('latest_version') or 'none'}",
    ]
    if file.get("entity_type"):
        lines.append(f"Entity: {file['entity_type']} / {file.get('entity_id')}")
    if file.get("body") is not None:
        lines.append(f"\n{file['body']}")
    return "\n".join(lines)


async def file_action_create(
    *,
    api_post: Any,
    require_fields: Any,
    project_id: Optional[str] = None,
    file_type: Optional[str] = None,
    title: Optional[str] = None,
    body: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("create", project_id=project_id, file_type=file_type, title=title, body=body)
    if error:
        return error
    payload: dict[str, Any] = {"type": file_type, "title": title, "body": body}
    if entity_type is not None:
        payload["entity_type"] = entity_type
    if entity_id is not None:
        payload["entity_id"] = entity_id
    # project_id is already resolved at the tool entry point in mcp/server.py
    file = await api_post(f"/api/projects/{str(project_id)}/files", body=payload)
    return f"File created: [{file['type']}] {file['title']} v{file.get('latest_version', 1)} (ID: {file['id']})"


async def file_action_add_version(
    *,
    api_post: Any,
    require_fields: Any,
    file_id: Optional[str] = None,
    body: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("add_version", file_id=file_id, body=body)
    if error:
        return error
    version = await api_post(f"/api/files/{file_id}/versions", body={"body": body})
    return f"Version {version['version']} added to file {file_id} (version ID: {version['id']})"


async def file_action_list_versions(
    *,
    api_get: Any,
    require_fields: Any,
    file_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("list_versions", file_id=file_id)
    if error:
        return error
    versions = await api_get(f"/api/files/{file_id}/versions")
    if not versions:
        return "No versions found."
    lines = [
        f"- v{v['version']} by {v.get('created_by') or 'unknown'} at {v.get('created_at') or '?'} (ID: {v['id']})"
        for v in versions
    ]
    return f"Versions ({len(versions)}):\n" + "\n".join(lines)


async def file_action_delete(
    *,
    api_delete: Any,
    require_fields: Any,
    file_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("delete", file_id=file_id)
    if error:
        return error
    await api_delete(f"/api/files/{file_id}")
    return f"File deleted (ID: {file_id})"


FILE_ACTION_HANDLERS = {
    "list": file_action_list,
    "get": file_action_get,
    "create": file_action_create,
    "add_version": file_action_add_version,
    "list_versions": file_action_list_versions,
    "delete": file_action_delete,
}
