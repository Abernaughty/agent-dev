# Git Commit Message Agent

An AI-powered tool that generates meaningful commit messages and creates pull requests using Claude AI by analyzing your git changes.

## Features

- 🤖 **AI-Powered Messages** - Generates conventional commit messages using Claude
- 📝 **Smart Default Behavior** - Analyzes staged changes automatically
- 🔒 **Safety Checks** - Validates git state before operations
- 🚀 **PR Creation** - Complete workflow from commit to pull request
- 🎯 **Interactive Prompts** - Always confirms before making changes
- 🔧 **Configurable** - Customize via YAML config file
- 📊 **JSON Output** - Perfect for CI/CD integration

## Requirements

- Python 3.8+
- Git installed and accessible in PATH
- Anthropic API key
- GitHub CLI (optional, for PR creation)

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

4. **Optional: Install GitHub CLI** (for PR creation):
   ```bash
   # See: https://cli.github.com/
   ```

5. **Optional: Make executable** (Linux/Mac):
   ```bash
   chmod +x git_commit_agent.py
   ```

## Usage

### Quick Start

The most common workflow:

```bash
# 1. Make your changes
echo "new feature" > feature.txt

# 2. Stage them
git add feature.txt

# 3. Generate commit message and commit
python git_commit_agent.py
# Review the message, type 'y' to commit
```

That's it! The tool now:
- Analyzes your staged changes by default
- Generates a commit message
- Prompts you to confirm before committing

### Create a Pull Request

```bash
# After making commits on your feature branch
python git_commit_agent.py --pr
```

This will:
1. Check git state (blocks if merge/rebase in progress)
2. Validate branch status (offers to pull/push if needed)
3. Generate AI-powered PR description
4. Create PR via GitHub CLI or open browser

### Process Specific Branch

```bash
python git_commit_agent.py --branch feature/user-auth
```

### JSON Output (for CI/CD)

```bash
# Commit message as JSON
python git_commit_agent.py --json

# PR description as JSON
python git_commit_agent.py --pr --json
```

## Command-Line Options

| Option | Description |
|--------|-------------|
| *(no flags)* | Generate message for staged changes (default) |
| `--pr` | Create pull request for current branch |
| `--branch <name>` | Process a specific branch |
| `--json` | Output results as JSON (no interactive prompts) |
| `--staged` | *(Deprecated)* Now default behavior |
| `--auto-commit` | *(Deprecated)* Now default behavior |

## Safety Features

The tool includes comprehensive safety checks:

### Git State Validation
- ❌ **Blocks** if merge/rebase/cherry-pick/revert in progress
- ⚠️ **Warns** if in detached HEAD state
- ℹ️ **Informs** if this is your first commit

### PR Workflow Safety
- ⚠️ **Offers to pull** if branch is behind upstream
- ❌ **Blocks** if branch has diverged (requires rebase)
- ⚠️ **Warns** about unstaged changes
- ⚠️ **Offers to push** unpushed commits before PR creation

## Configuration

Create a configuration file to customize behavior:

**Global config:** `~/.git-commit-agent.yaml`  
**Per-repo config:** `{repo_root}/.git-commit-agent.yaml`

**Example configuration:**
```yaml
# Maximum characters from diff to send to AI
max_diff_chars: 50000

# Claude model to use
model: claude-sonnet-4-5-20250929

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

## PR Description Format

For pull requests, the agent generates structured descriptions:

```markdown
## Overview
Brief summary of what this PR does

## Changes
- Key change 1
- Key change 2
- Key change 3

## Testing
How this was tested (if applicable)
```

## Git Alias Setup

Add a convenient git alias:

**Linux/Mac:**
```bash
git config --global alias.ai-commit '!python /absolute/path/to/git_commit_agent.py'
git config --global alias.ai-pr '!python /absolute/path/to/git_commit_agent.py --pr'
```

**Windows:**
```bash
git config --global alias.ai-commit "!python C:/absolute/path/to/git_commit_agent.py"
git config --global alias.ai-pr "!python C:/absolute/path/to/git_commit_agent.py --pr"
```

**Usage:**
```bash
git add .
git ai-commit

# Later, create PR
git ai-pr
```

## Examples

### Example 1: Default Workflow (Staged Changes)

```bash
$ git add src/api.py src/tests/test_api.py

$ python git_commit_agent.py

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

### Example 2: No Staged Changes (Helpful Guidance)

```bash
$ python git_commit_agent.py

ℹ️  No staged changes found.

Modified files (not staged):
  - src/api.py
  - src/tests/test_api.py

Stage changes with:
  git add <files>

Or create a PR for current branch:
  python git_commit_agent.py --pr
```

### Example 3: Complete PR Workflow

```bash
$ python git_commit_agent.py --pr

🔄 Preparing to create pull request...

⚠️  Branch has 2 unpushed commit(s)
Push commits now? [y/N]: y
🔄 Pushing feature/user-auth to origin...
✅ Pushed successfully!

📝 Generating PR description against main...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 Pull Request Preview
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Title: Implement JWT authentication system
Branch: feature/user-auth → main
Files: 8 files (+342, -12)

Description:
## Overview
This PR implements a complete JWT-based authentication system with token
generation, validation, and secure password hashing.

## Changes
- Add JWT token generation and validation middleware
- Implement user login and registration endpoints
- Add password hashing with bcrypt
- Create comprehensive authentication tests

## Testing
Added integration tests covering login, registration, and token validation
with 95% code coverage.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Create PR with this description? [y/N]: y
✅ Pull request created successfully!
https://github.com/user/repo/pull/123
```

### Example 4: Safety Check (Merge in Progress)

```bash
$ python git_commit_agent.py

❌ Error: Repository is in the middle of a merge

Please resolve the ongoing operation before committing:
  git merge --abort  # to abort the merge
  # or resolve conflicts and commit
```

### Example 5: JSON Output for CI/CD

```bash
$ python git_commit_agent.py --json

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

### Run Unit Tests

```bash
# Install pytest if not already installed
pip install pytest

# Run tests
pytest test_git_commit_agent.py -v
```

### Comprehensive Testing

See `TESTING_GUIDE.md` for detailed testing instructions covering:
- All core functionality
- Safety checks and edge cases
- PR workflow scenarios
- Integration tests
- Performance tests

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

### "No staged changes found"

Stage your changes first:
```bash
git add <files>
python git_commit_agent.py
```

### "Repository is in the middle of a merge"

Complete or abort the ongoing operation:
```bash
# Abort the merge
git merge --abort

# Or resolve conflicts and commit
git add <resolved-files>
git commit
```

### Rate Limiting

If you hit API rate limits, the agent will automatically retry with exponential backoff. You can also:
- Reduce the `max_diff_chars` in config to send less data
- Wait a moment between requests

### Large Diffs

For very large diffs (>50k characters), the agent automatically truncates the content. You can adjust this in the config:
```yaml
max_diff_chars: 100000  # Increase limit
```

### PR Creation Issues

**GitHub CLI not found:**
- Install from: https://cli.github.com/
- Or use browser fallback (opens automatically)

**Push rejected:**
- Pull latest changes: `git pull`
- Or rebase: `git rebase origin/main`

**Branch has diverged:**
- Rebase your branch: `git rebase origin/main`
- Or merge: `git merge origin/main`

## How It Works

### Commit Message Generation
1. **Git Analysis**: Collects `git diff --cached`, `git diff --stat`, and file changes
2. **Safety Checks**: Validates git state (no merge/rebase in progress, etc.)
3. **AI Generation**: Sends diff to Claude with structured prompt
4. **Format Validation**: Ensures output follows Conventional Commits format
5. **Interactive Confirmation**: Prompts user before committing

### PR Creation Workflow
1. **State Validation**: Checks for merge/rebase in progress, detached HEAD
2. **Branch Analysis**: Checks if ahead/behind/diverged from upstream
3. **Safety Prompts**: Offers to pull/push as needed
4. **Merge Base Detection**: Finds common ancestor with base branch (main/master)
5. **AI Generation**: Creates PR description from all commits since merge base
6. **PR Creation**: Uses GitHub CLI or opens browser as fallback

## Best Practices

1. **Review Generated Messages**: Always review before confirming
2. **Keep Changes Focused**: Smaller, focused commits generate better messages
3. **Stage Related Changes**: Group related changes for coherent messages
4. **Use Safety Checks**: Let the tool guide you through git state issues
5. **Configure Per-Project**: Use repo-specific config for team conventions
6. **Test Before PR**: Ensure tests pass before creating pull requests

## Advanced Usage

### CI/CD Integration

Use JSON output in your CI/CD pipeline:

```bash
# Generate message and capture output
MESSAGE=$(python git_commit_agent.py --json | jq -r '.suggested_message')

# Use in automated commit
git commit -m "$MESSAGE"
```

### Pre-commit Hook

Create `.git/hooks/prepare-commit-msg`:

```bash
#!/bin/bash
# Auto-generate commit message if none provided

if [ -z "$2" ]; then
    python /path/to/git_commit_agent.py --json | \
        jq -r '.suggested_message' > "$1"
fi
```

### Custom Prompts

Modify the prompts in `generate_commit_message()` or `generate_pr_description()` functions to customize AI behavior.

## Migration from v1.x

If you were using the old version:

**Old way:**
```bash
python git_commit_agent.py --staged --auto-commit
```

**New way (simpler):**
```bash
python git_commit_agent.py
```

The `--staged` and `--auto-commit` flags are deprecated but still work with warnings.

## What's New in v2.0

- ✅ **Staged changes are now the default** (no flags needed)
- ✅ **Always prompts before committing** (safer)
- ✅ **Complete PR workflow** with `--pr` flag
- ✅ **Comprehensive safety checks** (merge detection, branch validation)
- ✅ **Better error messages** with helpful guidance
- ✅ **GitHub CLI integration** for PR creation
- ✅ **Intelligent fallbacks** (browser PR creation, base branch detection)

## Contributing

Contributions are welcome! Areas for improvement:

- Support for more commit message formats
- Integration with other AI providers
- Additional PR templates
- Multi-language support
- GitLab/Bitbucket support

## License

MIT License - feel free to use and modify as needed.

## Credits

Built with:
- [Anthropic Claude](https://www.anthropic.com/) - AI-powered message generation
- [Conventional Commits](https://www.conventionalcommits.org/) - Commit message format
- [GitHub CLI](https://cli.github.com/) - PR creation

---

**Need help?** Check `TESTING_GUIDE.md` or open an issue.
