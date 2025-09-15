const fs = require('fs').promises;
const path = require('path');

class MCPFilesystemServer {
  constructor() {
    this.workspaceRoot = '/workspace';
  }

  // Validate and resolve paths within workspace
  resolvePath(requestedPath) {
    const resolved = path.resolve(this.workspaceRoot, requestedPath || '.');
    
    // Ensure path is within workspace
    if (!resolved.startsWith(this.workspaceRoot)) {
      throw new Error('Path outside workspace not allowed');
    }
    
    return resolved;
  }

  // Handle MCP protocol messages
  async handleRequest(request) {
    try {
      const { method, params } = request;
      
      switch (method) {
        case 'fs/list':
          return await this.listDirectory(params?.path);
        case 'fs/read':
          return await this.readFile(params?.path);
        case 'fs/write':
          return await this.writeFile(params?.path, params?.content);
        case 'fs/search':
          return await this.searchFiles(params?.pattern, params?.path);
        case 'fs/exists':
          return await this.fileExists(params?.path);
        case 'fs/mkdir':
          return await this.createDirectory(params?.path);
        default:
          throw new Error(`Unknown method: ${method}`);
      }
    } catch (error) {
      return {
        error: {
          code: -32000,
          message: error.message
        }
      };
    }
  }

  async listDirectory(dirPath = '.') {
    const fullPath = this.resolvePath(dirPath);
    const entries = await fs.readdir(fullPath, { withFileTypes: true });
    
    return {
      result: entries.map(entry => ({
        name: entry.name,
        type: entry.isDirectory() ? 'directory' : 'file',
        path: path.join(dirPath, entry.name)
      }))
    };
  }

  async readFile(filePath) {
    const fullPath = this.resolvePath(filePath);
    const content = await fs.readFile(fullPath, 'utf8');
    
    return {
      result: {
        path: filePath,
        content: content
      }
    };
  }

  async writeFile(filePath, content) {
    const fullPath = this.resolvePath(filePath);
    
    // Ensure directory exists
    await fs.mkdir(path.dirname(fullPath), { recursive: true });
    await fs.writeFile(fullPath, content, 'utf8');
    
    return {
      result: {
        path: filePath,
        success: true
      }
    };
  }

  async searchFiles(pattern, searchPath = '.') {
    const fullPath = this.resolvePath(searchPath);
    const results = [];
    
    async function searchRecursive(dir) {
      const entries = await fs.readdir(dir, { withFileTypes: true });
      
      for (const entry of entries) {
        const entryPath = path.join(dir, entry.name);
        const relativePath = path.relative('/workspace', entryPath);
        
        if (entry.name.toLowerCase().includes(pattern.toLowerCase())) {
          results.push({
            name: entry.name,
            path: relativePath,
            type: entry.isDirectory() ? 'directory' : 'file'
          });
        }
        
        if (entry.isDirectory()) {
          await searchRecursive(entryPath);
        }
      }
    }
    
    await searchRecursive(fullPath);
    return { result: results };
  }

  async fileExists(filePath) {
    try {
      const fullPath = this.resolvePath(filePath);
      await fs.access(fullPath);
      return { result: { exists: true, path: filePath } };
    } catch {
      return { result: { exists: false, path: filePath } };
    }
  }

  async createDirectory(dirPath) {
    const fullPath = this.resolvePath(dirPath);
    await fs.mkdir(fullPath, { recursive: true });
    
    return {
      result: {
        path: dirPath,
        success: true
      }
    };
  }
}

// Main server process
class MCPServer {
  constructor() {
    this.fsServer = new MCPFilesystemServer();
    this.requestId = 0;
  }

  start() {
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (data) => {
      this.handleInput(data.trim());
    });
    
    // Send initialization
    this.sendResponse({
      jsonrpc: '2.0',
      id: null,
      result: {
        protocolVersion: '2024-11-05',
        capabilities: {
          tools: [
            {
              name: 'fs_list',
              description: 'List directory contents'
            },
            {
              name: 'fs_read',
              description: 'Read file contents'
            },
            {
              name: 'fs_write',
              description: 'Write file contents'
            },
            {
              name: 'fs_search',
              description: 'Search for files by name'
            },
            {
              name: 'fs_exists',
              description: 'Check if file exists'
            },
            {
              name: 'fs_mkdir',
              description: 'Create directory'
            }
          ]
        }
      }
    });
  }

  async handleInput(input) {
    try {
      const request = JSON.parse(input);
      const response = await this.fsServer.handleRequest(request);
      
      this.sendResponse({
        jsonrpc: '2.0',
        id: request.id,
        ...response
      });
    } catch (error) {
      this.sendResponse({
        jsonrpc: '2.0',
        id: null,
        error: {
          code: -32700,
          message: 'Parse error'
        }
      });
    }
  }

  sendResponse(response) {
    process.stdout.write(JSON.stringify(response) + '\n');
  }
}

// Start server
const server = new MCPServer();
server.start();
