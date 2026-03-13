import json
import uuid
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import TransportSecuritySettings
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update

from projectbrain_mcp.runtime import get_runtime

_runtime = get_runtime()
settings = _runtime.settings
async_session = _runtime.async_session
current_team_id = _runtime.current_team_id
current_user_id = _runtime.current_user_id
log_audit = _runtime.log_audit
apply_cursor_pagination = _runtime.apply_cursor_pagination
paginate_results = _runtime.paginate_results

AuditLog = _runtime.models.AuditLog
Decision = _runtime.models.Decision
Fact = _runtime.models.Fact
Milestone = _runtime.models.Milestone
Project = _runtime.models.Project
Skill = _runtime.models.Skill
Task = _runtime.models.Task
TaskDependency = _runtime.models.TaskDependency
TeamInvite = _runtime.models.TeamInvite
User = _runtime.models.User
A2AMessage = _runtime.models.A2AMessage
TaskComment = _runtime.models.TaskComment

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


def _task_text_match_condition(term: str):
    pattern = f"%{term}%"
    return or_(Task.title.ilike(pattern), Task.description.ilike(pattern))


def _validate_response_mode(response_mode: str) -> Optional[str]:
    if response_mode not in VALID_RESPONSE_MODES:
        return f"Error: Invalid response_mode. Must be one of: {sorted(VALID_RESPONSE_MODES)}"
    return None


def _task_to_dict(task: Task) -> dict:
    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "estimate": task.estimate,
        "sort_order": task.sort_order,
        "project_id": str(task.project_id),
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "milestone_id": str(task.milestone_id) if task.milestone_id else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _decision_to_dict(decision: Decision) -> dict:
    return {
        "id": str(decision.id),
        "title": decision.title,
        "rationale": decision.rationale,
        "author_type": decision.author_type,
        "author_id": str(decision.author_id),
        "task_id": str(decision.task_id) if decision.task_id else None,
        "project_id": str(decision.project_id),
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
        "updated_at": decision.updated_at.isoformat() if decision.updated_at else None,
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


@mcp_server.tool(description="Create a new project in your team")
async def create_project(name: str, description: str = "") -> str:
    """Create a new project with the given name and optional description.

    Args:
        name: Project name.
        description: Optional project description.
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated. Provide a valid JWT in the Authorization header."

    async with async_session() as db:
        project = Project(
            id=uuid.uuid4(),
            name=name,
            description=description,
            team_id=uuid.UUID(team_id),
            created_by=uuid.UUID(user_id),
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        return f"Project created: {project.name} (ID: {project.id})"


@mcp_server.tool(description="List all projects in your team")
async def list_projects() -> str:
    """List all projects accessible to the authenticated user."""
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Project)
            .where(Project.team_id == uuid.UUID(team_id))
            .order_by(Project.created_at.desc())
        )
        projects = result.scalars().all()
        if not projects:
            return "No projects found."
        lines = [f"- {p.name}: {p.description or '(no description)'} (ID: {p.id})" for p in projects]
        return "Projects:\n" + "\n".join(lines)


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
    """Create a new task in the specified project.

    Args:
        project_id: UUID of the project
        title: Task title
        description: Task description
        status: One of: todo, in_progress, blocked, done, cancelled
        priority: Optional priority (e.g. high, medium, low)
        estimate: Optional time estimate in hours
        milestone_id: Optional milestone UUID to attach the task to
        assignee_id: Optional assignee UUID
        sort_order: Optional explicit ordering index (>=1)
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."
    if status not in VALID_STATUSES:
        return f"Error: Invalid status. Must be one of: {VALID_STATUSES}"

    async with async_session() as db:
        try:
            project_uuid = uuid.UUID(project_id)
        except ValueError:
            return "Error: Invalid project_id UUID."

        project = await db.get(Project, project_uuid)
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."
        milestone_uuid = None
        if milestone_id is not None:
            try:
                milestone_uuid = uuid.UUID(milestone_id)
            except ValueError:
                return "Error: Invalid milestone_id UUID."
            milestone_result = await db.execute(
                select(Milestone).where(
                    Milestone.id == milestone_uuid,
                    Milestone.project_id == project_uuid,
                )
            )
            if not milestone_result.scalar_one_or_none():
                return "Error: milestone_id does not belong to this project."
        assignee_uuid = None
        if assignee_id is not None:
            try:
                assignee_uuid = uuid.UUID(assignee_id)
            except ValueError:
                return "Error: Invalid assignee_id UUID."
            assignee = await db.get(User, assignee_uuid)
            if not assignee or str(assignee.team_id) != team_id:
                return "Error: assignee_id not found on your team."
        if sort_order is not None and sort_order < 1:
            return "Error: sort_order must be >= 1."
        max_sort_result = await db.execute(
            select(func.max(Task.sort_order)).where(Task.project_id == project_uuid)
        )
        next_sort_order = (max_sort_result.scalar() or 0) + 1

        task = Task(
            id=uuid.uuid4(),
            title=title,
            description=description,
            status=status,
            priority=priority,
            estimate=estimate,
            sort_order=sort_order if sort_order is not None else next_sort_order,
            project_id=project_uuid,
            milestone_id=milestone_uuid,
            assignee_id=assignee_uuid,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return f"Task created: {task.title} [{task.status}] (ID: {task.id})"


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
    """Update one or more fields on a task.

    Args:
        task_id: UUID of the task to update
        title: New title (optional)
        description: New description (optional)
        status: New status - one of: todo, in_progress, blocked, done, cancelled (optional)
        priority: New priority (optional)
        estimate: New estimate in hours (optional)
        sort_order: Explicit order index (optional)
        milestone_id: New milestone UUID. Pass empty string to clear.
        assignee_id: New assignee UUID. Pass empty string to clear.
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        return "Error: Invalid task_id UUID."
    if status and status not in VALID_STATUSES:
        return f"Error: Invalid status. Must be one of: {VALID_STATUSES}"

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(
                Task.id == task_uuid,
                Project.team_id == uuid.UUID(team_id),
            )
        )
        task = result.scalar_one_or_none()
        if not task:
            return "Error: Task not found."
        updates: dict[str, object] = {}
        for field, value in {
            "title": title,
            "description": description,
            "status": status,
            "priority": priority,
            "estimate": estimate,
            "sort_order": sort_order,
        }.items():
            if value is not None:
                updates[field] = value
        if sort_order is not None and sort_order < 1:
            return "Error: sort_order must be >= 1."
        if milestone_id is not None:
            if milestone_id == "":
                updates["milestone_id"] = None
            else:
                try:
                    milestone_uuid = uuid.UUID(milestone_id)
                except ValueError:
                    return "Error: Invalid milestone_id UUID."
                milestone_result = await db.execute(
                    select(Milestone).where(
                        Milestone.id == milestone_uuid,
                        Milestone.project_id == task.project_id,
                    )
                )
                if not milestone_result.scalar_one_or_none():
                    return "Error: milestone_id does not belong to this task's project."
                updates["milestone_id"] = milestone_uuid
        if assignee_id is not None:
            if assignee_id == "":
                updates["assignee_id"] = None
            else:
                try:
                    assignee_uuid = uuid.UUID(assignee_id)
                except ValueError:
                    return "Error: Invalid assignee_id UUID."
                assignee = await db.get(User, assignee_uuid)
                if not assignee or str(assignee.team_id) != team_id:
                    return "Error: assignee_id not found on your team."
                updates["assignee_id"] = assignee_uuid

        for field, value in updates.items():
            setattr(task, field, value)

        await db.commit()
        await db.refresh(task)
        return f"Task updated: {task.title} [{task.status}] (ID: {task.id})"


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
    """List tasks in a project with optional status, milestone, and text search filters.

    Args:
        project_id: UUID of the project
        status: Filter by status (todo, in_progress, blocked, done). Omit for all tasks.
        milestone_id: Filter by milestone UUID. Omit for all milestones.
        q: Search query to filter by title or description (case-insensitive). Omit to list all.
        q_any: OR filter terms; task matches if any term matches title/description.
        q_all: AND filter terms; task must match every term in title/description.
        q_not: Exclusion terms; task is excluded if any term matches title/description.
        cursor: Pagination cursor from a previous response. Omit for first page.
        limit: Max items to return (default 50, max 100).
        response_mode: one of human, json, both.
    """

    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."
    mode_error = _validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    if status and status not in VALID_STATUSES:
        return f"Error: Invalid status. Must be one of: {VALID_STATUSES}"
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        return "Error: Invalid project_id UUID."
    milestone_uuid = None
    if milestone_id:
        try:
            milestone_uuid = uuid.UUID(milestone_id)
        except ValueError:
            return "Error: Invalid milestone_id UUID."
    any_terms = _normalize_terms(q_any)
    all_terms = _normalize_terms(q_all)
    not_terms = _normalize_terms(q_not)

    async with async_session() as db:
        query = select(Task).join(Project).where(
            Task.project_id == project_uuid,
            Project.team_id == uuid.UUID(team_id),
        )
        if status:
            query = query.where(Task.status == status)
        else:
            query = query.where(Task.status != "cancelled")
        if milestone_uuid:
            query = query.where(Task.milestone_id == milestone_uuid)
        if q:
            query = query.where(_task_text_match_condition(q))
        if any_terms:
            query = query.where(or_(*[_task_text_match_condition(term) for term in any_terms]))
        if all_terms:
            for term in all_terms:
                query = query.where(_task_text_match_condition(term))
        if not_terms:
            for term in not_terms:
                query = query.where(~_task_text_match_condition(term))
        query = query.order_by(Task.sort_order.asc().nullslast(), Task.created_at.desc())

        query, effective_limit = apply_cursor_pagination(query, Task, cursor, limit)
        result = await db.execute(query)
        all_items = list(result.scalars().all())
        tasks, next_cursor, has_more = paginate_results(all_items, effective_limit)
    if not tasks and response_mode == "human":
        return "No tasks found."

    human_lines: list[str] = []
    if tasks:
        human_lines = [f"- [{t.status}] {t.title} (ID: {t.id})" for t in tasks]
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



@mcp_server.tool(description="Delete a task and clean up its dependencies")
async def delete_task(task_id: str) -> str:
    """Permanently delete a task. Also removes dependencies and unlinks decisions.

    Args:
        task_id: UUID of the task to delete
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(
                Task.id == uuid.UUID(task_id),
                Project.team_id == uuid.UUID(team_id),
            )
        )
        task = result.scalar_one_or_none()
        if not task:
            return "Error: Task not found."

        title = task.title

        # Clean up FK references
        await db.execute(
            sa_delete(TaskDependency).where(
                (TaskDependency.task_id == uuid.UUID(task_id))
                | (TaskDependency.depends_on_id == uuid.UUID(task_id))
            )
        )
        await db.execute(
            sa_update(Decision).where(Decision.task_id == uuid.UUID(task_id)).values(task_id=None)
        )
        await db.delete(task)
        await db.commit()
        return f"Task deleted: '{title}' (ID: {task_id})"


@mcp_server.tool(description="Join a team using an invite code")
async def join_team(invite_code: str) -> str:
    """Join an existing team using an invite code.

    Args:
        invite_code: The invite code provided by a team member
    """
    user_id = current_user_id.get()
    if not user_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(TeamInvite).where(
                TeamInvite.code == invite_code,
                TeamInvite.used_by.is_(None),
            )
        )
        invite = result.scalar_one_or_none()
        if not invite:
            return "Error: Invalid or already-used invite code."

        user = await db.get(User, uuid.UUID(user_id))
        if not user:
            return "Error: User not found."

        user.team_id = invite.team_id
        invite.used_by = user.id
        await db.commit()
        return f"Successfully joined team (ID: {invite.team_id}). Re-authenticate to get a new token with the updated team."


@mcp_server.tool(description="Update multiple tasks in a single transaction")
async def batch_update_tasks(updates: list[dict]) -> str:
    """Update multiple tasks at once. Each item must include 'id' plus any fields to update.

    Args:
        updates: List of dicts, each with 'id' (task UUID) and optional fields:
                 title, description, status, priority, estimate, sort_order,
                 milestone_id, assignee_id.
                 milestone_id/assignee_id can be null to clear.
                 Example: [{"id": "...", "status": "done"}, {"id": "...", "milestone_id": "..."}]
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."
    if not updates:
        return "Error: No updates provided."

    try:
        task_ids = [uuid.UUID(item["id"]) for item in updates]
    except (KeyError, ValueError) as e:
        return f"Error: Each update must include a valid 'id' field. {e}"

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(
                Task.id.in_(task_ids),
                Project.team_id == uuid.UUID(team_id),
            )
        )
        tasks_by_id = {t.id: t for t in result.scalars().all()}

        missing = [str(tid) for tid in task_ids if tid not in tasks_by_id]
        if missing:
            return f"Error: Tasks not found: {', '.join(missing)}"

        UUID_FIELDS = {"milestone_id", "assignee_id"}
        ALLOWED_FIELDS = {"title", "description", "status", "priority", "estimate"} | UUID_FIELDS
        ALLOWED_FIELDS.add("sort_order")
        for item in updates:
            task = tasks_by_id[uuid.UUID(item["id"])]
            for field in ALLOWED_FIELDS:
                if field not in item:
                    continue
                value = item[field]
                if field == "status" and value not in VALID_STATUSES:
                    return f"Error: Invalid status '{value}' for task {item['id']}."
                if field == "sort_order" and value is not None and value < 1:
                    return f"Error: Invalid sort_order '{value}' for task {item['id']}. Must be >= 1."
                if field == "milestone_id":
                    if value is None:
                        setattr(task, field, None)
                    else:
                        try:
                            milestone_uuid = uuid.UUID(value)
                        except (ValueError, TypeError):
                            return f"Error: Invalid milestone_id '{value}' for task {item['id']}."
                        milestone_result = await db.execute(
                            select(Milestone).where(
                                Milestone.id == milestone_uuid,
                                Milestone.project_id == task.project_id,
                            )
                        )
                        if not milestone_result.scalar_one_or_none():
                            return f"Error: milestone_id '{value}' does not belong to task project for task {item['id']}."
                        setattr(task, field, milestone_uuid)
                    continue
                if field == "assignee_id":
                    if value is None:
                        setattr(task, field, None)
                    else:
                        try:
                            assignee_uuid = uuid.UUID(value)
                        except (ValueError, TypeError):
                            return f"Error: Invalid assignee_id '{value}' for task {item['id']}."
                        assignee = await db.get(User, assignee_uuid)
                        if not assignee or assignee.team_id != uuid.UUID(team_id):
                            return f"Error: assignee_id '{value}' not found on your team for task {item['id']}."
                        setattr(task, field, assignee_uuid)
                    continue
                setattr(task, field, value)

        await db.commit()
        lines = [f"- {tasks_by_id[tid].title} [{tasks_by_id[tid].status}] (ID: {tid})" for tid in task_ids]
        return f"Updated {len(task_ids)} tasks:\n" + "\n".join(lines)


@mcp_server.tool(description="Get a compact project snapshot to orient yourself at session start")
async def get_session_context(project_id: str) -> str:
    """Returns in-progress tasks, todo tasks, recent decisions, team members, and pending messages.

    Args:
        project_id: UUID of the project
    """

    team_id = current_team_id.get()
    user_id = current_user_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."

        in_progress = await db.execute(
            select(Task)
            .where(Task.project_id == uuid.UUID(project_id), Task.status == "in_progress")
            .order_by(Task.sort_order.asc().nullslast(), Task.updated_at.desc())
            .limit(10)
        )
        in_progress_tasks = in_progress.scalars().all()

        todo = await db.execute(
            select(Task)
            .where(Task.project_id == uuid.UUID(project_id), Task.status == "todo")
            .order_by(Task.sort_order.asc().nullslast(), Task.created_at.desc())
            .limit(10)
        )
        todo_tasks = todo.scalars().all()

        decisions = await db.execute(
            select(Decision)
            .where(Decision.project_id == uuid.UUID(project_id))
            .order_by(Decision.created_at.desc())
            .limit(5)
        )
        recent_decisions = decisions.scalars().all()

        members = await db.execute(
            select(User).where(User.team_id == uuid.UUID(team_id)).order_by(User.created_at.asc())
        )
        team_members = members.scalars().all()

        pending_msgs = []
        recent_read_msgs = []
        if user_id:
            msgs_result = await db.execute(
                select(A2AMessage)
                .where(A2AMessage.recipient_id == uuid.UUID(user_id), A2AMessage.read == False)  # noqa: E712
                .order_by(A2AMessage.created_at.asc())
                .limit(10)
            )
            pending_msgs = msgs_result.scalars().all()
            for msg in pending_msgs:
                await db.refresh(msg, ["sender"])
            # Also fetch recent read messages so agents see what the UI shows
            if not pending_msgs:
                read_result = await db.execute(
                    select(A2AMessage)
                    .where(A2AMessage.recipient_id == uuid.UUID(user_id), A2AMessage.read == True)  # noqa: E712
                    .order_by(A2AMessage.created_at.desc())
                    .limit(5)
                )
                recent_read_msgs = read_result.scalars().all()
                for msg in recent_read_msgs:
                    await db.refresh(msg, ["sender"])

    lines = [f"# Project: {project.name}", f"Description: {project.description or '(none)'}"]

    lines.append(f"\n## In-Progress Tasks ({len(in_progress_tasks)})")
    for t in in_progress_tasks:
        lines.append(f"  - {t.title} (ID: {t.id})")

    lines.append(f"\n## Todo Tasks ({len(todo_tasks)})")
    for t in todo_tasks:
        priority = f" [{t.priority}]" if t.priority else ""
        lines.append(f"  - {t.title}{priority} (ID: {t.id})")

    lines.append(f"\n## Recent Decisions ({len(recent_decisions)})")
    for d in recent_decisions:
        lines.append(f"  - {d.title} (by {d.author_type}, ID: {d.id})")
        if d.rationale:
            lines.append(f"    Rationale: {d.rationale[:120]}{'...' if len(d.rationale) > 120 else ''}")

    # Facts — durable project knowledge
    facts_result = await db.execute(
        select(Fact)
        .where(Fact.project_id == uuid.UUID(project_id))
        .order_by(Fact.created_at.desc())
    )
    all_facts = facts_result.scalars().all()
    if all_facts:
        lines.append(f"\n## Project Facts ({len(all_facts)}) — conventions, constraints, context")
        for f in all_facts:
            cat_str = f" [{f.category}]" if f.category else ""
            lines.append(f"  - {f.title}{cat_str}")
            if f.body:
                lines.append(f"    {f.body[:150].replace(chr(10), ' ')}{'...' if len(f.body) > 150 else ''}")

    # Skills — reusable knowledge (project-scoped + team-wide)
    from sqlalchemy import or_
    skills_result = await db.execute(
        select(Skill)
        .where(
            Skill.team_id == uuid.UUID(team_id),
            or_(Skill.project_id == uuid.UUID(project_id), Skill.project_id.is_(None)),
        )
        .order_by(Skill.created_at.desc())
        .limit(10)
    )
    all_skills = skills_result.scalars().all()
    if all_skills:
        lines.append(f"\n## Skills ({len(all_skills)}) — call list_skills() or get_skill(id) for full content")
        for s in all_skills:
            scope = "team-wide" if not s.project_id else "project"
            cat_str = f" [{s.category}]" if s.category else ""
            tags_str = f" tags:{','.join(s.tags)}" if s.tags else ""
            lines.append(f"  - {s.title}{cat_str}{tags_str} ({scope}) (ID: {s.id})")

    lines.append(f"\n## Team Members ({len(team_members)})")
    for m in team_members:
        card_parts = []
        if m.role:
            card_parts.append(f"role:{m.role}")
        if m.skills:
            card_parts.append(f"skills:{','.join(m.skills)}")
        card_str = f"  ({', '.join(card_parts)})" if card_parts else ""
        lines.append(f"  - {m.name} <{m.email}> [{m.user_type}]{card_str} (ID: {m.id})")
        if m.description:
            lines.append(f"    {m.description}")

    if pending_msgs:
        lines.append(f"\n## ⚠ Pending Messages ({len(pending_msgs)}) — call get_pending_messages() to read")
        for msg in pending_msgs:
            sender_name = msg.sender.name if msg.sender else str(msg.sender_id)
            subject_str = f" — {msg.subject}" if msg.subject else ""
            lines.append(f"  - [{msg.message_type}]{subject_str} from {sender_name} (ID: {msg.id})")
    elif recent_read_msgs:
        lines.append(f"\n## Recent Messages ({len(recent_read_msgs)}) — all read. Call get_pending_messages(include_read=true) to review.")
        for msg in recent_read_msgs:
            sender_name = msg.sender.name if msg.sender else str(msg.sender_id)
            subject_str = f" — {msg.subject}" if msg.subject else ""
            lines.append(f"  - [{msg.message_type}]{subject_str} from {sender_name} ({msg.created_at.strftime('%Y-%m-%d %H:%M')})")

    return "\n".join(lines)


@mcp_server.tool(description="Record a technical or architectural decision for a project")
async def record_decision(
    project_id: str,
    title: str,
    rationale: str = "",
    task_id: Optional[str] = None,
) -> str:
    """Record a decision so future agents and humans can understand why something was done.

    Args:
        project_id: UUID of the project
        title: Short summary of the decision (e.g. "Use PKCE over implicit flow")
        rationale: Explanation of why this decision was made
        task_id: Optional UUID of the related task
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."

        decision = Decision(
            id=uuid.uuid4(),
            title=title,
            rationale=rationale or None,
            author_type="agent",
            author_id=uuid.UUID(user_id),
            project_id=uuid.UUID(project_id),
            task_id=uuid.UUID(task_id) if task_id else None,
        )
        db.add(decision)
        await db.commit()
        await db.refresh(decision)
        return f"Decision recorded: '{decision.title}' (ID: {decision.id})"


@mcp_server.tool(description="List decisions for a project, optionally filtered by search query")
async def list_decisions(
    project_id: str,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """List decisions recorded in a project.

    Args:
        project_id: UUID of the project
        q: Search query to filter by title or rationale (case-insensitive). Omit to list all.
        cursor: Pagination cursor from a previous response. Omit for first page.
        limit: Max items to return (default 50, max 100).
    """

    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        query = (
            select(Decision)
            .join(Project)
            .where(
                Decision.project_id == uuid.UUID(project_id),
                Project.team_id == uuid.UUID(team_id),
            )
            .order_by(Decision.created_at.desc())
        )
        if q:
            from sqlalchemy import or_
            pattern = f"%{q}%"
            query = query.where(or_(Decision.title.ilike(pattern), Decision.rationale.ilike(pattern)))

        query, effective_limit = apply_cursor_pagination(query, Decision, cursor, limit)
        result = await db.execute(query)
        all_items = list(result.scalars().all())
        decisions, next_cursor, has_more = paginate_results(all_items, effective_limit)
        if not decisions:
            return "No decisions found."
        lines = []
        for d in decisions:
            task_str = f" (task: {d.task_id})" if d.task_id else ""
            lines.append(f"- {d.title}{task_str} (ID: {d.id})")
            if d.rationale:
                lines.append(f"  {d.rationale[:200]}{'...' if len(d.rationale) > 200 else ''}")
        header = f"Decisions ({len(decisions)}):\n"
        footer = ""
        if next_cursor:
            footer = f"\n\nnext_cursor: {next_cursor}"
        return header + "\n".join(lines) + footer


@mcp_server.tool(description="Delete a decision")
async def delete_decision(decision_id: str) -> str:
    """Permanently delete a decision.

    Args:
        decision_id: UUID of the decision to delete
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Decision)
            .join(Project)
            .where(
                Decision.id == uuid.UUID(decision_id),
                Project.team_id == uuid.UUID(team_id),
            )
        )
        decision = result.scalar_one_or_none()
        if not decision:
            return "Error: Decision not found."

        title = decision.title
        await db.delete(decision)
        await db.commit()
        return f"Decision deleted: '{title}' (ID: {decision_id})"


@mcp_server.tool(description="Get full context for a task: task details and decisions")
async def get_task_context(task_id: str, response_mode: str = "human") -> str:
    """Retrieve a task along with any recorded decisions.

    Args:
        task_id: UUID of the task
        response_mode: one of human, json, both.
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."
    mode_error = _validate_response_mode(response_mode)
    if mode_error:
        return mode_error
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        return "Error: Invalid task_id UUID."

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(
                Task.id == task_uuid,
                Project.team_id == uuid.UUID(team_id),
            )
        )
        task = result.scalar_one_or_none()
        if not task:
            return "Error: Task not found."

        decisions_result = await db.execute(
            select(Decision)
            .where(Decision.task_id == task_uuid)
            .order_by(Decision.created_at.desc())
        )
        decisions = decisions_result.scalars().all()

    lines = [
        f"# Task: {task.title}",
        f"Status: {task.status}",
        f"Priority: {task.priority or 'not set'}",
        f"Estimate: {task.estimate or 'not set'}",
        f"ID: {task.id}",
        f"\nDescription:\n{task.description or '(none)'}",
    ]

    lines.append(f"\n## Decisions ({len(decisions)})")
    for d in decisions:
        lines.append(f"  - {d.title} (ID: {d.id})")
        if d.rationale:
            lines.append(f"    {d.rationale}")
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
    """Create a new milestone in the specified project.

    Args:
        project_id: UUID of the project
        title: Milestone title
        description: Optional description
        due_date: Optional due date in YYYY-MM-DD format
        status: One of: planned, in_progress, completed, cancelled
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    VALID_MS = {"planned", "in_progress", "completed", "cancelled"}
    if status not in VALID_MS:
        return f"Error: Invalid status. Must be one of: {VALID_MS}"

    from datetime import date
    parsed_due = None
    if due_date:
        try:
            parsed_due = date.fromisoformat(due_date)
        except ValueError:
            return "Error: due_date must be in YYYY-MM-DD format."

    async with async_session() as db:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."

        milestone = Milestone(
            id=uuid.uuid4(),
            project_id=uuid.UUID(project_id),
            title=title,
            description=description or None,
            due_date=parsed_due,
            status=status,
        )
        db.add(milestone)
        await db.commit()
        await db.refresh(milestone)
        due_str = f", due {milestone.due_date}" if milestone.due_date else ""
        return f"Milestone created: '{milestone.title}' [{milestone.status}]{due_str} (ID: {milestone.id})"


@mcp_server.tool(description="Update a milestone's fields")
async def update_milestone(
    milestone_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    due_date: Optional[str] = None,
    status: Optional[str] = None,
) -> str:
    """Update one or more fields on a milestone.

    Args:
        milestone_id: UUID of the milestone
        title: New title (optional)
        description: New description (optional)
        due_date: New due date in YYYY-MM-DD format (optional)
        status: New status — planned, in_progress, completed, cancelled (optional)
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    VALID_MS = {"planned", "in_progress", "completed", "cancelled"}
    if status and status not in VALID_MS:
        return f"Error: Invalid status. Must be one of: {VALID_MS}"

    from datetime import date
    parsed_due = None
    if due_date:
        try:
            parsed_due = date.fromisoformat(due_date)
        except ValueError:
            return "Error: due_date must be in YYYY-MM-DD format."

    async with async_session() as db:
        result = await db.execute(
            select(Milestone).join(Project).where(
                Milestone.id == uuid.UUID(milestone_id),
                Project.team_id == uuid.UUID(team_id),
            )
        )
        milestone = result.scalar_one_or_none()
        if not milestone:
            return "Error: Milestone not found."

        if title is not None:
            milestone.title = title
        if description is not None:
            milestone.description = description
        if parsed_due is not None:
            milestone.due_date = parsed_due
        if status is not None:
            milestone.status = status

        await db.commit()
        await db.refresh(milestone)
        due_str = f", due {milestone.due_date}" if milestone.due_date else ""
        return f"Milestone updated: '{milestone.title}' [{milestone.status}]{due_str} (ID: {milestone.id})"


@mcp_server.tool(description="Get a project summary: task counts and milestones with progress")
async def get_project_summary(project_id: str) -> str:
    """Returns overall task counts and milestone progress for a project.

    Args:
        project_id: UUID of the project
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."

        tasks_result = await db.execute(
            select(Task).where(Task.project_id == uuid.UUID(project_id))
        )
        all_tasks = tasks_result.scalars().all()

        total: dict[str, int] = {"todo": 0, "in_progress": 0, "blocked": 0, "done": 0, "cancelled": 0}
        ms_counts: dict[uuid.UUID, dict[str, int]] = {}
        for t in all_tasks:
            total[t.status] = total.get(t.status, 0) + 1
            if t.milestone_id:
                if t.milestone_id not in ms_counts:
                    ms_counts[t.milestone_id] = {"todo": 0, "in_progress": 0, "blocked": 0, "done": 0, "cancelled": 0}
                ms_counts[t.milestone_id][t.status] += 1

        milestones_result = await db.execute(
            select(Milestone)
            .where(Milestone.project_id == uuid.UUID(project_id))
            .order_by(Milestone.position.asc().nullslast(), Milestone.created_at.asc())
        )
        milestones = milestones_result.scalars().all()

    lines = [
        f"# {project.name} — Summary",
        "\n## Overall Tasks",
        f"  todo: {total['todo']}  in_progress: {total['in_progress']}  blocked: {total['blocked']}  done: {total['done']}",
        f"  total: {sum(total.values())}",
    ]

    lines.append(f"\n## Milestones ({len(milestones)})")
    for m in milestones:
        due_str = f" (due {m.due_date})" if m.due_date else ""
        lines.append(f"  [{m.status}] {m.title}{due_str} (ID: {m.id})")
        counts = ms_counts.get(m.id, {})
        if counts:
            total_ms = sum(counts.values())
            done_ms = counts.get("done", 0)
            lines.append(f"    Tasks: {done_ms}/{total_ms} done  |  todo:{counts.get('todo',0)} in_progress:{counts.get('in_progress',0)} blocked:{counts.get('blocked',0)}")

    return "\n".join(lines)


@mcp_server.tool()
async def update_my_card(
    description: str | None = None,
    skills: list[str] | None = None,
    role: str | None = None,
) -> str:
    """Update the agent's own card: description, skills, and/or role.

    Standard roles: planner, implementer, reviewer, general.
    Skills is a list of strings, e.g. ['Python', 'FastAPI', 'React'].

    Args:
        description: Optional profile description text.
        skills: Optional list of skill labels.
        role: Optional role label (planner, implementer, reviewer, general).
    """
    user_id = current_user_id.get()
    if not user_id:
        return "Error: not authenticated."
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if not user:
            return "Error: user not found."
        if description is not None:
            user.description = description
        if skills is not None:
            user.skills = skills
        if role is not None:
            user.role = role
        await db.commit()
        await db.refresh(user)

    parts = []
    if user.description:
        parts.append(f"Description: {user.description}")
    if user.skills:
        parts.append(f"Skills: {', '.join(user.skills)}")
    if user.role:
        parts.append(f"Role: {user.role}")

    return f"Agent card updated for {user.name}.\n" + "\n".join(parts)


@mcp_server.tool()
async def discover_agents() -> str:
    """List all agents on your team with their cards (name, role, skills, description).
    Use this to find the right agent to send a message to."""
    team_id = current_team_id.get()
    if not team_id:
        return "Error: not authenticated."
    async with async_session() as db:
        result = await db.execute(
            select(User)
            .where(User.team_id == uuid.UUID(team_id), User.user_type == "agent")
            .order_by(User.created_at)
        )
        agents = result.scalars().all()

    if not agents:
        return "No agents found on your team."

    lines = [f"# Agents on your team ({len(agents)})"]
    for a in agents:
        lines.append(f"\n## {a.name} (ID: {a.id})")
        lines.append(f"  Email: {a.email}")
        if a.role:
            lines.append(f"  Role: {a.role}")
        if a.skills:
            lines.append(f"  Skills: {', '.join(a.skills)}")
        if a.description:
            lines.append(f"  Description: {a.description}")
    return "\n".join(lines)


@mcp_server.tool()
async def send_message(
    recipient_id: str,
    body: str,
    message_type: str = "info",
    subject: Optional[str] = None,
) -> str:
    """Send a message to another team member (agent or human).

    Args:
        recipient_id: UUID of the recipient (use list_team_members to find IDs)
        body: Message body
        message_type: One of: task_delegation, status_update, question, info, hello
        subject: Optional subject line
    """

    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: not authenticated."

    async with async_session() as db:
        recipient = await db.get(User, uuid.UUID(recipient_id))
        if not recipient or str(recipient.team_id) != team_id:
            return f"Error: team member {recipient_id} not found on your team."

        sender = await db.get(User, uuid.UUID(user_id))

        msg = A2AMessage(
            id=uuid.uuid4(),
            team_id=uuid.UUID(team_id),
            sender_id=uuid.UUID(user_id),
            recipient_id=uuid.UUID(recipient_id),
            message_type=message_type,
            subject=subject,
            body=body,
            read=False,
        )
        db.add(msg)
        await db.commit()

    sender_name = sender.name if sender else "you"
    return f"Message sent to {recipient.name} [{message_type}].\nFrom: {sender_name}\n{f'Subject: {subject}' if subject else ''}\n{body[:200]}"


@mcp_server.tool()
async def get_pending_messages(
    mark_as_read: bool = False,
    include_read: bool = False,
) -> str:
    """Get messages sent to you from other team members (agents or humans).

    Args:
        mark_as_read: If true, marks all returned unread messages as read
        include_read: If true, returns all recent messages (read and unread), not just unread.
                      Useful when you expect a message but get_pending_messages shows none — it
                      may have been marked read by the UI or another session.
    """

    user_id = current_user_id.get()
    if not user_id:
        return "Error: not authenticated."

    async with async_session() as db:
        query = (
            select(A2AMessage)
            .where(A2AMessage.recipient_id == uuid.UUID(user_id))
            .order_by(A2AMessage.created_at.desc())
            .limit(20)
        )
        if not include_read:
            query = query.where(A2AMessage.read == False)  # noqa: E712

        result = await db.execute(query)
        msgs = result.scalars().all()

        if not msgs:
            return "No unread messages." if not include_read else "No messages."

        label = "Recent messages" if include_read else "Unread messages"
        lines = [f"# {label} ({len(msgs)})"]
        for msg in msgs:
            sender = await db.get(User, msg.sender_id)
            sender_name = sender.name if sender else str(msg.sender_id)
            subject_str = f" — {msg.subject}" if msg.subject else ""
            read_str = "  [read]" if msg.read else ""
            lines.append(f"\n## [{msg.message_type}]{subject_str}{read_str}")
            lines.append(f"  From: {sender_name}  |  ID: {msg.id}  |  {msg.created_at.strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"  {msg.body}")
            if mark_as_read and not msg.read:
                msg.read = True

        if mark_as_read:
            await db.commit()
            unread_count = sum(1 for m in msgs if not m.read)
            if unread_count > 0:
                lines.append(f"\n(Marked {unread_count} message(s) as read)")

    return "\n".join(lines)


@mcp_server.tool()
async def list_team_members() -> str:
    """List all members on your team — both humans and agents — with their IDs.
    Use this to find a human's ID before sending them a message via send_message()."""
    team_id = current_team_id.get()
    if not team_id:
        return "Error: not authenticated."
    async with async_session() as db:
        result = await db.execute(
            select(User).where(User.team_id == uuid.UUID(team_id)).order_by(User.created_at)
        )
        members = result.scalars().all()

    lines = [f"# Team Members ({len(members)})"]
    for m in members:
        role_str = f" [{m.role}]" if m.role else ""
        lines.append(f"  {m.user_type.upper()} {m.name}{role_str} <{m.email}> (ID: {m.id})")
    return "\n".join(lines)


@mcp_server.tool(description="Record a durable project fact")
async def create_fact(
    project_id: str,
    title: str,
    body: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """Create a new fact in a project. Facts are long-term memory — things like coding conventions,
    architectural constraints, or important context that should persist across sessions.

    Args:
        project_id: UUID of the project
        title: Short, descriptive title for the fact
        body: Optional markdown body with details
        category: Optional category (e.g. 'convention', 'constraint', 'context')
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."

        fact = Fact(
            id=uuid.uuid4(),
            project_id=uuid.UUID(project_id),
            team_id=uuid.UUID(team_id),
            title=title,
            body=body,
            category=category,
            author_id=uuid.UUID(user_id),
            author_type="agent",
        )
        db.add(fact)
        await db.commit()
        await db.refresh(fact)
    cat_str = f" [{category}]" if category else ""
    return f"Fact recorded{cat_str}: {title} (ID: {fact.id})"


@mcp_server.tool(description="List all facts for a project. Facts are durable knowledge — conventions, constraints, and context.")
async def list_facts(project_id: str, q: Optional[str] = None) -> str:
    """List all facts in a project, optionally filtered by a search query.

    Args:
        project_id: UUID of the project
        q: Search query to filter by title or body (case-insensitive). Omit to list all.
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        query = (
            select(Fact)
            .join(Project)
            .where(Fact.project_id == uuid.UUID(project_id), Project.team_id == uuid.UUID(team_id))
            .order_by(Fact.created_at.desc())
        )
        if q:
            from sqlalchemy import or_
            pattern = f"%{q}%"
            query = query.where(or_(Fact.title.ilike(pattern), Fact.body.ilike(pattern)))
        result = await db.execute(query)
        facts = result.scalars().all()

    if not facts:
        return "No facts recorded yet."

    lines = [f"# Project Facts ({len(facts)})"]
    for f in facts:
        cat_str = f" [{f.category}]" if f.category else ""
        lines.append(f"\n- **{f.title}**{cat_str} (ID: {f.id})")
        if f.body:
            lines.append(f"  {f.body[:200]}{'...' if len(f.body) > 200 else ''}")
    return "\n".join(lines)


@mcp_server.tool(description="Publish a reusable skill that other agents can consume")
async def create_skill(
    title: str,
    body: str,
    project_id: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    """Create a skill — a reusable piece of knowledge or instruction that agents can discover and follow.
    Skills with no project_id are team-wide; skills with a project_id are scoped to that project.

    Args:
        title: Short, descriptive title for the skill
        body: Markdown body with the skill content (instructions, steps, conventions)
        project_id: Optional UUID of a project to scope the skill to. Omit for team-wide.
        category: Optional category (e.g. 'workflow', 'coding', 'testing', 'deployment')
        tags: Optional list of tags for discovery (e.g. ['python', 'fastapi'])
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        if project_id:
            project = await db.get(Project, uuid.UUID(project_id))
            if not project or str(project.team_id) != team_id:
                return "Error: Project not found."

        skill = Skill(
            id=uuid.uuid4(),
            team_id=uuid.UUID(team_id),
            project_id=uuid.UUID(project_id) if project_id else None,
            title=title,
            body=body,
            category=category,
            tags=tags,
            author_id=uuid.UUID(user_id),
            author_type="agent",
        )
        db.add(skill)
        await db.commit()
        await db.refresh(skill)

    scope = f"project {project_id}" if project_id else "team-wide"
    cat_str = f" [{category}]" if category else ""
    return f"Skill published{cat_str}: '{title}' ({scope}) (ID: {skill.id})"


@mcp_server.tool(description="List skills available to your team, optionally filtered by project, category, or search query")
async def list_skills(
    project_id: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
) -> str:
    """List skills visible to the team. When project_id is given, returns both
    project-specific AND team-wide skills.

    Args:
        project_id: Optional UUID of a project. Returns project + team-wide skills.
        category: Filter by category.
        q: Search query to filter by title or body (case-insensitive).
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        from sqlalchemy import or_
        query = (
            select(Skill)
            .where(Skill.team_id == uuid.UUID(team_id))
            .order_by(Skill.created_at.desc())
        )
        if project_id:
            query = query.where(
                or_(Skill.project_id == uuid.UUID(project_id), Skill.project_id.is_(None))
            )
        if category:
            query = query.where(Skill.category == category)
        if q:
            pattern = f"%{q}%"
            query = query.where(or_(Skill.title.ilike(pattern), Skill.body.ilike(pattern)))

        result = await db.execute(query.limit(50))
        skills = result.scalars().all()

    if not skills:
        return "No skills found."

    lines = [f"# Skills ({len(skills)})"]
    for s in skills:
        scope = f"project:{s.project_id}" if s.project_id else "team-wide"
        cat_str = f" [{s.category}]" if s.category else ""
        tags_str = f" tags:{','.join(s.tags)}" if s.tags else ""
        lines.append(f"\n- **{s.title}**{cat_str}{tags_str} ({scope}) (ID: {s.id})")
        if s.body:
            lines.append(f"  {s.body[:200]}{'...' if len(s.body) > 200 else ''}")
    return "\n".join(lines)


@mcp_server.tool(description="Get the full content of a skill by ID")
async def get_skill(skill_id: str) -> str:
    """Retrieve the complete skill content. Use this to read a skill before following it.

    Args:
        skill_id: UUID of the skill
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Skill).where(Skill.id == uuid.UUID(skill_id), Skill.team_id == uuid.UUID(team_id))
        )
        skill = result.scalar_one_or_none()
        if not skill:
            return "Error: Skill not found."

    scope = f"project:{skill.project_id}" if skill.project_id else "team-wide"
    cat_str = f"Category: {skill.category}\n" if skill.category else ""
    tags_str = f"Tags: {', '.join(skill.tags)}\n" if skill.tags else ""
    return (
        f"# {skill.title}\n"
        f"ID: {skill.id}\n"
        f"Scope: {scope}\n"
        f"{cat_str}{tags_str}"
        f"Author: {skill.author_type} ({skill.author_id})\n"
        f"\n{skill.body}"
    )


@mcp_server.tool(description="Update a skill's content")
async def update_skill(
    skill_id: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    """Update one or more fields on a skill.

    Args:
        skill_id: UUID of the skill to update
        title: New title (optional)
        body: New body content (optional)
        category: New category (optional)
        tags: New tags list (optional)
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Skill).where(Skill.id == uuid.UUID(skill_id), Skill.team_id == uuid.UUID(team_id))
        )
        skill = result.scalar_one_or_none()
        if not skill:
            return "Error: Skill not found."

        if title is not None:
            skill.title = title
        if body is not None:
            skill.body = body
        if category is not None:
            skill.category = category
        if tags is not None:
            skill.tags = tags

        await db.commit()
        await db.refresh(skill)

    return f"Skill updated: '{skill.title}' (ID: {skill.id})"


@mcp_server.tool(description="Delete a skill")
async def delete_skill(skill_id: str) -> str:
    """Permanently delete a skill.

    Args:
        skill_id: UUID of the skill to delete
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Skill).where(Skill.id == uuid.UUID(skill_id), Skill.team_id == uuid.UUID(team_id))
        )
        skill = result.scalar_one_or_none()
        if not skill:
            return "Error: Skill not found."

        title = skill.title
        await db.delete(skill)
        await db.commit()

    return f"Skill deleted: '{title}' (ID: {skill_id})"


@mcp_server.tool(description="Add a comment to a task")
async def add_task_comment(task_id: str, body: str) -> str:
    """Post a comment on a task. Useful for leaving notes, status updates, or questions.

    Args:
        task_id: UUID of the task
        body: Comment text
    """

    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(
                Task.id == uuid.UUID(task_id),
                Project.team_id == uuid.UUID(team_id),
            )
        )
        task = result.scalar_one_or_none()
        if not task:
            return "Error: Task not found."

        comment = TaskComment(
            id=uuid.uuid4(),
            task_id=uuid.UUID(task_id),
            author_id=uuid.UUID(user_id),
            body=body,
        )
        db.add(comment)
        await db.commit()
        await db.refresh(comment)

    return f"Comment added to '{task.title}' (comment ID: {comment.id})"


@mcp_server.tool(description="List comments on a task")
async def list_task_comments(task_id: str) -> str:
    """Get all comments on a task, with author names and timestamps.

    Args:
        task_id: UUID of the task
    """

    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(
                Task.id == uuid.UUID(task_id),
                Project.team_id == uuid.UUID(team_id),
            )
        )
        task = result.scalar_one_or_none()
        if not task:
            return "Error: Task not found."

        comments_result = await db.execute(
            select(TaskComment, User.name.label("author_name"))
            .join(User, TaskComment.author_id == User.id)
            .where(TaskComment.task_id == uuid.UUID(task_id))
            .order_by(TaskComment.created_at.asc())
        )
        rows = comments_result.all()

    if not rows:
        return f"No comments on '{task.title}'."

    lines = [f"# Comments on '{task.title}' ({len(rows)})"]
    for comment, author_name in rows:
        lines.append(f"\n**{author_name}** — {comment.created_at.strftime('%Y-%m-%d %H:%M')} (ID: {comment.id})")
        lines.append(comment.body)

    return "\n".join(lines)


@mcp_server.tool(description="Create multiple tasks in a project in a single call. Useful for bootstrapping a milestone or feature with several tasks at once.")
async def batch_create_tasks(project_id: str, tasks: list[dict]) -> str:
    """Create multiple tasks in one transaction.

    Args:
        project_id: UUID of the project
        tasks: List of task objects. Each may have: title (required), description,
               status (default: todo), priority, estimate, milestone_id
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."
        max_sort_result = await db.execute(
            select(func.max(Task.sort_order)).where(Task.project_id == uuid.UUID(project_id))
        )
        next_sort_order = (max_sort_result.scalar() or 0) + 1

        created = []
        errors = []
        for i, t in enumerate(tasks):
            if not t.get("title"):
                errors.append(f"Task {i}: missing required field 'title'")
                continue
            status = t.get("status", "todo")
            if status not in VALID_STATUSES:
                errors.append(f"Task {i} ({t['title']}): invalid status '{status}'")
                continue
            task = Task(
                id=uuid.uuid4(),
                title=t["title"],
                description=t.get("description", ""),
                status=status,
                priority=t.get("priority"),
                estimate=t.get("estimate"),
                sort_order=next_sort_order,
                project_id=uuid.UUID(project_id),
                milestone_id=uuid.UUID(t["milestone_id"]) if t.get("milestone_id") else None,
            )
            db.add(task)
            created.append(task)
            next_sort_order += 1

        await db.commit()
        for task in created:
            await db.refresh(task)

    lines = [f"Created {len(created)}/{len(tasks)} tasks in project {project_id}:"]
    for task in created:
        lines.append(f"  - {task.title} [{task.status}] (ID: {task.id})")
    if errors:
        lines.append(f"\nErrors ({len(errors)}):")
        for e in errors:
            lines.append(f"  - {e}")
    return "\n".join(lines)


@mcp_server.tool(description="Update a project's name or description.")
async def update_project(project_id: str, name: Optional[str] = None, description: Optional[str] = None) -> str:
    """Update the name and/or description of a project.

    Args:
        project_id: UUID of the project
        name: New project name (optional)
        description: New project description (optional)
    """
    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."
    if name is None and description is None:
        return "Error: Provide at least one field to update (name or description)."

    async with async_session() as db:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        await db.commit()
        await db.refresh(project)

    return f"Project updated: {project.name} (ID: {project.id})"


@mcp_server.tool(description="Add a dependency: task_id is blocked by depends_on_id")
async def add_dependency(task_id: str, depends_on_id: str) -> str:
    """Mark that a task is blocked by another task (task_id depends on depends_on_id).

    Args:
        task_id: UUID of the task that is blocked
        depends_on_id: UUID of the task it depends on (must complete first)
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(Task.id == uuid.UUID(task_id), Project.team_id == uuid.UUID(team_id))
        )
        task = result.scalar_one_or_none()
        if not task:
            return "Error: Task not found."

        result2 = await db.execute(
            select(Task).join(Project).where(Task.id == uuid.UUID(depends_on_id), Project.team_id == uuid.UUID(team_id))
        )
        dep_task = result2.scalar_one_or_none()
        if not dep_task:
            return "Error: Dependency task not found."

        if task.id == dep_task.id:
            return "Error: A task cannot depend on itself."

        existing = await db.get(TaskDependency, (task.id, dep_task.id))
        if existing:
            return f"Dependency already exists: '{task.title}' is already blocked by '{dep_task.title}'."

        db.add(TaskDependency(task_id=task.id, depends_on_id=dep_task.id))
        await log_audit(db, task.project_id, "task", task.id, task.title, "dependency_added",
                        uuid.UUID(user_id) if user_id else None,
                        new_values={"depends_on_id": str(dep_task.id), "depends_on_title": dep_task.title})
        await db.commit()
        return f"Dependency added: '{task.title}' is now blocked by '{dep_task.title}'."


@mcp_server.tool(description="Remove a dependency between two tasks")
async def remove_dependency(task_id: str, depends_on_id: str) -> str:
    """Remove a blocked-by dependency between tasks.

    Args:
        task_id: UUID of the dependent task
        depends_on_id: UUID of the task to unblock from
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(Task.id == uuid.UUID(task_id), Project.team_id == uuid.UUID(team_id))
        )
        task = result.scalar_one_or_none()
        if not task:
            return "Error: Task not found."

        dep = await db.get(TaskDependency, (uuid.UUID(task_id), uuid.UUID(depends_on_id)))
        if not dep:
            return "Error: Dependency not found."

        await log_audit(db, task.project_id, "task", task.id, task.title, "dependency_removed",
                        uuid.UUID(user_id) if user_id else None,
                        old_values={"depends_on_id": depends_on_id})
        await db.delete(dep)
        await db.commit()
        return f"Dependency removed from task '{task.title}'."


@mcp_server.tool(description="List all dependencies (blocked-by tasks) for a given task")
async def list_dependencies(task_id: str) -> str:
    """List tasks that must complete before the given task can proceed.

    Args:
        task_id: UUID of the task to check
    """
    user_id = current_user_id.get()
    team_id = current_team_id.get()
    if not user_id or not team_id:
        return "Error: Not authenticated."

    async with async_session() as db:
        result = await db.execute(
            select(Task).join(Project).where(Task.id == uuid.UUID(task_id), Project.team_id == uuid.UUID(team_id))
        )
        task = result.scalar_one_or_none()
        if not task:
            return "Error: Task not found."

        deps = await db.execute(
            select(Task).join(TaskDependency, Task.id == TaskDependency.depends_on_id)
            .where(TaskDependency.task_id == task.id)
        )
        dep_tasks = deps.scalars().all()
        if not dep_tasks:
            return f"Task '{task.title}' has no dependencies."

        lines = [f"'{task.title}' is blocked by:"]
        for d in dep_tasks:
            lines.append(f"  - [{d.status}] {d.title} (ID: {d.id})")
        return "\n".join(lines)


@mcp_server.tool(description="Get recent changes in a project since a given timestamp")
async def get_changes_since(project_id: str, since: str) -> str:
    """Query the audit log for all changes in a project since a given ISO timestamp.
    Returns changes grouped by entity type — useful for catching up after being away.

    Args:
        project_id: UUID of the project
        since: ISO 8601 timestamp (e.g. '2026-03-07T00:00:00Z'). Returns all changes after this time.
    """
    from collections import defaultdict
    from datetime import datetime

    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."

    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        return "Error: 'since' must be a valid ISO 8601 timestamp (e.g. '2026-03-07T00:00:00Z')."

    async with async_session() as db:
        project = await db.get(Project, uuid.UUID(project_id))
        if not project or str(project.team_id) != team_id:
            return "Error: Project not found."

        result = await db.execute(
            select(AuditLog)
            .where(
                AuditLog.project_id == uuid.UUID(project_id),
                AuditLog.created_at > since_dt,
            )
            .order_by(AuditLog.created_at.asc())
            .limit(200)
        )
        entries = result.scalars().all()

    if not entries:
        return f"No changes since {since}."

    # Fetch actor names
    actor_ids = {e.actor_id for e in entries if e.actor_id}
    actors: dict[uuid.UUID, str] = {}
    if actor_ids:
        async with async_session() as db:
            users_result = await db.execute(select(User).where(User.id.in_(actor_ids)))
            actors = {u.id: u.name or u.email for u in users_result.scalars().all()}

    # Group by entity_type
    grouped: dict[str, list[AuditLog]] = defaultdict(list)
    for e in entries:
        grouped[e.entity_type].append(e)

    lines = [f"# Changes since {since} ({len(entries)} total)"]
    for entity_type, group in grouped.items():
        lines.append(f"\n## {entity_type.title()} ({len(group)} changes)")
        for e in group:
            actor = actors.get(e.actor_id, "unknown") if e.actor_id else "system"
            title_str = f" '{e.entity_title}'" if e.entity_title else ""
            ts = e.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"  - [{e.action}]{title_str} by {actor} at {ts}")
            if e.action == "updated" and e.new_values:
                changed = ", ".join(f"{k}: {v}" for k, v in e.new_values.items())
                lines.append(f"    → {changed[:200]}")

    if len(entries) >= 200:
        lines.append("\n(Showing first 200 changes. Use a more recent 'since' to paginate.)")

    return "\n".join(lines)


@mcp_server.tool(description="Search across tasks, decisions, facts, and skills in a single call")
async def search(
    project_id: str,
    q: str,
    limit: int = 5,
) -> str:
    """Unified cross-entity search. Returns results grouped by entity type.
    Saves agents from making separate list_tasks/list_decisions/list_facts/list_skills calls.

    Args:
        project_id: UUID of the project
        q: Search query (case-insensitive, matched against titles and bodies)
        limit: Max results per entity type (default 5, max 20)
    """
    from sqlalchemy import or_

    team_id = current_team_id.get()
    if not team_id:
        return "Error: Not authenticated."
    if not q or not q.strip():
        return "Error: Search query 'q' is required."

    limit = max(1, min(limit, 20))
    pattern = f"%{q}%"
    pid = uuid.UUID(project_id)
    tid = uuid.UUID(team_id)

    async with async_session() as db:
        project = await db.get(Project, pid)
        if not project or project.team_id != tid:
            return "Error: Project not found."

        # Tasks
        tasks_q = (
            select(Task).join(Project)
            .where(Task.project_id == pid, Project.team_id == tid,
                   or_(Task.title.ilike(pattern), Task.description.ilike(pattern)))
            .order_by(Task.updated_at.desc()).limit(limit)
        )
        tasks_result = await db.execute(tasks_q)
        tasks = tasks_result.scalars().all()

        # Decisions
        decisions_q = (
            select(Decision).join(Project)
            .where(Decision.project_id == pid, Project.team_id == tid,
                   or_(Decision.title.ilike(pattern), Decision.rationale.ilike(pattern)))
            .order_by(Decision.created_at.desc()).limit(limit)
        )
        decisions_result = await db.execute(decisions_q)
        decisions = decisions_result.scalars().all()

        # Facts
        facts_q = (
            select(Fact).join(Project)
            .where(Fact.project_id == pid, Project.team_id == tid,
                   or_(Fact.title.ilike(pattern), Fact.body.ilike(pattern)))
            .order_by(Fact.created_at.desc()).limit(limit)
        )
        facts_result = await db.execute(facts_q)
        facts = facts_result.scalars().all()

        # Skills (project-scoped + team-wide)
        skills_q = (
            select(Skill)
            .where(Skill.team_id == tid,
                   or_(Skill.project_id == pid, Skill.project_id.is_(None)),
                   or_(Skill.title.ilike(pattern), Skill.body.ilike(pattern)))
            .order_by(Skill.created_at.desc()).limit(limit)
        )
        skills_result = await db.execute(skills_q)
        skills = skills_result.scalars().all()

    total = len(tasks) + len(decisions) + len(facts) + len(skills)
    if total == 0:
        return f"No results for '{q}'."

    lines = [f"# Search results for '{q}' ({total} hits)"]

    if tasks:
        lines.append(f"\n## Tasks ({len(tasks)})")
        for t in tasks:
            lines.append(f"  - [{t.status}] {t.title} (ID: {t.id})")
            if t.description:
                lines.append(f"    {t.description[:120].replace(chr(10), ' ')}{'...' if len(t.description) > 120 else ''}")

    if decisions:
        lines.append(f"\n## Decisions ({len(decisions)})")
        for d in decisions:
            lines.append(f"  - {d.title} (ID: {d.id})")
            if d.rationale:
                lines.append(f"    {d.rationale[:120].replace(chr(10), ' ')}{'...' if len(d.rationale) > 120 else ''}")

    if facts:
        lines.append(f"\n## Facts ({len(facts)})")
        for f in facts:
            cat_str = f" [{f.category}]" if f.category else ""
            lines.append(f"  - {f.title}{cat_str} (ID: {f.id})")
            if f.body:
                lines.append(f"    {f.body[:120].replace(chr(10), ' ')}{'...' if len(f.body) > 120 else ''}")

    if skills:
        lines.append(f"\n## Skills ({len(skills)})")
        for s in skills:
            scope = "team-wide" if not s.project_id else "project"
            lines.append(f"  - {s.title} ({scope}) (ID: {s.id})")
            if s.body:
                lines.append(f"    {s.body[:120].replace(chr(10), ' ')}{'...' if len(s.body) > 120 else ''}")

    return "\n".join(lines)
