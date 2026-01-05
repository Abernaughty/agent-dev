# Agent Development Environment Makefile

.PHONY: help build up down logs clean test-mcp

# Default target
help:
	@echo "Agent Development Environment"
	@echo ""
	@echo "Available commands:"
	@echo "  build       Build all containers"
	@echo "  up          Start development environment (dev)"
	@echo "  down        Stop all services"
	@echo "  logs        View logs from all services"
	@echo "  clean       Remove all containers and volumes"
	@echo "  test-mcp    Test MCP servers (Phase 2)"
	@echo "  test-fs     Test filesystem MCP server"
	@echo "  test-shell  Test shell MCP server"
	@echo "  shell-dev   Open shell in dev container"

# Build all containers
build:
	docker-compose build

# Start development environment
up:
	docker-compose up -d dev
	@echo ""
	@echo "Development environment started!"
	@echo ""
	@echo "Use 'make logs' to view container logs"

# Stop all services
down:
	docker-compose down

# View logs
logs:
	docker-compose logs -f

# Clean up everything
clean:
	docker-compose down -v --rmi all --remove-orphans
	docker system prune -f

# Test MCP servers (Phase 2)
test-mcp:
	@echo "Testing MCP Filesystem Server..."
	echo '{"jsonrpc": "2.0", "id": 1, "method": "fs/list", "params": {"path": "."}}' | docker-compose run --rm -i mcp-fs
	@echo "Testing MCP Shell Server..."
	echo '{"jsonrpc": "2.0", "id": 1, "method": "shell/allowed"}' | docker-compose run --rm -i mcp-shell

# Test specific MCP operations
test-fs:
	echo '{"jsonrpc": "2.0", "id": 1, "method": "fs/list", "params": {"path": "."}}' | docker-compose run --rm -i mcp-fs

test-shell:
	echo '{"jsonrpc": "2.0", "id": 1, "method": "shell/exec", "params": {"command": "ls", "args": ["-la"]}}' | docker-compose run --rm -i mcp-shell

# Development helpers
shell-dev:
	docker-compose exec dev /bin/bash

# Build individual containers
build-dev:
	docker-compose build dev

build-mcp-fs:
	docker-compose build mcp-fs

build-mcp-shell:
	docker-compose build mcp-shell
