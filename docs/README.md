# MCP Servers Documentation

This directory contains comprehensive documentation for the Model Context Protocol (MCP) servers implemented in the Agent Development Environment project.

## Overview

The MCP servers provide secure, controlled interfaces for AI agents to interact with the development environment through standardized JSON-RPC protocols over stdio.

## Available Servers

### [MCP Filesystem Server](./mcp-filesystem-server.md)
Provides secure file operations within the workspace:
- **File Operations**: Read, write, list, search, exists, mkdir
- **Security**: Path validation, workspace isolation
- **Use Cases**: Reading configurations, writing code, managing project structure

### [MCP Shell Server](./mcp-shell-server.md)  
Provides controlled command execution:
- **Allowed Commands**: npm, node, git, ls, cat, echo
- **Security**: Command allowlisting, argument validation, timeouts
- **Use Cases**: Installing dependencies, running tests, building projects

## Architecture

```mermaid
graph TD
    A[Claude Desktop/AI Agent] -->|JSON-RPC over stdio| B[MCP Filesystem Server]
    A -->|JSON-RPC over stdio| C[MCP Shell Server]
    
    B --> D[/workspace Directory]
    C --> D
    
    B -.->|Read-only mount| E[Host Project Files]
    C -.->|Read-write mount| E
    
    subgraph "Container Security"
        F[No Network Access]
        G[Dropped Capabilities]
        H[Resource Limits]
        I[Non-root User]
    end
    
    B --- F
    B --- G
    B --- H
    B --- I
    
    C --- F
    C --- G  
    C --- H
    C --- I
```

## Security Model

### Defense in Depth
- **Container Isolation**: Each server runs in isolated containers
- **Network Isolation**: No network access (`network_mode: "none"`)
- **Filesystem Security**: Read-only root filesystem, workspace mounting
- **Capability Dropping**: All Linux capabilities dropped
- **Resource Limits**: CPU and memory constraints

### Input Validation
- **Path Validation**: Prevents directory traversal attacks
- **Command Allowlisting**: Only predefined commands permitted
- **Argument Validation**: Command arguments validated against allowlists
- **Timeout Protection**: Commands terminated after 30 seconds

## Protocol Specification

### JSON-RPC 2.0
Both servers implement the JSON-RPC 2.0 specification over stdio:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "fs/read",
  "params": {"path": "package.json"}
}
```

### Initialization Protocol
Each server sends capabilities on startup:

```json
{
  "jsonrpc": "2.0", 
  "id": null,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": [...]
    }
  }
}
```

## Integration Examples

### Claude Desktop Configuration

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--read-only",
        "--cap-drop=ALL", 
        "--network=none",
        "--memory=512m",
        "--cpus=0.5",
        "-v", "/path/to/project:/workspace:ro",
        "mcp-fs-server"
      ]
    },
    "shell": {
      "command": "docker", 
      "args": [
        "run", "--rm", "-i",
        "--read-only",
        "--cap-drop=ALL",
        "--network=none", 
        "--memory=512m",
        "--cpus=0.5",
        "-v", "/path/to/project:/workspace",
        "mcp-shell-server"
      ]
    }
  }
}
```

### Docker Compose Integration

```yaml
services:
  mcp-fs:
    build: ./containers/mcp-fs
    network_mode: "none"
    read_only: true
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    tmpfs: ["/tmp:noexec,nosuid,size=100m"]
    mem_limit: 512m
    cpus: 0.5
    volumes:
      - "./workspace:/workspace:ro"

  mcp-shell:
    build: ./containers/mcp-shell  
    network_mode: "none"
    read_only: true
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    tmpfs: ["/tmp:noexec,nosuid,size=100m"]
    mem_limit: 512m
    cpus: 0.5
    volumes:
      - "./workspace:/workspace"
```

## Common Workflows

### Reading Project Configuration
```bash
# List workspace contents
echo '{"jsonrpc":"2.0","id":1,"method":"fs/list","params":{"path":"."}}' | docker run --rm -i -v "$(pwd):/workspace:ro" mcp-fs-server

# Read package.json
echo '{"jsonrpc":"2.0","id":2,"method":"fs/read","params":{"path":"package.json"}}' | docker run --rm -i -v "$(pwd):/workspace:ro" mcp-fs-server
```

### Installing Dependencies and Running Tests
```bash
# Install dependencies
echo '{"jsonrpc":"2.0","id":1,"method":"shell/exec","params":{"command":"npm","args":["install"]}}' | docker run --rm -i -v "$(pwd):/workspace" mcp-shell-server

# Run tests
echo '{"jsonrpc":"2.0","id":2,"method":"shell/exec","params":{"command":"npm","args":["test"]}}' | docker run --rm -i -v "$(pwd):/workspace" mcp-shell-server
```

### Creating New Files
```bash
# Create notes directory
echo '{"jsonrpc":"2.0","id":1,"method":"fs/mkdir","params":{"path":"notes"}}' | docker run --rm -i -v "$(pwd):/workspace:ro" mcp-fs-server

# Write sample file
echo '{"jsonrpc":"2.0","id":2,"method":"fs/write","params":{"path":"notes/README.txt","content":"Project notes go here."}}' | docker run --rm -i -v "$(pwd):/workspace:ro" mcp-fs-server
```

## Development Guidelines

### Adding New Commands
1. Update `allowedCommands` object in shell server
2. Add argument validation rules
3. Test security implications
4. Update documentation

### Adding New File Operations
1. Implement method in filesystem server
2. Add path validation
3. Test security boundaries
4. Update API documentation

### Security Considerations
- Never mount sensitive directories
- Always use read-only mounts when possible
- Validate all inputs before processing
- Monitor resource usage and set limits
- Regular security audits of allowed commands

## Testing

### Unit Testing
Each server includes test suites for:
- Protocol compliance
- Security validation
- Error handling
- Edge cases

### Integration Testing
Full workflow testing with:
- Real project scenarios
- Claude Desktop integration
- Multi-server coordination
- Performance benchmarks

### Security Testing
Regular testing for:
- Directory traversal attempts
- Command injection attempts
- Resource exhaustion attacks
- Privilege escalation attempts

## Monitoring and Logging

### Container Logs
```bash
# View filesystem server logs
docker logs mcp-fs-container

# View shell server logs  
docker logs mcp-shell-container
```

### Performance Metrics
- Request/response times
- Memory usage patterns
- CPU utilization
- Error rates

### Security Alerts
- Path validation failures
- Unauthorized command attempts
- Resource limit violations
- Unusual usage patterns

## Troubleshooting

### Common Issues
1. **Permission Denied**: Check workspace mount permissions
2. **Command Not Found**: Verify command is in allowlist
3. **Path Errors**: Ensure paths are relative to workspace
4. **Timeout Issues**: Optimize long-running commands

### Debug Mode
Enable verbose logging:
```bash
DEBUG=mcp-* docker run --rm -i -v "$(pwd):/workspace" mcp-fs-server
```

### Health Checks
```bash
# Test filesystem server
echo '{"jsonrpc":"2.0","id":1,"method":"fs/list","params":{"path":"."}}' | docker run --rm -i -v "$(pwd):/workspace:ro" mcp-fs-server

# Test shell server
echo '{"jsonrpc":"2.0","id":1,"method":"shell/allowed"}' | docker run --rm -i -v "$(pwd):/workspace" mcp-shell-server
```

## Contributing

### Code Standards
- Follow Node.js best practices
- Implement comprehensive error handling
- Add security validations for all inputs
- Include unit tests for new features

### Documentation Standards
- Document all new methods and parameters
- Include usage examples
- Update security considerations
- Maintain API compatibility notes

### Security Review Process
1. Security impact assessment
2. Code review by security team
3. Penetration testing
4. Documentation updates
5. Deployment approval

## References

- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)
- [Node.js Security Checklist](https://nodejs.org/en/docs/guides/security/)
