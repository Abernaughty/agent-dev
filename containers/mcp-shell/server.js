const { spawn } = require('child_process');
const path = require('path');

class MCPShellServer {
  constructor() {
    this.workspaceRoot = '/workspace';
    this.allowedCommands = {
      'npm': ['install', 'test', 'run', 'build', 'start', 'dev', 'lint'],
      'node': ['--version'],
      'func': ['init', 'new', 'start', '--help'],
      'git': ['status', 'log', '--version'],
      'ls': ['-la', '-l'],
      'cat': [],
      'echo': []
    };
  }

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

  async executeCommand(command, args = [], options = {}) {
    this.validateCommand(command, args);
    
    return new Promise((resolve, reject) => {
      const childProcess = spawn(command, args, {
        cwd: options.cwd || this.workspaceRoot,
        env: { ...process.env, ...options.env },
        stdio: 'pipe'
      });
      
      let stdout = '';
      let stderr = '';
      
      childProcess.stdout.on('data', (data) => {
        stdout += data.toString();
      });
      
      childProcess.stderr.on('data', (data) => {
        stderr += data.toString();
      });
      
      childProcess.on('close', (code) => {
        resolve({
          command: `${command} ${args.join(' ')}`,
          exitCode: code,
          stdout: stdout.trim(),
          stderr: stderr.trim(),
          success: code === 0
        });
      });
      
      childProcess.on('error', (error) => {
        reject(new Error(`Command execution failed: ${error.message}`));
      });
      
      // Timeout after 30 seconds
      setTimeout(() => {
        childProcess.kill('SIGTERM');
        reject(new Error('Command timeout after 30 seconds'));
      }, 30000);
    });
  }

  async handleRequest(request) {
    try {
      const { method, params } = request;
      
      switch (method) {
        case 'shell/exec':
          return await this.handleExec(params);
        case 'shell/allowed':
          return await this.listAllowedCommands();
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

  async handleExec(params) {
    const { command, args = [], cwd } = params;
    
    const result = await this.executeCommand(command, args, { cwd });
    
    return {
      result: result
    };
  }

  async listAllowedCommands() {
    return {
      result: {
        allowedCommands: this.allowedCommands
      }
    };
  }
}

class MCPServer {
  constructor() {
    this.shellServer = new MCPShellServer();
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
              name: 'shell_exec',
              description: 'Execute allowed shell commands'
            },
            {
              name: 'shell_allowed',
              description: 'List allowed commands'
            }
          ]
        }
      }
    });
  }

  async handleInput(input) {
    try {
      const request = JSON.parse(input);
      const response = await this.shellServer.handleRequest(request);
      
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
