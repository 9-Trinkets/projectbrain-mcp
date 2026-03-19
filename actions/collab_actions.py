from __future__ import annotations

from typing import Any, Optional


async def collaboration_action_list_team_members(*, api_get: Any, **_: Any) -> str:
    members = await api_get("/api/teams/members")
    lines = [f"# Team Members ({len(members)})"]
    for member in members:
        lines.append(f"  {member['user_type'].upper()} {member['name']} <{member['email']}> (ID: {member['id']})")
    return "\n".join(lines)


async def collaboration_action_discover_agents(*, api_get: Any, format_timestamp: Any, **_: Any) -> str:
    import time as _time
    agents = await api_get("/api/a2a/agents")
    if not agents:
        return "No agents found on your team."

    # Fetch presence; degrade gracefully if unavailable
    online_ids: set[str] = set()
    last_seen_map: dict[str, str] = {}
    try:
        presence_data = await api_get("/api/stream/presence")
        online_ids = {p["user_id"] for p in presence_data.get("online", [])}
        for entry in presence_data.get("last_seen", []):
            if entry.get("last_seen"):
                last_seen_map[entry["user_id"]] = format_timestamp(entry["last_seen"])
    except Exception:
        pass

    lines = [f"# Agents on your team ({len(agents)})"]
    for agent in agents:
        is_online = agent["id"] in online_ids
        if is_online:
            status_str = "online"
        else:
            ls = last_seen_map.get(agent["id"])
            status_str = f"offline, last seen {ls}" if ls else "offline"
        lines.append(f"\n## {agent['name']} [{status_str}] (ID: {agent['id']})")
        lines.append(f"  Email: {agent['email']}")
        if agent.get("description"):
            lines.append(f"  Description: {agent['description']}")
    return "\n".join(lines)


async def collaboration_action_send_message(
    *,
    api_post: Any,
    require_fields: Any,
    preview: Any,
    recipient_id: Optional[str] = None,
    body: Optional[str] = None,
    message_type: str = "info",
    subject: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("send_message", recipient_id=recipient_id, body=body)
    if error:
        return error
    payload = {"recipient_id": recipient_id, "message_type": message_type, "subject": subject, "body": body}
    payload = {key: value for key, value in payload.items() if value is not None}
    message = await api_post("/api/a2a/messages", body=payload)
    sender_name = message.get("sender_name") or "you"
    recipient_name = message.get("recipient_name") or recipient_id
    subject_line = f"Subject: {subject}\n" if subject else ""
    return (
        f"Message sent to {recipient_name} [{message['message_type']}].\n"
        f"From: {sender_name}\n"
        f"{subject_line}"
        f"{preview(message['body'], 200)}"
    )


async def collaboration_action_get_messages(
    *,
    api_get: Any,
    api_patch: Any,
    format_timestamp: Any,
    include_read: bool = False,
    mark_as_read: bool = False,
    **_: Any,
) -> str:
    messages = await api_get("/api/a2a/messages", params={"unread_only": not include_read})
    if not messages:
        return "No unread messages." if not include_read else "No messages."
    marked_count = 0
    if mark_as_read:
        unread_items = [item for item in messages if not item.get("read")]
        for item in unread_items:
            try:
                await api_patch(f"/api/a2a/messages/{item['id']}/read")
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
        lines.append(f"  From: {sender_name}  |  ID: {item['id']}  |  {format_timestamp(item.get('created_at'))}")
        lines.append(f"  {item['body']}")
    if mark_as_read and marked_count > 0:
        lines.append(f"\n(Marked {marked_count} message(s) as read)")
    return "\n".join(lines)


async def collaboration_action_update_my_card(
    *,
    api_patch: Any,
    description: Optional[str] = None,
    **_: Any,
) -> str:
    if description is None:
        return "Error: action 'update_my_card' requires description."
    member = await api_patch("/api/auth/me/card", body={"description": description})
    parts = []
    if member.get("description"):
        parts.append(f"Description: {member['description']}")
    return f"Agent card updated for {member['name']}.\n" + "\n".join(parts)


async def collaboration_action_join_team(
    *,
    api_post: Any,
    require_fields: Any,
    invite_code: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("join_team", invite_code=invite_code)
    if error:
        return error
    member = await api_post("/api/teams/join", body={"invite_code": invite_code})
    return f"Successfully joined team (ID: {member['team_id']}). Re-authenticate to refresh team claims."


async def collaboration_action_get_agent_activity(
    *,
    api_get: Any,
    format_timestamp: Any,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 20,
    **_: Any,
) -> str:
    params = {"limit": limit}
    if agent_id:
        params["actor_id"] = agent_id
    if project_id:
        params["project_id"] = project_id
    if since:
        params["since"] = since

    res = await api_get("/api/activity", params=params)
    items = res.get("items", [])

    if not items:
        return "No recent activity found for the specified criteria."

    actor_name = items[0].get("actor_name") or agent_id or "Agent"
    lines = [f"# Recent Activity: {actor_name}"]
    for item in items:
        ts = format_timestamp(item.get("created_at"))
        entity = f"{item['entity_type']} '{item['entity_title']}'" if item.get("entity_title") else item["entity_type"]
        lines.append(f"  - [{ts}] {item['action']} on {entity}")

    if res.get("has_more"):
        lines.append("\n(More activity available. Use a newer 'since' or pagination.)")

    return "\n".join(lines)


COLLABORATION_ACTION_HANDLERS = {
    "list_team_members": collaboration_action_list_team_members,
    "discover_agents": collaboration_action_discover_agents,
    "get_agent_activity": collaboration_action_get_agent_activity,
    "send_message": collaboration_action_send_message,
    "get_messages": collaboration_action_get_messages,
    "update_my_card": collaboration_action_update_my_card,
    "join_team": collaboration_action_join_team,
}
