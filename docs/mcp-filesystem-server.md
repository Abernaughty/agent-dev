# MCP Filesystem Server Documentation

## Overview

The MCP Filesystem Server provides secure file operations for AI agents within a containerized environment. It implements the Model Context Protocol (MCP) over JSON-RPC via stdio, enabling safe file system interactions within a restricted workspace.

## Architecture

### Security Model
- **Workspace Isolation**: All operations restricted to `/workspace` directory
- **Path Validation**: Prevents directory traversal attacks
- **Container Isolation**: Runs in isolated container with no network access
- **Read-only Root**: Container filesystem is read-only except for workspace

### Communication
- **Protocol**: JSON-RPC 2.0 over stdio
- **Transport**: Standard input/output streams
- **Format**: Line-delimited JSON messages

## API Reference

### Initialization

Upon startup, the server sends an initialization message:

```json
{
  "jsonrpc": "2.0",
  "id": null,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": [
        {"name": "fs_list", "description": "List directory contents"},
        {"name": "fs_read", "description": "Read file contents"},
        {"name": "fs_write", "description": "Write file contents"},
        {"name": "fs_search", "description": "Search for files by name"},
        {"name": "fs_exists", "description": "Check if file exists"},
        {"name": "fs_mkdir", "description": "Create directory"}
      ]
    }
  }
}
```

### Methods

#### `fs/list` - List Directory Contents

Lists files and directories in the specified path.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "fs/list",
  "params": {
    "path": "." // Optional, defaults to workspace root
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {
      "name": "package.json",
      "type": "file",
      "path": "./package.json"
    },
    {
      "name": "src",
      "type": "directory", 
      "path": "./src"
    }
  ]
}
```

#### `fs/read` - Read File Contents

Reads the complete contents of a file.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "fs/read",
  "params": {
    "path": "package.json"
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "path": "package.json",
    "content": "{\n  \"name\": \"my-app\",\n  \"version\": \"1.0.0\"\n}"
  }
}
```

#### `fs/write` - Write File Contents

Writes content to a file, creating directories as needed.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "fs/write",
  "params": {
    "path": "src/app.js",
    "content": "console.log('Hello World');"
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "path": "src/app.js",
    "success": true
  }
}
```

#### `fs/search` - Search Files

Searches for files and directories by name pattern (case-insensitive).

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "fs/search",
  "params": {
    "pattern": "app",
    "path": "." // Optional, defaults to workspace root
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": [
    {
      "name": "app.js",
      "path": "src/app.js",
      "type": "file"
    },
    {
      "name": "app-config.json",
      "path": "config/app-config.json",
      "type": "file"
    }
  ]
}
```

#### `fs/exists` - Check File Existence

Checks if a file or directory exists.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "fs/exists",
  "params": {
    "path": "src/app.js"
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "exists": true,
    "path": "src/app.js"
  }
}
```

#### `fs/mkdir` - Create Directory

Creates a directory and any necessary parent directories.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "fs/mkdir",
  "params": {
    "path": "src/components"
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "path": "src/components",
    "success": true
  }
}
```

## Error Handling

### Error Response Format

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,
    "message": "Path outside workspace not allowed"
  }
}
```

### Common Error Codes

- `-32700`: Parse error (invalid JSON)
- `-32000`: Server error (security violation, file not found, etc.)

### Security Errors

- `"Path outside workspace not allowed"`: Attempted directory traversal
- `"Unknown method: {method}"`: Invalid method called

## Usage Examples

### Reading a Configuration File

```bash
# Request
echo '{"jsonrpc":"2.0","id":1,"method":"fs/read","params":{"path":"package.json"}}' | docker run --rm -i -v "$(pwd):/workspace:ro" mcp-fs-server

# Response
{"jsonrpc":"2.0","id":1,"result":{"path":"package.json","content":"{\n  \"name\": \"my-app\"\n}"}}
```

### Creating a New Component

```bash
# Create directory
echo '{"jsonrpc":"2.0","id":1,"method":"fs/mkdir","params":{"path":"src/components"}}' | docker run --rm -i -v "$(pwd):/workspace:ro" mcp-fs-server

# Write component file
echo '{"jsonrpc":"2.0","id":2,"method":"fs/write","params":{"path":"src/components/Button.svelte","content":"<button>Click me</button>"}}' | docker run --rm -i -v "$(pwd):/workspace:ro" mcp-fs-server
```

## Container Configuration

### Dockerfile
```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package.json server.js ./
RUN npm install
USER node
CMD ["node", "server.js"]
```

### Security Features
- Read-only root filesystem
- No network access (`network_mode: "none"`)
- Dropped capabilities (`cap_drop: ["ALL"]`)
- Non-root user execution
- Resource limits (512MB RAM, 0.5 CPU)

## Integration with Claude Desktop

### Configuration Example

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/path/to/workspace:/workspace:ro",
        "mcp-fs-server"
      ]
    }
  }
}
```

## Development Notes

### Path Resolution
- All paths are resolved relative to `/workspace`
- Absolute paths are converted to relative paths
- Parent directory traversal (`../`) is blocked

### Performance Considerations
- File operations are synchronous within the container
- Large files may impact response times
- Search operations are recursive and may be slow on large directories

### Limitations
- Text files only (UTF-8 encoding)
- No file permissions or ownership management
- No symbolic link support
- Maximum file size limited by container memory

## Security Best Practices

1. **Always mount workspace as read-only** when possible
2. **Use resource limits** to prevent resource exhaustion
3. **Monitor container logs** for security violations
4. **Regularly update** base Node.js image for security patches
5. **Validate all inputs** on the client side before sending to server
