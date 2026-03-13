# Open Source Policy (MCP Subtree)
This document defines how MCP server code is published from this monorepo into a public repository.

## Repository boundary
- Public repo includes only the `mcp/` subtree.
- Private product code (full API app, frontend app, internal infra files) must not be copied into the public MCP repo.

## Content boundary
- Public MCP repo may include server implementation, runtime contracts, and public setup docs.
- Do not publish secrets, private tokens, internal-only endpoints, or non-public operations docs.

## Licensing boundary
Before first public release, add explicit licensing in the public MCP repo:
- Source code license (recommended: MIT).

If a different license is chosen, document it in that repo README and include one canonical `LICENSE` file.

## Release checklist
- Public repo created and visibility set to public.
- README includes setup and scope.
- License file added and verified.
- Automated sync configured and tested.
