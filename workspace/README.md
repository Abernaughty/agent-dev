# Sample Project

This is a placeholder for your actual project code.

When you run the containers, they will mount this workspace directory and can initialize:
- A new Svelte project (via the web container)
- A new Azure Functions project (via the func container)

## Getting Started

1. Start the development environment:
   ```bash
   docker-compose up -d web func azurite
   ```

2. The web container will automatically initialize a Svelte project if one doesn't exist
3. The func container will automatically initialize an Azure Functions project if one doesn't exist

## Files that will be created:
- `package.json` (Svelte project)
- `host.json` (Azure Functions project)
- Various other project files
