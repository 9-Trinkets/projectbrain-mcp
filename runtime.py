from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class MCPSettings:
    api_base_url: str
    mcp_server_url: str
    cors_origins: list[str]
    request_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class MCPRuntimeConfig:
    settings: MCPSettings
    auth_token: ContextVar[str | None]


_runtime_config: MCPRuntimeConfig | None = None


def configure_runtime(config: MCPRuntimeConfig) -> None:
    global _runtime_config
    _runtime_config = config


def get_runtime() -> MCPRuntimeConfig:
    if _runtime_config is None:
        raise RuntimeError(
            "mcp runtime not configured. "
            "Call configure_runtime(...) before importing server."
        )
    return _runtime_config
