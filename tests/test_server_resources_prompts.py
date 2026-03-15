import json

import pytest

import api_adapter  # noqa: F401
import server


@pytest.mark.asyncio
async def test_list_resources_contains_registered_project_brain_resources():
    resources = await server.mcp_server.list_resources()
    by_uri = {str(resource.uri): resource for resource in resources}

    assert "projectbrain://server/overview" in by_uri
    assert "projectbrain://playbooks/default-workflow" in by_uri
    assert by_uri["projectbrain://server/overview"].mimeType == "application/json"
    assert by_uri["projectbrain://playbooks/default-workflow"].mimeType == "text/plain"


@pytest.mark.asyncio
async def test_read_server_overview_resource_returns_expected_payload():
    contents = await server.mcp_server.read_resource("projectbrain://server/overview")

    assert len(contents) == 1
    payload = json.loads(contents[0].content)
    assert payload["name"] == "ProjectBrain"
    assert payload["transport"] == "streamable-http"
    assert "resources/list" in payload["discovery_methods"]
    assert "prompts/list" in payload["discovery_methods"]
    assert payload["tools"] == ["context", "projects", "tasks", "knowledge", "collaboration"]


@pytest.mark.asyncio
async def test_list_prompts_contains_registered_project_brain_prompts():
    prompts = await server.mcp_server.list_prompts()
    prompt_names = {prompt.name for prompt in prompts}

    assert "project_brain_session_bootstrap" in prompt_names
    assert "project_brain_task_execution" in prompt_names


@pytest.mark.asyncio
async def test_task_execution_prompt_renders_project_and_task_context():
    result = await server.mcp_server.get_prompt(
        "project_brain_task_execution",
        {"task_id": "task-123", "project_id": "project-abc"},
    )

    assert len(result.messages) == 1
    text = result.messages[0].content.text
    assert "- task_id: task-123" in text
    assert "- project_id: project-abc" in text
    assert "tasks(action=\"context\", task_id=task_id)" in text
