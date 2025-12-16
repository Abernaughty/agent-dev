# Git Commit Message Agent

An automated agent that scans your git repository, identifies branches with unpushed changes, and generates meaningful commit messages using Claude AI.

## Features

- 🔍 **Automatic Branch Scanning** - Finds all branches with unpushed commits
- 🤖 **AI-Powered Messages** - Generates conventional commit messages using Claude
- 📝 **Staged Changes Support** - Works with `git add` workflow
- 🎯 **Interactive Selection** - Choose which branch to process
- ⚡ **Auto-commit Mode** - Generate and commit in one step
- 🔧 **Configurable** - Customize via YAML config file
- 📊 **JSON Output** - Perfect for CI/CD integration

## Requirements

- Python 3.8+
- Git installed and accessible in PATH
- Anthropic API key

## Installation

1. **Clone or download** this repository

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set your API key:**
   ```bash
   # Linux/Mac
   export ANTHROPIC_API_KEY='your-api-key-here'
   
   # Windows (Command Prompt)
   set ANTHROPIC_API_KEY=your-api-key-here
   
   # Windows (PowerShell)
   $env:ANTHROPIC_API_KEY='your-api-key-here'
   ```

4. **Optional: Make executable** (Linux/Mac):
   ```bash
   chmod +x git_commit_agent.py
   ```

## Usage

### Basic Usage

**Scan all branches with unpushed commits:**
```bash
python git_commit_agent.py
```

This will:
1. Find all branches ahead of their upstream
2. Show an interactive menu to select a branch
3. Generate a commit message for the selected branch

### Generate Message for Staged Changes

**Stage your changes first:**
```bash
git add file1.py file2.py
```

**Generate commit message:**
```bash
python git_commit_agent.py --staged
```

### Auto-commit Mode

**Generate and commit in one step:**
```bash
git add .
python git_commit_agent.py --staged --auto-commit
```

You'll be prompted to confirm before committing.

### Process Specific Branch

**Generate message for a specific branch:**
```bash
python git_commit_agent.py --branch feature/user-auth
```

### JSON Output

**Get results as JSON (useful for scripts):**
```bash
python git_commit_agent.py --json
python git_commit_agent.py --staged --json
```

## Command-Line Options

| Option | Description |
|--------|-------------|
| `--staged` | Generate message for currently staged changes |
| `--branch <name>` | Process a specific branch |
| `--auto-commit` | Automatically commit after generating message (requires `--staged`) |
| `--json` | Output results as JSON |

## Configuration

Create a configuration file to customize behavior:

**Global config:** `~/.git-commit-agent.yaml`  
**Per-repo config:** `{repo_root}/.git-commit-agent.yaml`

**Example configuration:**
```yaml
# Maximum characters from diff to send to AI
max_diff_chars: 50000

# Claude model to use
model: claude-3-5-sonnet-20241022

# Temperature (0.0 = deterministic, 1.0 = creative)
temperature: 0.7

# Maximum API retry attempts
max_retries: 3

# Allowed commit types
commit_types:
  - feat
  - fix
  - docs
  - style
  - refactor
  - test
  - chore
  - perf
  - ci
  - build
```

See `.git-commit-agent.yaml.example` for a complete example.

## Commit Message Format

The agent generates messages following the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <subject>

<body>
```

**Example:**
```
feat(auth): implement JWT authentication system

- Add JWT token generation and validation middleware
- Implement user login and registration endpoints
- Add password hashing with bcrypt
- Create authentication tests with 95% coverage
```

### Commit Types

- **feat**: New feature
- **fix**: Bug fix
- **docs**: Documentation changes
- **style**: Code style changes (formatting, etc.)
- **refactor**: Code refactoring
- **test**: Adding or updating tests
- **chore**: Maintenance tasks
- **perf**: Performance improvements
- **ci**: CI/CD changes
- **build**: Build system changes

## Git Alias Setup

Add a convenient git alias to use the agent directly:

**Linux/Mac:**
```bash
git config --global alias.ai-commit '!python /absolute/path/to/git_commit_agent.py --staged'
```

**Windows:**
```bash
git config --global alias.ai-commit "!python C:/absolute/path/to/git_commit_agent.py --staged"
```

**Usage:**
```bash
git add .
git ai-commit
```

## Examples

### Example 1: Interactive Branch Selection

```bash
$ python git_commit_agent.py

🔍 Scanning repository for unpushed changes...

🔍 Found branches with unpushed commits:

  1. feature/user-auth (3 commits ahead of origin/feature/user-auth)
  2. bugfix/login-error (1 commits ahead of origin/bugfix/login-error)
  0. Exit

Select a branch (0 to exit): 1

📝 Analyzing branch: feature/user-auth...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 feature/user-auth (3 commits ahead of origin/feature/user-auth)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Suggested commit message:

feat(auth): implement JWT-based authentication system

- Add JWT token generation and validation middleware
- Implement user login and registration endpoints
- Add password hashing with bcrypt
- Create authentication tests with 95% coverage

Files changed: 8 files (+342, -12)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 Tip: Use --branch feature/user-auth to process this branch directly
```

### Example 2: Staged Changes with Auto-commit

```bash
$ git add src/api.py src/tests/test_api.py

$ python git_commit_agent.py --staged --auto-commit

📝 Analyzing staged changes...

Suggested commit message:

fix(api): resolve race condition in session handling

- Add mutex lock to session update operations
- Prevent concurrent session modifications
- Add integration test for concurrent requests

Files changed: 2 files (+45, -8)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Commit with this message? [y/N]: y
✅ Committed successfully!
```

### Example 3: JSON Output for CI/CD

```bash
$ python git_commit_agent.py --staged --json

{
  "branch": "main",
  "staged": true,
  "suggested_message": "feat(api): add user profile endpoint\n\n- Implement GET /api/users/:id endpoint\n- Add user profile validation\n- Include profile picture upload support",
  "files_changed": 3,
  "insertions": 127,
  "deletions": 5
}
```

## Testing

Run the test suite:

```bash
# Install pytest if not already installed
pip install pytest

# Run tests
pytest test_git_commit_agent.py -v
```

## Troubleshooting

### "ANTHROPIC_API_KEY environment variable not set"

Make sure you've set your API key:
```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

To make it permanent, add it to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.).

### "Not a git repository"

Run the command from within a git repository:
```bash
cd /path/to/your/git/repo
python git_commit_agent.py
```

### "No branches with unpushed commits found"

This means all your branches are up-to-date with their remotes. Try:
- Making some changes and committing them
- Using `--staged` mode for uncommitted changes

### Rate Limiting

If you hit API rate limits, the agent will automatically retry with exponential backoff. You can also:
- Reduce the `max_diff_chars` in config to send less data
- Wait a moment between requests

### Large Diffs

For very large diffs (>50k characters), the agent automatically truncates the content. You can adjust this in the config:
```yaml
max_diff_chars: 100000  # Increase limit
```

## How It Works

1. **Branch Discovery**: Uses `git for-each-ref` to find branches ahead of their upstream
2. **Diff Collection**: Collects `git diff`, `git diff --stat`, and `git log` for context
3. **AI Generation**: Sends diff to Claude with a structured prompt
4. **Format Validation**: Ensures output follows Conventional Commits format
5. **Interactive Display**: Shows results with formatting and statistics

## Best Practices

1. **Review Generated Messages**: Always review before committing
2. **Keep Changes Focused**: Smaller, focused commits generate better messages
3. **Use Staged Mode**: Stage related changes together for coherent messages
4. **Configure Per-Project**: Use repo-specific config for team conventions
5. **Combine with Git Hooks**: Integrate into pre-commit workflows

## Advanced Usage

### Custom Prompt Templates

While the agent uses a built-in prompt, you can modify the source code to customize the prompt template in the `generate_commit_message()` function.

### CI/CD Integration

Use JSON output in your CI/CD pipeline:

```bash
# Generate message and capture output
MESSAGE=$(python git_commit_agent.py --staged --json | jq -r '.suggested_message')

# Use in automated commit
git commit -m "$MESSAGE"
```

### Pre-commit Hook

Create `.git/hooks/prepare-commit-msg`:

```bash
#!/bin/bash
# Auto-generate commit message if none provided

if [ -z "$2" ]; then
    python /path/to/git_commit_agent.py --staged --json | \
        jq -r '.suggested_message' > "$1"
fi
```

## Contributing

Contributions are welcome! Areas for improvement:

- Support for more commit message formats
- Integration with other AI providers
- PR description generation
- Commit message templates
- Multi-language support

## License

MIT License - feel free to use and modify as needed.

## Credits

Built with:
- [Anthropic Claude](https://www.anthropic.com/) - AI-powered message generation
- [Conventional Commits](https://www.conventionalcommits.org/) - Commit message format

---

**Need help?** Open an issue or check the troubleshooting section above.
