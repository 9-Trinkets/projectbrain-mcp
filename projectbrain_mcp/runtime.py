from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class MCPSettings:
    server_url: str
    mcp_server_url: str
    cors_origins: list[str]


@dataclass(frozen=True)
class MCPModels:
    AuditLog: Any
    Decision: Any
    Fact: Any
    Milestone: Any
    Project: Any
    Skill: Any
    Task: Any
    TaskDependency: Any
    TeamInvite: Any
    User: Any
    A2AMessage: Any
    TaskComment: Any


@dataclass(frozen=True)
class MCPRuntimeConfig:
    settings: MCPSettings
    async_session: Any
    current_user_id: ContextVar[str | None]
    current_team_id: ContextVar[str | None]
    log_audit: Callable[..., Awaitable[None]]
    apply_cursor_pagination: Callable[..., tuple[Any, int]]
    paginate_results: Callable[..., tuple[list[Any], str | None, bool]]
    models: MCPModels


_runtime_config: MCPRuntimeConfig | None = None


def configure_runtime(config: MCPRuntimeConfig) -> None:
    global _runtime_config
    _runtime_config = config


def get_runtime() -> MCPRuntimeConfig:
    if _runtime_config is None:
        raise RuntimeError(
            "projectbrain_mcp runtime not configured. "
            "Call configure_runtime(...) before importing projectbrain_mcp.server."
        )
    return _runtime_config
