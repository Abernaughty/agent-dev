# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A secure, containerized development environment for AI agents and general development workflows. It uses defense-in-depth security with isolated containers, read-only filesystems, and allowlisted command execution via MCP (Model Context Protocol) servers.

## Commands

### Development Environment

```bash
# Build all containers
make build

# Start development environment (dev)
make up

# Stop all services
make down

# View logs from all services
make logs

# Clean up everything (containers, volumes, images)
make clean
```

### Container Access

```bash
# Open shell in dev container
make shell-dev
```

### MCP Server Testing

```bash
# Test both MCP servers
make test-mcp

# Test filesystem server only
make test-fs

# Test shell server only
make test-shell
```

## Architecture

### Container Structure

The project uses Docker Compose to orchestrate two service types:

1. **Development Container** (dev)
   - Connected via `dev-network`
   - Network access for development tools
   - Relaxed security constraints for workflow needs

2. **MCP Servers** (mcp-fs, mcp-shell)
   - Network-isolated (`network_mode: "none"`)
   - Strict security constraints (read-only root, dropped capabilities)
   - Communicate via stdio only (JSON-RPC 2.0)
   - Resource-limited (256MB RAM, 0.25 CPU)

### Security Model

All containers implement a security baseline defined in `docker-compose.yml`:

```yaml
x-security-base:
  read_only: true
  cap_drop: ["ALL"]
  security_opt: ["no-new-privileges:true"]
  tmpfs: ["/tmp:noexec,nosuid,size=100m"]
  mem_limit: 512m
  cpus: 0.5
```

The dev container overrides some restrictions for workflow needs (e.g., `read_only: false`), while MCP servers maintain strict security.

### MCP Protocol

Both MCP servers implement JSON-RPC 2.0 over stdio:

**Filesystem Server** (`mcp-fs`):
- Methods: `fs/list`, `fs/read`, `fs/write`, `fs/search`, `fs/exists`, `fs/mkdir`
- All operations restricted to `/workspace` directory
- Path traversal prevention via validation

**Shell Server** (`mcp-shell`):
- Methods: `shell/exec`, `shell/allowed`
- Allowlisted commands: npm, node, git, ls, cat, echo
- 30-second timeout protection
- Argument validation against allowlists

See `docs/mcp-filesystem-server.md` and `docs/mcp-shell-server.md` for complete API documentation.

### Workspace Mount Pattern

The `/workspace` directory is mounted from `./workspace`:
- **MCP servers**: Read-only mount for security (filesystem server) and read-write for shell where needed
- **Development container**: Read-write for active development

## File Structure

```
agent-dev/
├── containers/          # Container definitions
│   ├── dev/            # General dev container
│   ├── mcp-fs/         # MCP Filesystem server
│   └── mcp-shell/      # MCP Shell server
├── workspace/          # Project workspace (your code)
├── config/             # Configuration files
│   └── claude-desktop.json  # MCP server config for Claude Desktop
├── docs/               # Documentation
├── memory-bank/        # Project context and patterns
├── Tools/              # Git commit message agent
└── docker-compose.yml  # Main orchestration file
```

## Tools

### Git Commit Agent

Located in `Tools/git_commit_agent.py` - an AI-powered commit message generator using Claude.

**Usage:**
```bash
# Generate message for staged changes
python Tools/git_commit_agent.py --staged

# Generate and auto-commit
python Tools/git_commit_agent.py --staged --auto-commit

# Scan branches with unpushed commits
python Tools/git_commit_agent.py

# Process specific branch
python Tools/git_commit_agent.py --branch feature/name
```

**Requirements:**
- Set `ANTHROPIC_API_KEY` environment variable
- `pip install -r Tools/requirements.txt`

Generates Conventional Commits format messages using Claude Sonnet 4.5.

## Configuration

### Claude Desktop Integration

To use MCP servers with Claude Desktop, copy `config/claude-desktop.json` to your Claude config directory and update paths as needed.

## Memory Bank

The `memory-bank/` directory contains project context:
- `projectbrief.md`: Core requirements and goals
- `systemPatterns.md`: Architecture diagrams and patterns
- `techContext.md`: Technology stack details
- `activeContext.md`: Current development focus
- `progress.md`: Phase completion tracking

These files provide context for AI agents and developers about the project's architecture and progress.
