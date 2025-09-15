#!/bin/bash
set -e

# Initialize workspace if package.json doesn't exist
if [ ! -f "/workspace/package.json" ]; then
    echo "Initializing new Svelte project..."
    cd /workspace
    npm create svelte@latest . -- --template skeleton --types typescript --no-eslint --no-prettier --no-playwright
    npm install
fi

# Install dependencies if node_modules doesn't exist
if [ ! -d "/workspace/node_modules" ]; then
    echo "Installing dependencies..."
    cd /workspace
    npm install
fi

# Start the appropriate service based on command
case "$1" in
    "dev")
        echo "Starting Svelte development server..."
        cd /workspace
        npm run dev -- --host 0.0.0.0 --port 3000
        ;;
    "build")
        echo "Building Svelte project..."
        cd /workspace
        npm run build
        ;;
    "code-server")
        echo "Starting VS Code Server..."
        code-server --bind-addr 0.0.0.0:8080 --auth none /workspace
        ;;
    *)
        echo "Available commands: dev, build, code-server"
        exec "$@"
        ;;
esac
