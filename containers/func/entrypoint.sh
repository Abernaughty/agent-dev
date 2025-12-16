#!/bin/bash
set -e

# Initialize Azure Functions project if host.json doesn't exist
if [ ! -f "/workspace/host.json" ]; then
    echo "Initializing new Azure Functions project..."
    cd /workspace
    func init . --typescript --worker-runtime node
    
    # Create a sample HTTP trigger function
    func new --name HttpTrigger --template "HTTP trigger" --authlevel anonymous
fi

# Install dependencies if package.json exists and node_modules doesn't
if [ -f "/workspace/package.json" ] && [ ! -d "/workspace/node_modules" ]; then
    echo "Installing dependencies..."
    cd /workspace
    npm install
fi

# Start the appropriate service based on command
case "$1" in
    "start")
        echo "Starting Azure Functions runtime..."
        cd /workspace
        func start --host 0.0.0.0 --port 7071
        ;;
    "build")
        echo "Building Azure Functions project..."
        cd /workspace
        npm run build
        ;;
    "new")
        echo "Creating new function..."
        cd /workspace
        func new
        ;;
    *)
        echo "Available commands: start, build, new"
        exec "$@"
        ;;
esac
