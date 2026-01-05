# Tech Context

## Stack
- **Dev Container Base**: `node:20-bookworm-slim`
- **Core Tools**: git, node, python3, jq, ripgrep
- **MCP Servers**: Node.js services for filesystem and shell
- **Orchestration**: Docker Compose

## Security Model
- Read-only root filesystems (baseline)
- Dropped Linux capabilities
- No new privileges
- Network isolation for MCP servers
- Resource limits for all containers

## Ports
- No default ports exposed by the dev container
- MCP servers use stdio (no ports)

## Workspace
- `/workspace` mounted from host `./workspace`
- Dev container uses read-write mount
- MCP filesystem server uses read-only mount
- MCP shell server uses read-write mount for command execution
