# Project Brain MCP Server (subtree)
This directory contains the MCP server code intended to be published to a standalone public repository while still being developed inside this monorepo.

## Scope
- In scope: MCP server implementation, runtime adapters/contracts, package-facing documentation.
- Out of scope: private API app internals, secrets, and internal-only docs.

## Publishing model (monorepo → public MCP repo)
Use a snapshot sync from repository root:

1. Add public remote (once):
```bash
git remote add mcp-public git@github.com:<org>/projectbrain-mcp.git
```

2. Publish current `mcp/` snapshot:
```bash
bash scripts/sync-mcp-public.sh mcp-public main
```

This script clones the public repo branch, replaces its contents with `mcp/`, commits, and pushes.

## Current layout
- `projectbrain_mcp/server.py` — MCP tool/server implementation
- `projectbrain_mcp/runtime.py` — runtime dependency contract injected by the host app
- `projectbrain_mcp/__init__.py` — package exports

## Integration in this monorepo
`api/app/mcp_server.py` is a thin adapter that wires app dependencies into `projectbrain_mcp.runtime` and exposes `mcp_server`.

## Next steps
- Add release packaging/versioning metadata in the public MCP repository.
- Publish npm package from that repository (tracked separately).
