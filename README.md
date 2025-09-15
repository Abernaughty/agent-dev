# Agent Development Environment

A secure, containerized development environment for AI agents to safely interact with Svelte + Azure Functions projects through MCP (Model Context Protocol) servers.

## Phase 1: Core Infrastructure ✅
## Phase 2: MCP Server Implementation ✅

### Project Structure
```
agent-dev/
├── docker-compose.yml          # Main orchestration file
├── containers/                 # Container definitions
│   ├── web/                   # Svelte development container
│   ├── func/                  # Azure Functions container
│   ├── mcp-fs/               # MCP Filesystem server
│   └── mcp-shell/            # MCP Shell server
├── workspace/                 # Your project code (mounted read-only)
└── config/                   # Configuration files
```

### Security Features
- **Read-only root filesystems** for all containers
- **Dropped capabilities** (`cap_drop: ["ALL"]`)
- **No new privileges** (`no-new-privileges: true`)
- **Isolated networks** (MCP servers have `network_mode: "none"`)
- **Memory and CPU limits**
- **Non-executable /tmp** mounts

### Services

#### Web Container
- **Purpose**: Svelte development with VS Code Server
- **Ports**: 3000 (dev server), 8080 (VS Code Server)
- **Features**: Auto-initializes Svelte projects, installs dependencies

#### Func Container
- **Purpose**: Azure Functions development
- **Port**: 7071 (Functions runtime)
- **Features**: Auto-initializes Functions projects, TypeScript support

#### Azurite Container
- **Purpose**: Local Azure Storage emulator
- **Ports**: 10000 (Blob), 10001 (Queue), 10002 (Table)

#### MCP Filesystem Server
- **Purpose**: Safe file operations within /workspace
- **Operations**: list, read, write, search, exists, mkdir
- **Security**: Path validation, workspace containment

#### MCP Shell Server
- **Purpose**: Allowlisted command execution
- **Commands**: npm, node, func, git, ls, cat, echo
- **Security**: Command validation, timeout protection

## Quick Start

1. **Build and start services**:
   ```bash
   docker-compose build
   docker-compose up -d web func azurite
   ```

2. **Access development environment**:
   - Svelte dev server: http://localhost:3000
   - VS Code Server: http://localhost:8080
   - Azure Functions: http://localhost:7071

3. **Test MCP servers**:
   ```bash
   make test-mcp     # Test both servers
   make test-fs      # Test filesystem server
   make test-shell   # Test shell server
   ```

## Next Steps

- **Phase 2**: Implement MCP server functionality
- **Phase 3**: Complete development environment setup
- **Phase 4**: Security hardening and agent integration

## Commands

### Development
```bash
# Start development environment
docker-compose up -d web func azurite

# View logs
docker-compose logs -f web

# Stop all services
docker-compose down
```

### Building
```bash
# Build all containers
docker-compose build

# Build specific container
docker-compose build web
```

### MCP Testing (Phase 2)
```bash
# Test filesystem server
docker-compose run --rm -i mcp-fs

# Test shell server  
docker-compose run --rm -i mcp-shell
```
