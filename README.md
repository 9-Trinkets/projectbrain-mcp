# Project Brain MCP
Project Brain MCP is a Model Context Protocol (MCP) server for project planning and execution workflows.
It exposes tools for tasks, decisions, facts, milestones, comments, and team messaging through a Streamable HTTP endpoint.

## What it does
- Serves MCP tools over HTTPS at `https://mcp.projectbrain.tools`
- Allows unauthenticated MCP discovery requests for `initialize`, `notifications/initialized`, `ping`, and `tools/list`
- Authenticates bearer tokens (JWT or API key) for all tool execution and data access
- Provides MCP OAuth metadata endpoints
- Executes tool actions against the Project Brain API

## Service endpoints
- `GET /health`
- `GET /.well-known/oauth-protected-resource`
- `GET /.well-known/oauth-authorization-server`
- `POST /`

## Configuration
Set these environment variables:

- `SERVER_URL` (default: `http://localhost:8000`)  
  Base URL of the Project Brain API.
- `MCP_SERVER_URL` (default: same as `SERVER_URL`)  
  Public base URL used in OAuth resource metadata.
- `JWT_SECRET_KEY`  
  Secret used to validate JWT bearer tokens.
- `JWT_ALGORITHM` (default: `HS256`)
- `CORS_ORIGINS` (default: `["http://localhost:5173"]`)  
  Accepts either a JSON array or a comma-separated list.
- `MCP_REQUEST_TIMEOUT_SECONDS` (default: `30`)

## Local development
From repository root:

1. Install backend dependencies:
   - `cd api && uv sync --locked`
2. Start the MCP server:
   - `PYTHONPATH=mcp api/.venv/bin/uvicorn api_adapter:app --app-dir mcp --host 0.0.0.0 --port 8001`

## Directory structure
- `api_adapter.py` — FastAPI app entrypoint and auth middleware
- `server.py` — MCP tool definitions and HTTP client adapter
- `runtime.py` — runtime config and request-scoped auth context
