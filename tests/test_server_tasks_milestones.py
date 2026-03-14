import json

import pytest

import api_adapter  # noqa: F401
import server


@pytest.mark.asyncio
async def test_list_milestones_human_response(monkeypatch):
    async def fake_get(path, *, params=None, client=None):
        assert path == "/api/projects/project-1/milestones"
        assert params == {"q": "mcp"}
        return [
            {
                "id": "milestone-1",
                "project_id": "project-1",
                "title": "M13",
                "description": "Milestone description",
                "due_date": "2026-04-01",
                "status": "planned",
                "position": 1,
                "created_at": "2026-03-14T00:00:00Z",
                "updated_at": "2026-03-14T00:00:00Z",
            }
        ]

    monkeypatch.setattr(server, "_api_get", fake_get)
    result = await server.tasks(action="list_milestones", project_id="project-1", q="mcp")
    assert "Milestones (1)" in result
    assert "[planned] M13 (due 2026-04-01) (ID: milestone-1)" in result


@pytest.mark.asyncio
async def test_list_milestones_json_response(monkeypatch):
    async def fake_get(path, *, params=None, client=None):
        assert path == "/api/projects/project-1/milestones"
        assert params == {"q": None}
        return [
            {
                "id": "milestone-1",
                "project_id": "project-1",
                "title": "M13",
                "description": None,
                "due_date": None,
                "status": "planned",
                "position": 1,
                "created_at": "2026-03-14T00:00:00Z",
                "updated_at": "2026-03-14T00:00:00Z",
            }
        ]

    monkeypatch.setattr(server, "_api_get", fake_get)
    raw = await server.tasks(action="list_milestones", project_id="project-1", response_mode="json")
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["meta"]["tool"] == "tasks.list_milestones"
    assert payload["data"]["items"][0]["id"] == "milestone-1"


@pytest.mark.asyncio
async def test_create_milestone_rejects_invalid_status():
    result = await server.tasks(
        action="create_milestone",
        project_id="project-1",
        title="M13",
        status="todo",
    )
    assert "Invalid status" in result
    assert "planned" in result


@pytest.mark.asyncio
async def test_create_milestone_calls_expected_endpoint(monkeypatch):
    called = {}

    async def fake_post(path, *, body=None, client=None):
        called["path"] = path
        called["body"] = body
        return {
            "id": "milestone-1",
            "project_id": "project-1",
            "title": body["title"],
            "description": body.get("description"),
            "due_date": body.get("due_date"),
            "status": body["status"],
            "position": 1,
            "created_at": "2026-03-14T00:00:00Z",
            "updated_at": "2026-03-14T00:00:00Z",
        }

    monkeypatch.setattr(server, "_api_post", fake_post)
    result = await server.tasks(
        action="create_milestone",
        project_id="project-1",
        title="M13",
        description="MCP improvements",
        due_date="2026-04-01",
        status="planned",
    )
    assert called["path"] == "/api/projects/project-1/milestones"
    assert called["body"] == {
        "title": "M13",
        "status": "planned",
        "description": "MCP improvements",
        "due_date": "2026-04-01",
    }
    assert "Milestone created: M13 [planned] (ID: milestone-1)" in result


@pytest.mark.asyncio
async def test_update_milestone_requires_mutable_fields():
    result = await server.tasks(action="update_milestone", milestone_id="milestone-1")
    assert "requires at least one mutable field" in result


@pytest.mark.asyncio
async def test_reorder_milestones_requires_non_empty_ids():
    result = await server.tasks(action="reorder_milestones", project_id="project-1", milestone_ids=[])
    assert "requires non-empty milestone_ids" in result


@pytest.mark.asyncio
async def test_reorder_milestones_calls_expected_endpoint(monkeypatch):
    called = {}

    async def fake_post(path, *, body=None, client=None):
        called["path"] = path
        called["body"] = body
        return {"ok": True}

    monkeypatch.setattr(server, "_api_post", fake_post)
    result = await server.tasks(
        action="reorder_milestones",
        project_id="project-1",
        milestone_ids=["milestone-2", "milestone-1"],
    )
    assert called["path"] == "/api/projects/project-1/milestones/reorder"
    assert called["body"] == {"milestone_ids": ["milestone-2", "milestone-1"]}
    assert "Milestones reordered (2 IDs) for project project-1." == result
