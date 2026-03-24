from typing import Optional


class MCPError(Exception):
    """Base exception for all MCP-related errors."""

    def __init__(self, message: str, status_code: int = 500, error_type: str = "mcp_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type


class APIError(MCPError):
    """Raised for errors returned from the backing API."""

    def __init__(self, message: str, status_code: int = 500, error_type: str = "api_error"):
        super().__init__(message, status_code, error_type)


class ValidationError(MCPError):
    """Raised for input validation errors."""

    def __init__(self, message: str, field_name: Optional[str] = None, status_code: int = 400, error_type: str = "validation_error"):
        super().__init__(message, status_code, error_type)
        self.field_name = field_name

