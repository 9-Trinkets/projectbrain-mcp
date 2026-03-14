import asyncio
import contextlib
import json
import os
import re
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from typing import Any

import jwt
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.routing import Route

from runtime import MCPRuntimeConfig, MCPSettings, configure_runtime


def _parse_cors_origins(value: str | None) -> list[str]:
    if not value:
        return ["http://localhost:5173"]
    raw = value.strip()
    if not raw:
        return ["http://localhost:5173"]
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(origin).strip() for origin in parsed if str(origin).strip()]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", SERVER_URL)
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
CORS_ORIGINS = _parse_cors_origins(os.getenv("CORS_ORIGINS"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("MCP_REQUEST_TIMEOUT_SECONDS", "30"))
MCP_UNAUTH_DISCOVERY_RATE_LIMIT_PER_MINUTE = int(os.getenv("MCP_UNAUTH_DISCOVERY_RATE_LIMIT_PER_MINUTE", "60"))
MCP_UNAUTH_DISCOVERY_METHOD_SCAN_BYTES = int(os.getenv("MCP_UNAUTH_DISCOVERY_METHOD_SCAN_BYTES", "8192"))

current_auth_token: ContextVar[str | None] = ContextVar("current_auth_token", default=None)


class MCPAuthMiddleware:
    """ASGI auth middleware for MCP transport requests."""
    _PUBLIC_METHODS = {
        "initialize",
        "notifications/initialized",
        "ping",
        "tools/list",
    }
    _METHOD_PATTERN = re.compile(rb'"method"\s*:\s*"([^"]+)"')
    _RATE_WINDOW_SECONDS = 60.0

    def __init__(self, app):
        self.app = app
        self._rate_limit_per_minute = max(1, MCP_UNAUTH_DISCOVERY_RATE_LIMIT_PER_MINUTE)
        self._method_scan_bytes = max(1024, MCP_UNAUTH_DISCOVERY_METHOD_SCAN_BYTES)
        self._rate_state: defaultdict[str, deque[float]] = defaultdict(deque)
        self._rate_lock = asyncio.Lock()

    def _verify_jwt(self, token: str) -> bool:
        try:
            jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return True
        except jwt.InvalidTokenError:
            return False

    async def _extract_method_and_replay_receive(self, receive) -> tuple[str | None, Any]:
        buffered_messages: list[dict[str, Any]] = []
        scanned_body = bytearray()

        while True:
            message = await receive()
            buffered_messages.append(message)
            if message.get("type") != "http.request":
                if message.get("type") == "http.disconnect":
                    break
                continue
            chunk = message.get("body", b"")
            if chunk and len(scanned_body) < self._method_scan_bytes:
                remaining = self._method_scan_bytes - len(scanned_body)
                scanned_body.extend(chunk[:remaining])
            if not message.get("more_body", False):
                break

        method: str | None = None
        if scanned_body:
            match = self._METHOD_PATTERN.search(bytes(scanned_body))
            if match:
                with contextlib.suppress(UnicodeDecodeError):
                    method = match.group(1).decode()

        async def replay_receive():
            if buffered_messages:
                return buffered_messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        return method, replay_receive

    def _client_id(self, scope, headers: dict[bytes, bytes]) -> str:
        forwarded_for = headers.get(b"x-forwarded-for", b"").decode().strip()
        if forwarded_for:
            first_hop = forwarded_for.split(",")[0].strip()
            if first_hop:
                return first_hop
        client = scope.get("client")
        if isinstance(client, (list, tuple)) and client:
            return str(client[0])
        return "unknown"

    async def _is_rate_limited(self, client_id: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._RATE_WINDOW_SECONDS
        async with self._rate_lock:
            bucket = self._rate_state[client_id]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._rate_limit_per_minute:
                return True
            bucket.append(now)
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()

            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                if token.startswith("pb_") or self._verify_jwt(token):
                    reset_token = current_auth_token.set(token)
                    try:
                        await self.app(scope, receive, send)
                    finally:
                        current_auth_token.reset(reset_token)
                    return
            method, replay_receive = await self._extract_method_and_replay_receive(receive)
            if method in self._PUBLIC_METHODS:
                client_id = self._client_id(scope, headers)
                if await self._is_rate_limited(client_id):
                    response = JSONResponse(
                        status_code=429,
                        content={"error": "Rate limit exceeded for unauthenticated discovery requests"},
                        headers={"Retry-After": "60"},
                    )
                    await response(scope, replay_receive, send)
                    return
                await self.app(scope, replay_receive, send)
                return

            prm_url = f"{MCP_SERVER_URL}/.well-known/oauth-protected-resource"
            response = JSONResponse(
                status_code=401,
                content={"error": "Missing or invalid authorization token"},
                headers={
                    "WWW-Authenticate": (
                        f'Bearer realm="mcp", '
                        f'resource_metadata="{prm_url}"'
                    )
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


configure_runtime(
    MCPRuntimeConfig(
        settings=MCPSettings(
            api_base_url=SERVER_URL,
            mcp_server_url=MCP_SERVER_URL,
            cors_origins=CORS_ORIGINS,
            request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        ),
        auth_token=current_auth_token,
    )
)

from server import mcp_server  # noqa: E402

MCP_RESOURCE = MCP_SERVER_URL


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(title="Project Brain MCP", lifespan=lifespan, redirect_slashes=False)


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def oauth_protected_resource():
    return JSONResponse({
        "resource": MCP_RESOURCE,
        "authorization_servers": [SERVER_URL],
        "scopes_supported": ["mcp"],
    })


@app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def oauth_authorization_server():
    return JSONResponse({
        "issuer": SERVER_URL,
        "authorization_endpoint": f"{SERVER_URL}/api/oauth/authorize",
        "token_endpoint": f"{SERVER_URL}/api/oauth/token",
        "registration_endpoint": f"{SERVER_URL}/api/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
    })


mcp_app = mcp_server.streamable_http_app()
mcp_endpoint = next((route.endpoint for route in mcp_app.routes if getattr(route, "path", None) == "/mcp"), None)
if mcp_endpoint is None:
    raise RuntimeError("Unable to locate MCP /mcp endpoint in streamable HTTP app.")
protected_mcp_endpoint = MCPAuthMiddleware(mcp_endpoint)
app.router.routes.append(Route("/", endpoint=protected_mcp_endpoint, include_in_schema=False))


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}
