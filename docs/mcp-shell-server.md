# MCP Shell Server Documentation

## Overview

The MCP Shell Server provides secure command execution for AI agents within a containerized environment. It implements the Model Context Protocol (MCP) over JSON-RPC via stdio, enabling controlled execution of allowlisted commands within a restricted workspace.

## Architecture

### Security Model
- **Command Allowlisting**: Only predefined commands and arguments are permitted
- **Workspace Isolation**: Commands execute within `/workspace` directory
- **Container Isolation**: Runs in isolated container with no network access
- **Timeout Protection**: Commands automatically terminated after 30 seconds
- **Argument Validation**: Command arguments are validated against allowlists

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
        {"name": "shell_exec", "description": "Execute allowed shell commands"},
        {"name": "shell_allowed", "description": "List allowed commands"}
      ]
    }
  }
}
```

### Methods

#### `shell/exec` - Execute Command

Executes an allowed command with specified arguments.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "shell/exec",
  "params": {
    "command": "npm",
    "args": ["install"],
    "cwd": "/workspace" // Optional, defaults to /workspace
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "command": "npm install",
    "exitCode": 0,
    "stdout": "added 245 packages from 789 contributors",
    "stderr": "",
    "success": true
  }
}
```

#### `shell/allowed` - List Allowed Commands

Returns the complete list of allowed commands and their permitted arguments.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "shell/allowed"
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "allowedCommands": {
      "npm": ["install", "test", "run", "build", "start", "dev", "lint"],
      "node": ["--version"],
      "git": ["status", "log", "--version"],
      "ls": ["-la", "-l"],
      "cat": [],
      "echo": []
    }
  }
}
```

## Allowed Commands

### NPM Commands
- `npm install` - Install dependencies
- `npm test` - Run tests
- `npm run [script]` - Run package.json scripts
- `npm build` - Build project
- `npm start` - Start application
- `npm dev` - Start development server
- `npm lint` - Run linting

### Development Tools
- `node --version` - Check Node.js version
- `git status` - Check git status
- `git log` - View commit history
- `git --version` - Check git version

### System Commands
- `ls -la` / `ls -l` - List directory contents
- `cat [file]` - Display file contents (any arguments allowed)
- `echo [text]` - Echo text (any arguments allowed)

## Error Handling

### Error Response Format

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,
    "message": "Command 'rm' not allowed"
  }
}
```

### Common Error Types

- **Command Not Allowed**: Attempted to execute non-allowlisted command
- **Argument Not Allowed**: Used forbidden argument with allowlisted command
- **Command Timeout**: Command exceeded 30-second timeout
- **Execution Failed**: Command failed to start or crashed

### Security Errors

- `"Command '{cmd}' not allowed"`: Command not in allowlist
- `"Command '{cmd}' with argument '{arg}' not allowed"`: Invalid argument
- `"Command timeout after 30 seconds"`: Execution timeout
- `"Command execution failed: {error}"`: System execution error

## Usage Examples

### Installing Dependencies

```bash
# Request
echo '{"jsonrpc":"2.0","id":1,"method":"shell/exec","params":{"command":"npm","args":["install"]}}' | docker run --rm -i -v "$(pwd):/workspace" mcp-shell-server

# Response
{"jsonrpc":"2.0","id":1,"result":{"command":"npm install","exitCode":0,"stdout":"added 245 packages","stderr":"","success":true}}
```

### Running Tests

```bash
# Request  
echo '{"jsonrpc":"2.0","id":2,"method":"shell/exec","params":{"command":"npm","args":["test"]}}' | docker run --rm -i -v "$(pwd):/workspace" mcp-shell-server

# Response
{"jsonrpc":"2.0","id":2,"result":{"command":"npm test","exitCode":0,"stdout":"✓ All tests passed","stderr":"","success":true}}
```

### Starting a Development Script

```bash
# Request
echo '{"jsonrpc":"2.0","id":3,"method":"shell/exec","params":{"command":"npm","args":["run","dev"]}}' | docker run --rm -i -v "$(pwd):/workspace" mcp-shell-server

# Response  
{"jsonrpc":"2.0","id":3,"result":{"command":"npm run dev","exitCode":0,"stdout":"Server running","stderr":"","success":true}}
```

### Checking Project Status

```bash
# Request
echo '{"jsonrpc":"2.0","id":4,"method":"shell/exec","params":{"command":"git","args":["status"]}}' | docker run --rm -i -v "$(pwd):/workspace" mcp-shell-server

# Response
{"jsonrpc":"2.0","id":4,"result":{"command":"git status","exitCode":0,"stdout":"On branch main\nnothing to commit","stderr":"","success":true}}
```

## Security Features

### Command Validation

The server implements strict command validation:

```javascript
validateCommand(command, args = []) {
  const cmd = command.toLowerCase();
  
  if (!this.allowedCommands[cmd]) {
    throw new Error(`Command '${cmd}' not allowed`);
  }
  
  const allowedArgs = this.allowedCommands[cmd];
  if (allowedArgs.length === 0) return true; // Any args allowed
  
  // Check if first arg is in allowed list
  if (args.length > 0 && !allowedArgs.includes(args[0])) {
    throw new Error(`Command '${cmd}' with argument '${args[0]}' not allowed`);
  }
  
  return true;
}
```

### Execution Safety

- **Process Isolation**: Each command runs in separate process
- **Timeout Protection**: 30-second maximum execution time
- **Working Directory**: Commands execute in `/workspace` by default
- **Environment Control**: Environment variables are controlled
- **Output Capture**: Both stdout and stderr are captured safely

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
    "shell": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/path/to/workspace:/workspace",
        "mcp-shell-server"
      ]
    }
  }
}
```

## Common Workflows

### Project Workflow

```javascript
// Install dependencies
{"method": "shell/exec", "params": {"command": "npm", "args": ["install"]}}

// Run development script
{"method": "shell/exec", "params": {"command": "npm", "args": ["run", "dev"]}}

// Run tests
{"method": "shell/exec", "params": {"command": "npm", "args": ["test"]}}

// Build for production
{"method": "shell/exec", "params": {"command": "npm", "args": ["run", "build"]}}
```

## Development Notes

### Adding New Commands

To add new allowed commands, modify the `allowedCommands` object:

```javascript
this.allowedCommands = {
  // Existing commands...
  'yarn': ['install', 'test', 'build'],
  'docker': ['--version'],
  'kubectl': ['get', 'describe']
};
```

### Argument Validation Rules

- **Empty array `[]`**: Any arguments allowed
- **Non-empty array**: Only specified arguments allowed as first parameter
- **Case sensitivity**: Command names are case-insensitive, arguments are case-sensitive

### Environment Variables

The server inherits the container's environment and allows custom environment variables:

```javascript
// Custom environment for command
{
  "method": "shell/exec",
  "params": {
    "command": "npm",
    "args": ["run", "build"],
    "env": {"NODE_ENV": "production"}
  }
}
```

## Troubleshooting

### Common Issues

1. **Command Not Found**: Ensure command is installed in container
2. **Permission Denied**: Check file permissions in workspace
3. **Timeout Errors**: Increase timeout or optimize command
4. **Memory Limits**: Monitor container resource usage

### Debugging

Enable verbose logging by setting environment variable:
```bash
DEBUG=mcp-shell docker run --rm -i -v "$(pwd):/workspace" mcp-shell-server
```

### Performance Optimization

- Use `.dockerignore` to reduce context size
- Cache npm dependencies in container layers
- Use multi-stage builds for smaller images
- Monitor memory usage during long-running commands

## Security Best Practices

1. **Minimal Command Set**: Only include necessary commands in allowlist
2. **Argument Validation**: Strictly validate all command arguments
3. **Resource Limits**: Set appropriate CPU and memory limits
4. **Network Isolation**: Never enable network access for shell server
5. **Regular Updates**: Keep base images and dependencies updated
6. **Audit Logs**: Monitor command execution for suspicious activity
7. **Workspace Isolation**: Never mount sensitive directories
