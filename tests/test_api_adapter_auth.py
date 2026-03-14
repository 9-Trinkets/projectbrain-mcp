import asyncio
import json

import jwt
import pytest
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from api_adapter import JWT_ALGORITHM, JWT_SECRET_KEY, MCPAuthMiddleware, current_auth_token

class _FakeTool:
    def __init__(self, name: str):
        self._name = name

    def model_dump(self, by_alias: bool = True, exclude_none: bool = True) -> dict:
        return {"name": self._name, "description": f"{self._name} tool", "inputSchema": {"type": "object"}}


class _FakeMCPServer:
    def __init__(self):
        self.calls = 0

    async def list_tools(self):
        self.calls += 1
        return [_FakeTool("alpha"), _FakeTool("beta")]


class _SlowFakeMCPServer:
    def __init__(self):
        self.calls = 0

    async def list_tools(self):
        self.calls += 1
        await asyncio.sleep(0.1)
        return [_FakeTool("alpha"), _FakeTool("beta")]


async def _echo_jsonrpc_app(scope, receive, send):
    assert scope["type"] == "http"
    body_chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            continue
        body_chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    payload = json.loads(b"".join(body_chunks) or "{}")
    response = JSONResponse(
        {
            "method": payload.get("method"),
            "token": current_auth_token.get(),
        }
    )
    await response(scope, receive, send)


async def _sleepy_echo_jsonrpc_app(scope, receive, send):
    assert scope["type"] == "http"
    body_chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            continue
        body_chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    payload = json.loads(b"".join(body_chunks) or "{}")
    await asyncio.sleep(0.1)
    response = JSONResponse({"method": payload.get("method")})
    await response(scope, receive, send)


@pytest.mark.asyncio
async def test_tools_list_allowed_without_auth():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    app._mcp_server = _FakeMCPServer()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    assert len(payload["result"]["tools"]) == 2


@pytest.mark.asyncio
async def test_tools_list_uses_cache_for_unauthenticated_requests():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    fake_server = _FakeMCPServer()
    app._mcp_server = fake_server
    app._tools_list_cache_ttl_seconds = 600

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        second = await client.post("/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert fake_server.calls == 1
    assert first.json()["id"] == 1
    assert second.json()["id"] == 2


@pytest.mark.asyncio
async def test_tools_call_denied_without_auth():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
    assert response.status_code == 401
    assert response.json()["error"] == "Missing or invalid authorization token"
    assert "WWW-Authenticate" in response.headers


@pytest.mark.asyncio
async def test_tools_call_allowed_with_valid_jwt():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    token = jwt.encode({"sub": "test-user"}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["method"] == "tools/call"
    assert response.json()["token"] == token


@pytest.mark.asyncio
async def test_unauthenticated_discovery_rate_limited():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    app._mcp_server = _FakeMCPServer()
    app._rate_limit_per_minute = 2

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        second = await client.post("/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        third = await client.post("/", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"] == "Rate limit exceeded for unauthenticated discovery requests"
    assert third.headers.get("Retry-After") == "60"


@pytest.mark.asyncio
async def test_authenticated_requests_not_affected_by_unauth_discovery_rate_limit():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    app._mcp_server = _FakeMCPServer()
    app._rate_limit_per_minute = 1
    token = jwt.encode({"sub": "test-user"}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauth = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        rate_limited = await client.post("/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        authed = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/call"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert unauth.status_code == 200
    assert rate_limited.status_code == 429
    assert authed.status_code == 200


@pytest.mark.asyncio
async def test_global_unauthenticated_discovery_rate_limited():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    app._mcp_server = _FakeMCPServer()
    app._rate_limit_per_minute = 999
    app._global_rate_limit_per_minute = 2

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"x-forwarded-for": "1.1.1.1"},
        )
        second = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={"x-forwarded-for": "2.2.2.2"},
        )
        third = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            headers={"x-forwarded-for": "3.3.3.3"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"] == "Global rate limit exceeded for unauthenticated discovery requests"
    assert third.headers.get("Retry-After") == "60"


@pytest.mark.asyncio
async def test_unauthenticated_discovery_concurrency_throttled():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    app._mcp_server = _SlowFakeMCPServer()
    app._rate_limit_per_minute = 999
    app._global_rate_limit_per_minute = 999
    app._discovery_semaphore = asyncio.Semaphore(1)
    app._discovery_acquire_wait_seconds = 0.01

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(
            client.post(
                "/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"x-forwarded-for": "1.1.1.1"},
            ),
            client.post(
                "/",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers={"x-forwarded-for": "2.2.2.2"},
            ),
        )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 429]
    throttled = next(response for response in responses if response.status_code == 429)
    assert throttled.json()["error"] == "Too many concurrent unauthenticated discovery requests"
    assert throttled.headers.get("Retry-After") == "1"
