# Agent Development Environment - Project Brief

## Core Requirements
Build a secure, containerized development environment enabling AI agents to safely interact with Svelte + Azure Functions projects through MCP (Model Context Protocol) servers.

## Primary Goals
1. **Security**: Isolated containers with read-only filesystems, dropped capabilities, network isolation for MCP tools
2. **Development**: Full-featured Svelte + Azure Functions development environment
3. **Agent Integration**: MCP servers for safe AI agent interaction via stdio
4. **Reproducibility**: Consistent development environment across machines

## Implementation Phases
1. **Phase 1**: Core infrastructure (docker-compose, security baselines, container definitions) ✅
2. **Phase 2**: MCP server implementation (filesystem + shell servers with stdio communication)
3. **Phase 3**: Development environment completion (VS Code integration, project initialization)
4. **Phase 4**: Security hardening and agent configuration (Claude Desktop integration)

## Success Criteria
- AI agents can safely perform file operations and execute approved commands
- Development environment provides full Svelte + Azure Functions capabilities
- All containers follow security best practices
- Easy setup and reproducible across environments
