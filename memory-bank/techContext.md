# Technical Context

## Tech Stack
- **Container Runtime**: Docker + Docker Compose
- **Web Framework**: Svelte/SvelteKit with Vite
- **Backend**: Azure Functions v4 (Node.js 18)
- **Storage**: Azurite (Azure Storage emulator)
- **Development**: VS Code Server, hot reloading
- **Protocol**: MCP (Model Context Protocol) for AI communication

## Container Images
- **Web**: `node:18-alpine` + VS Code Server + Svelte tools
- **Functions**: `mcr.microsoft.com/azure-functions/node:4-node18`
- **Storage**: `mcr.microsoft.com/azure-storage/azurite:latest`
- **MCP Servers**: Custom `node:18-alpine` with security hardening

## Security Configuration
```yaml
x-security-base: &security-base
  read_only: true
  cap_drop: ["ALL"]
  security_opt: ["no-new-privileges:true"]
  tmpfs: ["/tmp:noexec,nosuid,size=100m"]
  mem_limit: 512m
  cpus: 0.5
```

## Network Setup
- **Development Network**: `172.20.0.0/16` for web/func/azurite
- **MCP Isolation**: `network_mode: "none"` for security
- **Port Mapping**: 3000 (Svelte), 7071 (Functions), 8080 (VS Code), 10000+ (Azurite)

## File Structure
- **Workspace**: `/workspace` (read-only mount)
- **Containers**: Individual Dockerfiles in `containers/*/`
- **Config**: Claude Desktop MCP configuration
- **Management**: Makefile for common operations
