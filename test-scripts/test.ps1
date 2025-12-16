#!/usr/bin/env pwsh

Write-Host "🧪 Testing Agent Development Environment" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

# Test 1: Clean build containers
Write-Host "`n📦 Building MCP containers (clean)..." -ForegroundColor Yellow
docker-compose build --no-cache mcp-fs mcp-shell
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Containers built successfully" -ForegroundColor Green
} else {
    Write-Host "❌ Container build failed" -ForegroundColor Red
    exit 1
}

# Test 2: Test filesystem server
Write-Host "`n🔍 Testing MCP Filesystem Server..." -ForegroundColor Yellow

Write-Host "Testing fs/list operation:"
'{"jsonrpc": "2.0", "id": 1, "method": "fs/list", "params": {"path": "."}}' | docker-compose run --rm -T mcp-fs

Write-Host "`nTesting fs/exists operation:"
'{"jsonrpc": "2.0", "id": 2, "method": "fs/exists", "params": {"path": "README.md"}}' | docker-compose run --rm -T mcp-fs

# Test 3: Test shell server
Write-Host "`n🐚 Testing MCP Shell Server..." -ForegroundColor Yellow

Write-Host "Testing shell/allowed operation:"
'{"jsonrpc": "2.0", "id": 1, "method": "shell/allowed"}' | docker-compose run --rm -T mcp-shell

Write-Host "`nTesting shell/exec operation:"
'{"jsonrpc": "2.0", "id": 2, "method": "shell/exec", "params": {"command": "ls", "args": ["-la"]}}' | docker-compose run --rm -T mcp-shell

Write-Host "`n✅ MCP Testing Complete!" -ForegroundColor Green
