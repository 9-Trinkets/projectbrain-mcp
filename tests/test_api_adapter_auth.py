import json

import jwt
import pytest
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from api_adapter import JWT_ALGORITHM, JWT_SECRET_KEY, MCPAuthMiddleware, current_auth_token


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


@pytest.mark.asyncio
async def test_tools_list_allowed_without_auth():
    app = MCPAuthMiddleware(_echo_jsonrpc_app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 200
    assert response.json()["method"] == "tools/list"
    assert response.json()["token"] is None


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
