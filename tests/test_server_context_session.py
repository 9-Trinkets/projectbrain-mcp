from unittest.mock import AsyncMock, patch

import pytest

import api_adapter  # noqa: F401 — configures the MCP runtime before server is imported
import server


@pytest.mark.asyncio
async def test_context_session_semantic_params():
    # Arrange
    project_id = "proj-123"
    task_id = "task-456"
    intent = "fix bug"
    knowledge_limit = 3

    # To isolate the test, we patch the internal fetch function and resolve_project_id
    with patch("actions.context_actions._fetch_context_session_data", new_callable=AsyncMock) as mock_fetch, \
         patch("server._resolve_project_id", new_callable=AsyncMock, return_value=project_id):
        mock_fetch.return_value = {
            "project": {"name": "Test Project", "description": "A test project"},
            "in_progress": [],
            "todo": [],
            "decisions": [],
            "members": [],
            "facts": [],
            "skills": [],
            "knowledge": [],
        }

        # Act
        result = await server.context(
            action="session",
            project_id=project_id,
            task_id=task_id,
            intent=intent,
            knowledge_limit=knowledge_limit,
        )

        # Assert
        # Verify that our patched function was called with the correct params
        mock_fetch.assert_called_once()
        call_args, call_kwargs = mock_fetch.call_args
        assert call_kwargs.get("project_id") == project_id
        assert call_kwargs.get("task_id") == task_id
        assert call_kwargs.get("intent") == intent
        assert call_kwargs.get("knowledge_limit") == knowledge_limit

        # Verify that the rendering logic produced a non-empty result
        assert isinstance(result, str)
        assert "Test Project" in result
        assert "In-Progress Tasks" in result
