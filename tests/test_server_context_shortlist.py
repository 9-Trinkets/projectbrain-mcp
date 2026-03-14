import pytest

import api_adapter  # noqa: F401
import server


@pytest.mark.asyncio
async def test_context_shortlist_returns_ranked_top_k():
    result = await server.context(
        action="shortlist",
        q="create and reorder milestones for roadmap",
        limit=3,
    )
    assert "Mode: shortlist" in result
    assert "Returned: 3 operation(s)" in result
    assert 'tasks(action="create_milestone", project_id, title, ...)' in result
    assert 'tasks(action="reorder_milestones", project_id, milestone_ids)' in result


@pytest.mark.asyncio
async def test_context_shortlist_requires_query():
    result = await server.context(action="shortlist")
    assert "requires field(s): q" in result


@pytest.mark.asyncio
async def test_context_shortlist_uses_fallback_when_no_matches():
    result = await server.context(action="shortlist", q="zzzz qqqq", limit=4)
    assert "Fallback used: yes" in result
    assert 'context(action="session", project_id)' in result
    assert 'tasks(action="list", project_id, status?, q?, q_any?, q_all?, q_not?)' in result


@pytest.mark.asyncio
async def test_context_shortlist_full_tool_mode_returns_all_operations():
    result = await server.context(
        action="shortlist",
        q="milestone updates",
        limit=1,
        full_tool_mode=True,
    )
    assert "Mode: full_tool_mode" in result
    assert "Returned: 1 operation(s)" not in result
    assert 'collaboration(action="send_message", recipient_id, body, ...)' in result
