#!/bin/bash
set -e

echo "🧪 Testing Agent Development Environment"
echo "========================================"

echo "📦 Building containers..."
docker-compose build mcp-fs mcp-shell

echo "✅ Containers built successfully"
echo ""

echo "🔍 Testing MCP Filesystem Server..."
echo "Testing fs/list operation:"
echo '{"jsonrpc": "2.0", "id": 1, "method": "fs/list", "params": {"path": "."}}' | docker-compose run --rm -i mcp-fs
echo ""

echo "Testing fs/exists operation:"
echo '{"jsonrpc": "2.0", "id": 2, "method": "fs/exists", "params": {"path": "README.md"}}' | docker-compose run --rm -i mcp-fs
echo ""

echo "🐚 Testing MCP Shell Server..."
echo "Testing shell/allowed operation:"
echo '{"jsonrpc": "2.0", "id": 1, "method": "shell/allowed"}' | docker-compose run --rm -i mcp-shell
echo ""

echo "Testing shell/exec operation:"
echo '{"jsonrpc": "2.0", "id": 2, "method": "shell/exec", "params": {"command": "ls", "args": ["-la"]}}' | docker-compose run --rm -i mcp-shell

echo ""
echo "✅ MCP Testing Complete!"
