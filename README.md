# Agent Development Environment

A secure, containerized development environment for AI agents and general-purpose development workflows, backed by MCP (Model Context Protocol) servers for safe file and command access.

## What's Inside

| Component | Description |
|---|---|
| [MCP Servers](#mcp-servers) | Containerized filesystem and shell servers for safe AI agent access |
| [Git Commit Agent](./tools/README.md) | AI-powered tool that generates conventional commit messages using Claude |
| [Dev Container](#dev-container) | General-purpose development environment with common tooling |

## Project Structure

```
agent-dev/
├── docker-compose.yml          # Main orchestration file
├── containers/                 # Container definitions
│   ├── dev/                   # General development container
│   ├── mcp-fs/               # MCP Filesystem server
│   └── mcp-shell/            # MCP Shell server
├── tools/                     # Standalone AI-powered developer tools
│   ├── git_commit_agent.py    # Automated commit message generator
│   ├── requirements.txt
│   └── README.md
├── workspace/                 # Project code (mounted read-write into containers)
├── config/                    # Configuration files
├── docs/                      # MCP server documentation
└── memory-bank/               # AI context files (see below)
```

## MCP Servers

Two MCP (Model Context Protocol) servers provide AI agents with controlled access to the development environment over JSON-RPC via stdio.

### MCP Filesystem Server
- **Purpose**: Safe file operations scoped to `/workspace`
- **Operations**: `list`, `read`, `write`, `search`, `exists`, `mkdir`
- **Security**: Path validation, directory traversal prevention

### MCP Shell Server
- **Purpose**: Allowlisted command execution
- **Allowed commands**: `npm`, `node`, `git`, `ls`, `cat`, `echo`
- **Security**: Command allowlisting, argument validation, 30s timeout

### Security Model
- **Read-only root filesystems** for all containers
- **Dropped capabilities** (`cap_drop: ["ALL"]`)
- **No new privileges** (`no-new-privileges: true`)
- **Network isolation** (`network_mode: "none"` for MCP servers)
- **Memory and CPU limits**
- **Non-executable /tmp** mounts

See [docs/README.md](./docs/README.md) for full MCP server documentation and integration examples.

## Git Commit Agent

An AI-powered tool that scans your git repo, finds branches with unpushed changes, and generates conventional commit messages using Claude.

```bash
cd tools
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python git_commit_agent.py
```

See [tools/README.md](./tools/README.md) for full usage, config, and options.

## Dev Container

A general-purpose development container with common tooling pre-installed (git, node, python, jq, ripgrep).

## Quick Start

```bash
# Build and start the dev environment
docker-compose build
docker-compose up -d dev

# Test MCP servers
make test-mcp     # Test both
make test-fs      # Filesystem server only
make test-shell   # Shell server only
```

### Common Commands

```bash
# View logs
docker-compose logs -f dev

# Stop all services
docker-compose down

# Run MCP servers interactively
docker-compose run --rm -i mcp-fs
docker-compose run --rm -i mcp-shell
```

## AI Context Files

This repo includes files used by Claude/Cline for persistent AI context:

- **`CLAUDE.md`** — Instructions and context for Claude when working in this repo
- **`memory-bank/`** — Structured context files (active context, system patterns, tech stack, progress) used by Cline's memory bank feature

These files are committed intentionally and are part of the AI-assisted development workflow.

## Roadmap

- ✅ Phase 1: Core container infrastructure
- ✅ Phase 2: MCP server implementation (filesystem + shell)
- ✅ Phase 3: Git commit agent tool
- 🚧 Phase 4: Additional MCP tools (browser, search)
- 🚧 Phase 5: Streamlit dashboard for agent monitoring
