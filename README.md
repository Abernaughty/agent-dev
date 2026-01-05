# Agent Development Environment

A secure, containerized development environment for AI agents and general-purpose development workflows, backed by MCP (Model Context Protocol) servers for safe file and command access.

## Phase 1: Core Infrastructure ✅
## Phase 2: MCP Server Implementation ✅

### Project Structure
```
agent-dev/
├── docker-compose.yml          # Main orchestration file
├── containers/                 # Container definitions
│   ├── dev/                   # General development container
│   ├── mcp-fs/               # MCP Filesystem server
│   └── mcp-shell/            # MCP Shell server
├── workspace/                 # Your project code (mounted read-write)
└── config/                   # Configuration files
```

### Security Features
- **Read-only root filesystems** for containers by default
- **Dropped capabilities** (`cap_drop: ["ALL"]`)
- **No new privileges** (`no-new-privileges: true`)
- **Isolated networks** (MCP servers have `network_mode: "none"`)
- **Memory and CPU limits**
- **Non-executable /tmp** mounts

### Services

#### Dev Container
- **Purpose**: General development environment for agent workflows
- **Features**: Common tooling (git, node, python, jq, ripgrep)

#### MCP Filesystem Server
- **Purpose**: Safe file operations within /workspace
- **Operations**: list, read, write, search, exists, mkdir
- **Security**: Path validation, workspace containment

#### MCP Shell Server
- **Purpose**: Allowlisted command execution
- **Commands**: npm, node, git, ls, cat, echo
- **Security**: Command validation, timeout protection

## Quick Start

1. **Build and start services**:
   ```bash
   docker-compose build
   docker-compose up -d dev
   ```

2. **Test MCP servers**:
   ```bash
   make test-mcp     # Test both servers
   make test-fs      # Test filesystem server
   make test-shell   # Test shell server
   ```

## Commands

### Development
```bash
# Start development environment
docker-compose up -d dev

# View logs
docker-compose logs -f dev

# Stop all services
docker-compose down
```

### Building
```bash
# Build all containers
docker-compose build

# Build specific container
docker-compose build dev
```

### MCP Testing
```bash
# Test filesystem server
docker-compose run --rm -i mcp-fs

# Test shell server
docker-compose run --rm -i mcp-shell
```
