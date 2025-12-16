#!/usr/bin/env pwsh

Write-Host "🔧 Testing Shell Server Fix" -ForegroundColor Yellow

# Rebuild just the shell server
docker-compose build --no-cache mcp-shell

Write-Host "`n🐚 Testing fixed MCP Shell Server..." -ForegroundColor Yellow

Write-Host "Testing shell/exec operation (ls):"
'{"jsonrpc": "2.0", "id": 1, "method": "shell/exec", "params": {"command": "ls", "args": ["-la"]}}' | docker-compose run --rm -T mcp-shell

Write-Host "`nTesting shell/exec operation (echo):"
'{"jsonrpc": "2.0", "id": 2, "method": "shell/exec", "params": {"command": "echo", "args": ["Hello MCP!"]}}' | docker-compose run --rm -T mcp-shell

Write-Host "`n✅ Shell Server Test Complete!" -ForegroundColor Green
