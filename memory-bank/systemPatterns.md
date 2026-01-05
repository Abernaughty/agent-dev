# System Patterns & Architecture

## Container Architecture
```
┌──────────────────────────────┐
│        Dev Container         │
│   (Agent Development Tools)  │
│   Network: dev-network       │
└──────────────────────────────┘
               │
               │
┌─────────────────────────────────┐
│        Development Network       │
│        (172.20.0.0/16)           │
└─────────────────────────────────┘

┌─────────────────┐    ┌─────────────────┐
│  MCP-FS Server  │    │ MCP-Shell Server│
│  (Filesystem)   │    │   (Commands)    │
│ Network: none   │    │ Network: none   │
│ Stdio only      │    │ Stdio only      │
└─────────────────┘    └─────────────────┘
```

## Security Patterns
- **Defense in Depth**: Multiple security layers (container, network, filesystem)
- **Principle of Least Privilege**: Minimal permissions, dropped capabilities
- **Isolation**: Network isolation for MCP servers, controlled workspace access
- **Immutable Infrastructure**: Read-only root filesystems by default

## Communication Patterns
- **MCP Protocol**: JSON-RPC over stdio for agent communication
- **Container Orchestration**: Docker Compose for service management
- **Volume Mounting**: Dedicated `/workspace` mount for projects

## Development Patterns
- **General-Purpose Tooling**: Dev container ships common CLIs
- **Explicit Bootstrapping**: Projects created intentionally in `/workspace`
- **Minimal Assumptions**: No framework-specific scaffolding
