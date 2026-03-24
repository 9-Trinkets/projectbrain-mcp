from __future__ import annotations

from typing import Any, Optional

from envelope import parse as _parse_envelope


async def collaboration_action_list_team_members(
    *,
    api_get: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    response_mode: str = "human",
    **_: Any,
) -> str:
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    members = await api_get("/api/teams/members")
    if response_mode == "json":
        return json_envelope("collaboration.list_team_members", data={"members": members})
    lines = [f"# Team Members ({len(members)})"]
    for member in members:
        lines.append(f"  {member['user_type'].upper()} {member['name']} <{member['email']}> (ID: {member['id']})")
    human = "\n".join(lines)
    if response_mode == "both":
        env = json_envelope("collaboration.list_team_members", data={"members": members})
        return f"{human}\n\n---\n{env}"
    return human


async def collaboration_action_discover_agents(*, api_get: Any, format_timestamp: Any, validate_response_mode: Any, json_envelope: Any, response_mode: str = "human", **_: Any) -> str:
    import time as _time
    agents = await api_get("/api/a2a/agents")
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    if not agents:
        if response_mode == "json":
            return json_envelope("collaboration.discover_agents", data={"agents": []})
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

    if response_mode == "json":
        agents_data = [
            {**agent, "online": agent["id"] in online_ids}
            for agent in agents
        ]
        return json_envelope("collaboration.discover_agents", data={"agents": agents_data})

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
    human = "\n".join(lines)
    if response_mode == "both":
        agents_data = [{**agent, "online": agent["id"] in online_ids} for agent in agents]
        env = json_envelope("collaboration.discover_agents", data={"agents": agents_data})
        return f"{human}\n\n---\n{env}"
    return human


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
    env = _parse_envelope(message.get("body") or "")
    return (
        f"Message sent to {recipient_name} [{message['message_type']}].\n"
        f"From: {sender_name}\n"
        f"{subject_line}"
        f"{preview(env.display_text, 200)}"
    )


async def collaboration_action_get_messages(
    *,
    api_get: Any,
    api_patch: Any,
    format_timestamp: Any,
    validate_response_mode: Any,
    json_envelope: Any,
    include_read: bool = False,
    mark_as_read: bool = False,
    response_mode: str = "human",
    **_: Any,
) -> str:
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    messages = await api_get("/api/a2a/messages", params={"unread_only": not include_read})
    if not messages:
        if response_mode == "json":
            return json_envelope("collaboration.get_messages", data={"messages": [], "marked_as_read": 0})
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
    if response_mode == "json":
        return json_envelope(
            "collaboration.get_messages",
            data={"messages": messages, "marked_as_read": marked_count},
        )
    label = "Recent messages" if include_read else "Unread messages"
    lines = [f"# {label} ({len(messages)})"]
    for item in messages:
        subject_str = f" — {item['subject']}" if item.get("subject") else ""
        read_str = " [read]" if item.get("read") else ""
        sender_name = item.get("sender_name") or item.get("sender_id")
        lines.append(f"\n## [{item['message_type']}]{subject_str}{read_str}")
        lines.append(f"  From: {sender_name}  |  ID: {item['id']}  |  {format_timestamp(item.get('created_at'))}")
        env = _parse_envelope(item.get("body") or "")
        lines.append(f"  {env.display_text}")
        if env.preamble:
            for lbl, tokens in env.preamble.items():
                lines.append(f"  [{lbl}: {' '.join(tokens)}]")
    if mark_as_read and marked_count > 0:
        lines.append(f"\n(Marked {marked_count} message(s) as read)")
    human = "\n".join(lines)
    if response_mode == "both":
        env_json = json_envelope("collaboration.get_messages", data={"messages": messages, "marked_as_read": marked_count})
        return f"{human}\n\n---\n{env_json}"
    return human


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
    validate_response_mode: Any,
    json_envelope: Any,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 20,
    response_mode: str = "human",
    **_: Any,
) -> str:
    mode_error = validate_response_mode(response_mode)
    if mode_error:
        return mode_error
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
        if response_mode == "json":
            return json_envelope("collaboration.get_agent_activity", data={"items": [], "has_more": False})
        return "No recent activity found for the specified criteria."

    if response_mode == "json":
        return json_envelope(
            "collaboration.get_agent_activity",
            data={"items": items, "has_more": bool(res.get("has_more"))},
        )

    actor_name = items[0].get("actor_name") or agent_id or "Agent"
    lines = [f"# Recent Activity: {actor_name}"]
    for item in items:
        ts = format_timestamp(item.get("created_at"))
        entity = f"{item['entity_type']} '{item['entity_title']}'" if item.get("entity_title") else item["entity_type"]
        lines.append(f"  - [{ts}] {item['action']} on {entity}")

    if res.get("has_more"):
        lines.append("\n(More activity available. Use a newer 'since' or pagination.)")

    human = "\n".join(lines)
    if response_mode == "both":
        env = json_envelope(
            "collaboration.get_agent_activity",
            data={"items": items, "has_more": bool(res.get("has_more"))},
        )
        return f"{human}\n\n---\n{env}"
    return human


COLLABORATION_ACTION_HANDLERS = {
    "list_team_members": collaboration_action_list_team_members,
    "discover_agents": collaboration_action_discover_agents,
    "get_agent_activity": collaboration_action_get_agent_activity,
    "send_message": collaboration_action_send_message,
    "get_messages": collaboration_action_get_messages,
    "update_my_card": collaboration_action_update_my_card,
    "join_team": collaboration_action_join_team,
}
