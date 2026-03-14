import contextlib
import json
import os
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

current_auth_token: ContextVar[str | None] = ContextVar("current_auth_token", default=None)


class MCPAuthMiddleware:
    """ASGI auth middleware for MCP transport requests."""

    def __init__(self, app):
        self.app = app

    def _verify_jwt(self, token: str) -> bool:
        try:
            jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return True
        except jwt.InvalidTokenError:
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
