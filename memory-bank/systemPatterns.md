# System Patterns & Architecture

## Container Architecture
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Web Container │    │  Func Container │    │ Azurite Storage │
│   (Svelte Dev)  │    │ (Azure Funcs)   │    │   (Emulator)    │
│   Port: 3000    │    │   Port: 7071    │    │  Ports: 10000+  │
│   Network: dev  │    │  Network: dev   │    │  Network: dev   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
              ┌─────────────────────────────────┐
              │        Development Network       │
              │        (172.20.0.0/16)         │
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
- **Isolation**: Network isolation for MCP servers, read-only workspace
- **Immutable Infrastructure**: Read-only root filesystems

## Communication Patterns
- **MCP Protocol**: JSON-RPC over stdio for agent communication
- **Container Orchestration**: Docker Compose for service management
- **Volume Mounting**: Read-only workspace, writable tmp/cache directories

## Development Patterns
- **Auto-initialization**: Containers detect and create missing project files
- **Hot Reloading**: Development servers with file watching
- **Multi-stage Build**: Optimized container images
