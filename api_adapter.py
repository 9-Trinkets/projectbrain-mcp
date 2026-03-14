import contextlib
import json
import os
import re
from contextvars import ContextVar
from typing import Any

import jwt
import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
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
MCP_UNAUTH_DISCOVERY_METHOD_SCAN_BYTES = int(os.getenv("MCP_UNAUTH_DISCOVERY_METHOD_SCAN_BYTES", "8192"))
SENTRY_DSN = os.getenv("SENTRY_DSN")
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.2"))
SENTRY_SEND_DEFAULT_PII = os.getenv("SENTRY_SEND_DEFAULT_PII", "false").lower() == "true"
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT")

current_auth_token: ContextVar[str | None] = ContextVar("current_auth_token", default=None)


def _init_sentry() -> None:
    if not SENTRY_DSN:
        return

    integrations: list[Any] = []
    with contextlib.suppress(Exception):
        from sentry_sdk.integrations.mcp import MCPIntegration

        integrations.append(MCPIntegration())

    init_kwargs: dict[str, Any] = {
        "dsn": SENTRY_DSN,
        "traces_sample_rate": SENTRY_TRACES_SAMPLE_RATE,
        "send_default_pii": SENTRY_SEND_DEFAULT_PII,
    }
    if SENTRY_ENVIRONMENT:
        init_kwargs["environment"] = SENTRY_ENVIRONMENT
    if integrations:
        init_kwargs["integrations"] = integrations

    sentry_sdk.init(**init_kwargs)


_init_sentry()


class MCPAuthMiddleware:
    """ASGI auth middleware for MCP transport requests."""

    _PUBLIC_METHODS = {
        "initialize",
        "notifications/initialized",
        "ping",
        "tools/list",
    }
    _METHOD_PATTERN = re.compile(rb'"method"\s*:\s*"([^"]+)"')

    def __init__(self, app):
        self.app = app
        self._method_scan_bytes = max(1024, MCP_UNAUTH_DISCOVERY_METHOD_SCAN_BYTES)

    def _parse_auth_header(self, scope: dict[str, Any]) -> str:
        headers = dict(scope.get("headers", []))
        return headers.get(b"authorization", b"").decode()

    def _parse_bearer_token(self, auth_header: str) -> str | None:
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:]
        if not token:
            return None
        return token

    def _verify_jwt(self, token: str) -> bool:
        try:
            jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return True
        except jwt.InvalidTokenError:
            return False

    def _validate_token(self, token: str | None) -> bool:
        if not token:
            return False
        return token.startswith("pb_") or self._verify_jwt(token)

    def _method_is_public(self, method: str | None) -> bool:
        return method in self._PUBLIC_METHODS

    def _build_unauthorized_response(self) -> JSONResponse:
        prm_url = f"{MCP_SERVER_URL}/.well-known/oauth-protected-resource"
        return JSONResponse(
            status_code=401,
            content={"error": "Missing or invalid authorization token"},
            headers={
                "WWW-Authenticate": (
                    f'Bearer realm="mcp", '
                    f'resource_metadata="{prm_url}"'
                )
            },
        )

    async def _extract_method_and_replay_receive(self, receive) -> tuple[str | None, Any, bytes]:
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
            return await receive()

        return method, replay_receive, bytes(scanned_body)

    async def _inspect_method_for_public_allowlist(self, receive) -> tuple[bool, Any]:
        method, replay_receive, _ = await self._extract_method_and_replay_receive(receive)
        return self._method_is_public(method), replay_receive

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth_header = self._parse_auth_header(scope)
            token = self._parse_bearer_token(auth_header)

            if self._validate_token(token):
                reset_token = current_auth_token.set(token)
                try:
                    await self.app(scope, receive, send)
                finally:
                    current_auth_token.reset(reset_token)
                return

            is_public_method, replay_receive = await self._inspect_method_for_public_allowlist(receive)
            if is_public_method:
                await self.app(scope, replay_receive, send)
                return

            response = self._build_unauthorized_response()
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


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /\n")
