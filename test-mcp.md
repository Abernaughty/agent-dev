# Test MCP Servers

Simple test script to validate MCP servers work correctly.

## Test Filesystem Server

```bash
# Test filesystem operations
echo '{"jsonrpc": "2.0", "id": 1, "method": "fs/list", "params": {"path": "."}}' | docker-compose run --rm -i mcp-fs

echo '{"jsonrpc": "2.0", "id": 2, "method": "fs/exists", "params": {"path": "README.md"}}' | docker-compose run --rm -i mcp-fs
```

## Test Shell Server

```bash
# Test shell operations
echo '{"jsonrpc": "2.0", "id": 1, "method": "shell/allowed"}' | docker-compose run --rm -i mcp-shell

echo '{"jsonrpc": "2.0", "id": 2, "method": "shell/exec", "params": {"command": "ls", "args": ["-la"]}}' | docker-compose run --rm -i mcp-shell
```

## Example Responses

### Filesystem List
```json
{"jsonrpc": "2.0", "id": 1, "result": [{"name": "README.md", "type": "file", "path": "README.md"}]}
```

### Shell Exec
```json
{"jsonrpc": "2.0", "id": 2, "result": {"command": "ls -la", "exitCode": 0, "stdout": "total 4\ndrwxr-xr-x 2 root root 4096 ...", "success": true}}
```
