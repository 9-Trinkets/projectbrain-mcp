"""Tests for tasks.get_my_tasks MCP action."""
import json

import pytest

import api_adapter  # noqa: F401
import server

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CALLER_ID = "agent-abc"
_PROJECT_ID = "proj-1"

_ME = {"id": _CALLER_ID, "name": "Agent ABC", "team_id": "team-1"}

_WORKFLOW_TWO_STAGES = {
    "statuses": [
        {"id": "s1", "name": "todo"},
        {"id": "s2", "name": "in_progress"},
        {"id": "s3", "name": "reviewing"},
        {"id": "s4", "name": "done"},
    ],
    "stages": [
        {
            "id": "stage-todo",
            "name": "To Do",
            "statuses": [{"id": "s1", "name": "todo"}],
            "claimed_agents": [],
        },
        {
            "id": "stage-wip",
            "name": "In Progress",
            "statuses": [
                {"id": "s2", "name": "in_progress"},
                {"id": "s3", "name": "reviewing"},
            ],
            "claimed_agents": [{"id": _CALLER_ID}],
        },
        {
            "id": "stage-done",
            "name": "Done",
            "statuses": [{"id": "s4", "name": "done"}],
            "claimed_agents": [{"id": "other-agent"}],
        },
    ],
}

_TASKS_IN_PROGRESS = {
    "items": [
        {
            "id": "task-1",
            "title": "Implement feature",
            "status": "in_progress",
            "priority": "high",
            "sort_order": 1,
            "assignee_id": None,
            "project_id": _PROJECT_ID,
            "description": None,
            "estimate": None,
            "milestone_id": None,
            "created_at": "2026-03-16T00:00:00Z",
            "updated_at": "2026-03-16T00:00:00Z",
        }
    ],
    "has_more": False,
    "next_cursor": None,
}

_TASKS_REVIEWING = {
    "items": [
        {
            "id": "task-2",
            "title": "Review PR",
            "status": "reviewing",
            "priority": "medium",
            "sort_order": 2,
            "assignee_id": None,
            "project_id": _PROJECT_ID,
            "description": None,
            "estimate": None,
            "milestone_id": None,
            "created_at": "2026-03-16T00:00:00Z",
            "updated_at": "2026-03-16T00:00:00Z",
        }
    ],
    "has_more": False,
    "next_cursor": None,
}

_TASKS_EMPTY = {"items": [], "has_more": False, "next_cursor": None}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_my_tasks_human_eligible_stages_only(monkeypatch):
    """Returns only tasks in stages where the caller is claimed (or open stages)."""
    calls = []

    async def fake_get(path, *, params=None, client=None):
        calls.append((path, params))
        if path == "/api/auth/me":
            return _ME
        if path == f"/api/projects/{_PROJECT_ID}/workflow":
            return _WORKFLOW_TWO_STAGES
        if params and params.get("status") == "in_progress":
            return _TASKS_IN_PROGRESS
        if params and params.get("status") == "reviewing":
            return _TASKS_REVIEWING
        # "todo" stage is open (no claimed agents) — return empty so test stays focused
        return _TASKS_EMPTY

    monkeypatch.setattr(server, "_api_get", fake_get)
    result = await server.tasks(action="get_my_tasks", project_id=_PROJECT_ID)

    # Should include tasks from In Progress stage (claimed by caller) and To Do (open)
    assert "task-1" in result
    assert "task-2" in result
    # Should NOT include tasks from Done stage (claimed by other-agent only)
    assert "done" not in result or "stage-done" not in result

    # High priority task (task-1) should appear before medium (task-2)
    assert result.index("task-1") < result.index("task-2")


@pytest.mark.asyncio
async def test_get_my_tasks_status_filter(monkeypatch):
    """status filter restricts results to the specified status only."""
    async def fake_get(path, *, params=None, client=None):
        if path == "/api/auth/me":
            return _ME
        if path == f"/api/projects/{_PROJECT_ID}/workflow":
            return _WORKFLOW_TWO_STAGES
        if params and params.get("status") == "reviewing":
            return _TASKS_REVIEWING
        return _TASKS_EMPTY

    monkeypatch.setattr(server, "_api_get", fake_get)
    result = await server.tasks(
        action="get_my_tasks", project_id=_PROJECT_ID, status="reviewing"
    )
    assert "task-2" in result
    assert "task-1" not in result


@pytest.mark.asyncio
async def test_get_my_tasks_json_envelope(monkeypatch):
    """response_mode=json returns a valid JSON envelope with caller and task data."""
    async def fake_get(path, *, params=None, client=None):
        if path == "/api/auth/me":
            return _ME
        if path == f"/api/projects/{_PROJECT_ID}/workflow":
            return _WORKFLOW_TWO_STAGES
        if params and params.get("status") == "in_progress":
            return _TASKS_IN_PROGRESS
        return _TASKS_EMPTY

    monkeypatch.setattr(server, "_api_get", fake_get)
    raw = await server.tasks(
        action="get_my_tasks", project_id=_PROJECT_ID, response_mode="json"
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["meta"]["tool"] == "tasks.get_my_tasks"
    assert payload["data"]["caller_id"] == _CALLER_ID
    assert any(t["id"] == "task-1" for t in payload["data"]["tasks"])


@pytest.mark.asyncio
async def test_get_my_tasks_no_eligible_stages(monkeypatch):
    """Returns a clear message when the caller has no eligible stages."""
    workflow_all_claimed_by_other = {
        "statuses": [{"id": "s1", "name": "todo"}],
        "stages": [
            {
                "id": "stage-todo",
                "name": "To Do",
                "statuses": [{"id": "s1", "name": "todo"}],
                "claimed_agents": [{"id": "other-agent"}],
            }
        ],
    }

    async def fake_get(path, *, params=None, client=None):
        if path == "/api/auth/me":
            return _ME
        if path == f"/api/projects/{_PROJECT_ID}/workflow":
            return workflow_all_claimed_by_other
        return _TASKS_EMPTY

    monkeypatch.setattr(server, "_api_get", fake_get)
    result = await server.tasks(action="get_my_tasks", project_id=_PROJECT_ID)
    assert "No eligible stages" in result


@pytest.mark.asyncio
async def test_get_my_tasks_assigned_to_caller_included(monkeypatch):
    """Tasks already assigned to the caller are included in results."""
    tasks_with_caller_assigned = {
        "items": [
            {
                "id": "task-mine",
                "title": "Already mine",
                "status": "in_progress",
                "priority": None,
                "sort_order": 1,
                "assignee_id": _CALLER_ID,
                "project_id": _PROJECT_ID,
                "description": None,
                "estimate": None,
                "milestone_id": None,
                "created_at": "2026-03-16T00:00:00Z",
                "updated_at": "2026-03-16T00:00:00Z",
            },
            {
                "id": "task-other",
                "title": "Claimed by other",
                "status": "in_progress",
                "priority": None,
                "sort_order": 2,
                "assignee_id": "other-agent",
                "project_id": _PROJECT_ID,
                "description": None,
                "estimate": None,
                "milestone_id": None,
                "created_at": "2026-03-16T00:00:00Z",
                "updated_at": "2026-03-16T00:00:00Z",
            },
        ],
        "has_more": False,
        "next_cursor": None,
    }

    async def fake_get(path, *, params=None, client=None):
        if path == "/api/auth/me":
            return _ME
        if path == f"/api/projects/{_PROJECT_ID}/workflow":
            return _WORKFLOW_TWO_STAGES
        if params and params.get("status") == "in_progress":
            return tasks_with_caller_assigned
        return _TASKS_EMPTY

    monkeypatch.setattr(server, "_api_get", fake_get)
    result = await server.tasks(action="get_my_tasks", project_id=_PROJECT_ID)
    assert "task-mine" in result
    assert "task-other" not in result


@pytest.mark.asyncio
async def test_get_my_tasks_missing_project_id(monkeypatch):
    """Returns error when project_id is omitted."""
    result = await server.tasks(action="get_my_tasks")
    assert "project_id is required" in result
