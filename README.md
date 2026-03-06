# Agent Development Environment

A secure, containerized development environment for AI agents to safely interact with
Svelte + Azure Functions projects through MCP (Model Context Protocol) servers.

## Status

- Phase 1: Core Infrastructure ✅
- Phase 2: MCP Server Implementation ✅

## Project Structure

```
agent-dev/
├── docker-compose.yml          # Main orchestration file
├── containers/                 # Container definitions
│   ├── web/                   # Svelte development container
│   ├── func/                  # Azure Functions container
│   ├── mcp-fs/               # MCP Filesystem server
│   └── mcp-shell/            # MCP Shell server
├── workspace/                 # Your project code (mounted read-only)
├── tools/                     # Utility scripts and agents
└── config/                   # Configuration files
```

## Security Features

All containers are hardened with:

- **Read-only root filesystems**
- **Dropped capabilities** (`cap_drop: ["ALL"]`)
- **No new privileges** (`no-new-privileges: true`)
- **Isolated networks** — MCP servers run with `network_mode: "none"`
- **Memory and CPU limits**
- **Non-executable /tmp** mounts

## Services

### Web Container
- **Purpose**: Svelte development with VS Code Server
- **Ports**: 3000 (dev server), 8080 (VS Code Server)
- **Features**: Auto-initializes Svelte projects, installs dependencies

### Func Container
- **Purpose**: Azure Functions development
- **Port**: 7071 (Functions runtime)
- **Features**: Auto-initializes Functions projects, TypeScript support

### Azurite Container
- **Purpose**: Local Azure Storage emulator
- **Ports**: 10000 (Blob), 10001 (Queue), 10002 (Table)

### MCP Filesystem Server
- **Purpose**: Safe file operations within /workspace
- **Operations**: list, read, write, search, exists, mkdir
- **Security**: Path validation, workspace containment

### MCP Shell Server
- **Purpose**: Allowlisted command execution
- **Commands**: npm, node, func, git, ls, cat, echo
- **Security**: Command validation, timeout protection

## Quick Start

```bash
# Build and start services
docker-compose build
docker-compose up -d web func azurite

# Access the environment
# Svelte dev server:    http://localhost:3000
# VS Code Server:       http://localhost:8080
# Azure Functions:      http://localhost:7071

# Test MCP servers
make test-mcp     # Test both servers
make test-fs      # Test filesystem server
make test-shell   # Test shell server
```

## Commands

```bash
# Start / stop
docker-compose up -d web func azurite
docker-compose down

# Logs
docker-compose logs -f web

# Build
docker-compose build
docker-compose build web    # specific container

# MCP testing
docker-compose run --rm -i mcp-fs
docker-compose run --rm -i mcp-shell
```

## Related Projects

- [langchain-price-agent](https://github.com/Abernaughty/langchain-price-agent) — LangChain/LangGraph
  agent experiments designed to run in environments like this one
- [code-vector-sync](https://github.com/Abernaughty/code-vector-sync) — MCP server for semantic
  code search (companion tool)
