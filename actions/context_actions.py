from __future__ import annotations

from typing import Any, Optional

import httpx

TOOL_SHORTLIST_MAX_LIMIT = 20
TOOL_ACTION_CATALOG: list[dict[str, Any]] = [
    {
        "id": "context.session",
        "call": 'context(action="session", project_id)',
        "description": "Full project orientation and current work state.",
        "keywords": ["start", "orient", "context", "overview", "status", "session", "where am i"],
    },
    {
        "id": "context.summary",
        "call": 'context(action="summary", project_id)',
        "description": "Project-level summary with milestone/task status.",
        "keywords": ["summary", "snapshot", "progress", "health", "dashboard"],
    },
    {
        "id": "context.changes",
        "call": 'context(action="changes", project_id, since)',
        "description": "Grouped recent changes since a timestamp.",
        "keywords": ["changes", "diff", "recent", "what changed", "since"],
    },
    {
        "id": "context.search",
        "call": 'context(action="search", project_id, q, limit?)',
        "description": "Cross-entity search across tasks, decisions, facts, skills.",
        "keywords": ["search", "find", "lookup", "discover", "query"],
    },
    {
        "id": "projects.list",
        "call": 'projects(action="list")',
        "description": "List available projects.",
        "keywords": ["projects", "list projects", "which project", "available projects"],
    },
    {
        "id": "projects.get",
        "call": 'projects(action="get", project_id)',
        "description": "Inspect a specific project.",
        "keywords": ["project details", "inspect project", "project info"],
    },
    {
        "id": "tasks.list",
        "call": 'tasks(action="list", project_id, status?, q?, q_any?, q_all?, q_not?)',
        "description": "List tasks with advanced filters.",
        "keywords": ["tasks", "todo", "backlog", "queue", "work items", "list tasks"],
    },
    {
        "id": "tasks.create",
        "call": 'tasks(action="create", project_id, title, ...)',
        "description": "Create a new task.",
        "keywords": ["create task", "new task", "add task", "todo item"],
    },
    {
        "id": "tasks.update",
        "call": 'tasks(action="update", task_id, ...)',
        "description": "Update task status/fields.",
        "keywords": ["update task", "mark done", "in progress", "blocked", "status"],
    },
    {
        "id": "tasks.context",
        "call": 'tasks(action="context", task_id)',
        "description": "Read task context with linked decisions.",
        "keywords": ["task context", "task details", "history", "why"],
    },
    {
        "id": "tasks.batch_create",
        "call": 'tasks(action="batch_create", project_id, items)',
        "description": "Create many tasks at once.",
        "keywords": ["bulk create", "batch create", "multiple tasks"],
    },
    {
        "id": "tasks.batch_update",
        "call": 'tasks(action="batch_update", updates)',
        "description": "Update many tasks at once.",
        "keywords": ["bulk update", "batch update", "update many tasks"],
    },
    {
        "id": "tasks.list_milestones",
        "call": 'tasks(action="list_milestones", project_id, q?)',
        "description": "List milestones for a project.",
        "keywords": ["milestones", "list milestones", "roadmap"],
    },
    {
        "id": "tasks.create_milestone",
        "call": 'tasks(action="create_milestone", project_id, title, ...)',
        "description": "Create a new milestone.",
        "keywords": ["create milestone", "new milestone", "roadmap milestone"],
    },
    {
        "id": "tasks.update_milestone",
        "call": 'tasks(action="update_milestone", milestone_id, ...)',
        "description": "Update milestone fields/status.",
        "keywords": ["update milestone", "milestone status", "edit milestone"],
    },
    {
        "id": "tasks.reorder_milestones",
        "call": 'tasks(action="reorder_milestones", project_id, milestone_ids)',
        "description": "Reorder milestones.",
        "keywords": ["reorder milestones", "prioritize milestones", "milestone order"],
    },
    {
        "id": "knowledge.list_skills",
        "call": 'knowledge(entity="skill", action="list", project_id?, q?)',
        "description": "Find reusable skills/procedures.",
        "keywords": ["skills", "playbook", "procedure", "how to", "reusable"],
    },
    {
        "id": "knowledge.create_decision",
        "call": 'knowledge(entity="decision", action="create", project_id, title, rationale, ...)',
        "description": "Record tradeoffs and rationale.",
        "keywords": ["decision", "tradeoff", "rationale", "why we chose"],
    },
    {
        "id": "knowledge.create_fact",
        "call": 'knowledge(entity="fact", action="create", project_id, title, body, ...)',
        "description": "Record durable constraints and facts.",
        "keywords": ["fact", "constraint", "rule", "policy", "durable memory"],
    },
    {
        "id": "collaboration.list_team_members",
        "call": 'collaboration(action="list_team_members")',
        "description": "List team members for coordination.",
        "keywords": ["team", "members", "who can help", "people"],
    },
    {
        "id": "collaboration.discover_agents",
        "call": 'collaboration(action="discover_agents")',
        "description": "Discover available agents by role/skills.",
        "keywords": ["agents", "discover agents", "planner", "implementer", "reviewer"],
    },
    {
        "id": "collaboration.send_message",
        "call": 'collaboration(action="send_message", recipient_id, body, ...)',
        "description": "Send an A2A/team message.",
        "keywords": ["message", "delegate", "handoff", "notify", "ask agent"],
    },
]
DEFAULT_TOOL_SHORTLIST_IDS = [
    "context.session",
    "tasks.list",
    "tasks.update",
    "knowledge.list_skills",
    "knowledge.create_decision",
]


def _normalize_text(value: str) -> str:
    normalized = "".join(char if char.isalnum() else " " for char in value.lower())
    return " ".join(normalized.split())


def _intent_tokens(value: str) -> set[str]:
    return {token for token in _normalize_text(value).split() if token}


def _score_keywords(intent_text: str, intent_tokens: set[str], keywords: list[str]) -> tuple[int, list[str]]:
    score = 0
    matched: list[str] = []
    for keyword in keywords:
        normalized_keyword = _normalize_text(keyword)
        if not normalized_keyword:
            continue
        if " " in normalized_keyword:
            if normalized_keyword in intent_text:
                score += 3
                matched.append(keyword)
            else:
                keyword_tokens = [token for token in normalized_keyword.split() if token]
                if keyword_tokens and all(
                    any(
                        intent_token == keyword_token
                        or intent_token.startswith(keyword_token)
                        or keyword_token.startswith(intent_token)
                        for intent_token in intent_tokens
                    )
                    for keyword_token in keyword_tokens
                ):
                    score += 2
                    matched.append(keyword)
            continue
        if normalized_keyword in intent_tokens:
            score += 2
            matched.append(keyword)
            continue
        if any(
            token.startswith(normalized_keyword) or normalized_keyword.startswith(token)
            for token in intent_tokens
        ):
            score += 1
            matched.append(keyword)
    deduped = list(dict.fromkeys(matched))
    return score, deduped


def _shortlist_tool_actions(intent: str, top_k: int, full_tool_mode: bool = False) -> tuple[list[dict[str, Any]], bool]:
    normalized_intent = _normalize_text(intent)
    intent_tokens = _intent_tokens(intent)
    scored: list[tuple[int, int, dict[str, Any], list[str]]] = []
    for index, operation in enumerate(TOOL_ACTION_CATALOG):
        score, matched_keywords = _score_keywords(normalized_intent, intent_tokens, operation["keywords"])
        if score > 0:
            scored.append((score, index, operation, matched_keywords))
    scored.sort(key=lambda item: (-item[0], item[1]))

    fallback_used = False
    if not scored:
        fallback_used = True
        fallback_map = {operation["id"]: operation for operation in TOOL_ACTION_CATALOG}
        scored = [
            (0, index, fallback_map[operation_id], [])
            for index, operation_id in enumerate(DEFAULT_TOOL_SHORTLIST_IDS)
            if operation_id in fallback_map
        ]

    if full_tool_mode:
        existing_ids = {operation["id"] for _, _, operation, _ in scored}
        for index, operation in enumerate(TOOL_ACTION_CATALOG):
            if operation["id"] not in existing_ids:
                scored.append((0, index + len(TOOL_ACTION_CATALOG), operation, []))
        selected = scored
    else:
        effective_limit = max(1, min(top_k, TOOL_SHORTLIST_MAX_LIMIT))
        selected = scored[:effective_limit]
        selected_ids = {operation["id"] for _, _, operation, _ in selected}
        if len(selected) < effective_limit:
            fallback_map = {operation["id"]: operation for operation in TOOL_ACTION_CATALOG}
            for index, operation_id in enumerate(DEFAULT_TOOL_SHORTLIST_IDS):
                operation = fallback_map.get(operation_id)
                if not operation or operation["id"] in selected_ids:
                    continue
                selected.append((0, index + len(TOOL_ACTION_CATALOG), operation, []))
                selected_ids.add(operation["id"])
                if len(selected) == effective_limit:
                    break
        if len(selected) < effective_limit:
            for index, operation in enumerate(TOOL_ACTION_CATALOG):
                if operation["id"] in selected_ids:
                    continue
                selected.append((0, index + (2 * len(TOOL_ACTION_CATALOG)), operation, []))
                selected_ids.add(operation["id"])
                if len(selected) == effective_limit:
                    break

    shortlist = [
        {
            "id": operation["id"],
            "call": operation["call"],
            "description": operation["description"],
            "score": score,
            "matched_keywords": matched_keywords,
        }
        for score, _, operation, matched_keywords in selected
    ]
    return shortlist, fallback_used


async def _fetch_context_session_data(*, api_get: Any, request_timeout_seconds: float, project_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=request_timeout_seconds) as client:
        session = await api_get(f"/api/projects/{project_id}/session-context", client=client)
        facts_page = await api_get(f"/api/projects/{project_id}/facts", params={"limit": 10}, client=client)
        skills_page = await api_get("/api/skills", params={"project_id": project_id, "limit": 10}, client=client)
    return {
        "project": session["project"],
        "in_progress": session.get("in_progress_tasks", []),
        "todo": session.get("todo_tasks", []),
        "decisions": session.get("recent_decisions", []),
        "members": session.get("team_members", []),
        "facts": facts_page.get("items", []),
        "skills": skills_page.get("items", []),
    }


def _render_context_session(data: dict[str, Any], *, preview: Any) -> str:
    project = data["project"]
    in_progress = data["in_progress"]
    todo = data["todo"]
    decisions = data["decisions"]
    members = data["members"]
    facts = data["facts"]
    skills = data["skills"]

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
            lines.append(f"    {preview(item['rationale'], 120)}")

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


async def _fetch_context_summary_data(*, api_get: Any, project_id: str) -> dict[str, Any]:
    summary = await api_get(f"/api/projects/{project_id}/summary")
    return {
        "project": summary["project"],
        "counts": summary.get("task_counts", {}),
        "milestones": summary.get("milestones", []),
    }


def _render_context_summary(data: dict[str, Any]) -> str:
    project = data["project"]
    counts = data["counts"]
    milestones = data["milestones"]
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


async def _fetch_context_changes_data(*, api_get: Any, project_id: str, since: str) -> dict[str, Any]:
    return await api_get(f"/api/projects/{project_id}/changes", params={"since": since})


def _render_context_changes(data: dict[str, Any], *, since: str, format_timestamp: Any) -> str:
    total = int(data.get("total", 0))
    if total == 0:
        return f"No changes since {since}."
    lines = [f"# Changes since {data.get('since', since)} ({total} total)"]
    for group in data.get("groups", []):
        group_items = group.get("changes", [])
        lines.append(f"\n## {group.get('entity_type', 'unknown').title()} ({len(group_items)} changes)")
        for entry in group_items:
            actor = entry.get("actor_name") or "system"
            title = f" '{entry['entity_title']}'" if entry.get("entity_title") else ""
            lines.append(f"  - [{entry['action']}]{title} by {actor} at {format_timestamp(entry.get('created_at'))}")
    if data.get("truncated"):
        lines.append("\n(Showing first 200 changes. Use a newer 'since' to narrow results.)")
    return "\n".join(lines)


async def _fetch_context_search_data(
    *,
    api_get: Any,
    request_timeout_seconds: float,
    project_id: str,
    q: str,
    limit: int,
) -> dict[str, Any]:
    per_entity_limit = max(1, min(limit, 20))
    async with httpx.AsyncClient(timeout=request_timeout_seconds) as client:
        await api_get(f"/api/projects/{project_id}", client=client)
        tasks_page = await api_get(f"/api/projects/{project_id}/tasks", params={"q": q, "limit": per_entity_limit}, client=client)
        decisions_page = await api_get(f"/api/projects/{project_id}/decisions", params={"q": q, "limit": per_entity_limit}, client=client)
        facts_page = await api_get(f"/api/projects/{project_id}/facts", params={"q": q, "limit": per_entity_limit}, client=client)
        skills_page = await api_get("/api/skills", params={"project_id": project_id, "q": q, "limit": per_entity_limit}, client=client)
    return {
        "tasks_items": tasks_page.get("items", []),
        "decisions_items": decisions_page.get("items", []),
        "facts_items": facts_page.get("items", []),
        "skills_items": skills_page.get("items", []),
    }


def _render_context_search(data: dict[str, Any], *, q: str, preview: Any) -> str:
    tasks_items = data["tasks_items"]
    decisions_items = data["decisions_items"]
    facts_items = data["facts_items"]
    skills_items = data["skills_items"]
    total = len(tasks_items) + len(decisions_items) + len(facts_items) + len(skills_items)
    if total == 0:
        return f"No results for '{q}'."

    lines = [f"# Search results for '{q}' ({total} hits)"]
    if tasks_items:
        lines.append(f"\n## Tasks ({len(tasks_items)})")
        for item in tasks_items:
            lines.append(f"  - [{item['status']}] {item['title']} (ID: {item['id']})")
            if item.get("description"):
                lines.append(f"    {preview(item['description'])}")
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


async def context_action_session(
    *,
    api_get: Any,
    require_fields: Any,
    preview: Any,
    request_timeout_seconds: float,
    project_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("session", project_id=project_id)
    if error:
        return error
    data = await _fetch_context_session_data(
        api_get=api_get,
        request_timeout_seconds=request_timeout_seconds,
        project_id=project_id,
    )
    return _render_context_session(data, preview=preview)


async def context_action_summary(
    *,
    api_get: Any,
    require_fields: Any,
    project_id: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("summary", project_id=project_id)
    if error:
        return error
    data = await _fetch_context_summary_data(api_get=api_get, project_id=project_id)
    return _render_context_summary(data)


async def context_action_changes(
    *,
    api_get: Any,
    require_fields: Any,
    format_timestamp: Any,
    project_id: Optional[str] = None,
    since: Optional[str] = None,
    **_: Any,
) -> str:
    error = require_fields("changes", project_id=project_id, since=since)
    if error:
        return error
    data = await _fetch_context_changes_data(api_get=api_get, project_id=project_id, since=since)
    return _render_context_changes(data, since=since, format_timestamp=format_timestamp)


async def context_action_search(
    *,
    api_get: Any,
    require_fields: Any,
    preview: Any,
    request_timeout_seconds: float,
    project_id: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 5,
    **_: Any,
) -> str:
    error = require_fields("search", project_id=project_id, q=q)
    if error:
        return error
    data = await _fetch_context_search_data(
        api_get=api_get,
        request_timeout_seconds=request_timeout_seconds,
        project_id=project_id,
        q=q,
        limit=limit,
    )
    return _render_context_search(data, q=q, preview=preview)


async def context_action_shortlist(
    *,
    require_fields: Any,
    q: Optional[str] = None,
    limit: int = 5,
    full_tool_mode: bool = False,
    **_: Any,
) -> str:
    error = require_fields("shortlist", q=q)
    if error:
        return error
    shortlist, fallback_used = _shortlist_tool_actions(
        intent=q,
        top_k=limit,
        full_tool_mode=full_tool_mode,
    )
    mode_label = "full_tool_mode" if full_tool_mode else "shortlist"
    lines = [
        f"# Tool shortlist for '{q}'",
        f"Mode: {mode_label}",
        f"Returned: {len(shortlist)} operation(s)",
        f"Fallback used: {'yes' if fallback_used else 'no'}",
    ]
    for item in shortlist:
        matched_suffix = ""
        if item["matched_keywords"]:
            matched_suffix = f" | matched: {', '.join(item['matched_keywords'])}"
        lines.append(f"- {item['call']} — {item['description']}{matched_suffix}")
    return "\n".join(lines)


CONTEXT_ACTION_HANDLERS = {
    "session": context_action_session,
    "summary": context_action_summary,
    "changes": context_action_changes,
    "search": context_action_search,
    "shortlist": context_action_shortlist,
}
