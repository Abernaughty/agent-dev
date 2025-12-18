#!/usr/bin/env python3
"""
Git Commit Message Agent

Automatically generates meaningful commit messages using Claude AI by analyzing
git diffs and branch changes.

Usage:
    python git_commit_agent.py                    # Generate message for staged changes (default)
    python git_commit_agent.py --branch feature/x # Process specific branch
    python git_commit_agent.py --pr               # Create PR for current branch
    python git_commit_agent.py --json             # Output as JSON

Requirements:
    - Python 3.8+
    - anthropic package (pip install anthropic)
    - ANTHROPIC_API_KEY environment variable
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple
import re

try:
    import anthropic
except ImportError:
    print("❌ Error: 'anthropic' package not found.")
    print("Install it with: pip install anthropic")
    sys.exit(1)

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULT_CONFIG = {
    "max_diff_chars": 50000,
    "model": "claude-sonnet-4-5-20250929",
    "temperature": 0.7,
    "max_retries": 3,
    "commit_types": [
        "feat", "fix", "docs", "style", "refactor",
        "test", "chore", "perf", "ci", "build"
    ]
}

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class BranchInfo:
    """Information about a git branch and its changes."""
    name: str
    upstream: Optional[str]
    ahead: int
    behind: int
    diff_stat: str
    diff_content: str
    commit_log: str
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass
class Config:
    """Configuration for the agent."""
    max_diff_chars: int
    model: str
    temperature: float
    max_retries: int
    commit_types: List[str]


# ============================================================================
# GIT OPERATIONS
# ============================================================================

def run_git(args: List[str], check: bool = True) -> Tuple[bool, str]:
    """
    Execute a git command and return success status and output.
    
    Args:
        args: Git command arguments (without 'git' prefix)
        check: Whether to raise on non-zero exit code
        
    Returns:
        Tuple of (success, output)
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=check
        )
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if check:
            return False, e.stderr.strip() if e.stderr else ""
        return False, e.stdout.strip() if e.stdout else ""
    except FileNotFoundError:
        print("❌ Error: git command not found. Please install git.")
        sys.exit(1)


def is_git_repo() -> bool:
    """Check if current directory is a git repository."""
    success, _ = run_git(["rev-parse", "--git-dir"], check=False)
    return success


def get_repo_root() -> Optional[str]:
    """Get the root directory of the git repository."""
    success, output = run_git(["rev-parse", "--show-toplevel"], check=False)
    return output if success else None


def parse_tracking_info(track_info: str) -> Tuple[int, int]:
    """
    Parse git tracking info like '[ahead 2, behind 1]' or '[ahead 3]'.
    
    Returns:
        Tuple of (ahead, behind)
    """
    ahead = 0
    behind = 0
    
    if not track_info:
        return ahead, behind
    
    ahead_match = re.search(r'ahead (\d+)', track_info)
    behind_match = re.search(r'behind (\d+)', track_info)
    
    if ahead_match:
        ahead = int(ahead_match.group(1))
    if behind_match:
        behind = int(behind_match.group(1))
    
    return ahead, behind


def get_all_branches_with_status() -> List[Tuple[str, Optional[str], int, int]]:
    """
    Get all branches with their upstream tracking status.
    
    Returns:
        List of tuples: (branch_name, upstream, ahead, behind)
    """
    success, output = run_git([
        "for-each-ref",
        "--format=%(refname:short)|%(upstream:short)|%(upstream:track)",
        "refs/heads"
    ])
    
    if not success or not output:
        return []
    
    branches = []
    for line in output.split('\n'):
        if not line:
            continue
        
        parts = line.split('|')
        branch_name = parts[0]
        upstream = parts[1] if len(parts) > 1 and parts[1] else None
        track_info = parts[2] if len(parts) > 2 else ""
        
        ahead, behind = parse_tracking_info(track_info)
        
        # Only include branches that are ahead (have unpushed commits)
        if ahead > 0:
            branches.append((branch_name, upstream, ahead, behind))
    
    return branches


def get_branch_diff(branch: str, upstream: Optional[str], config: Config) -> Optional[BranchInfo]:
    """
    Get detailed diff information for a branch.
    
    Args:
        branch: Branch name
        upstream: Upstream branch name
        config: Configuration object
        
    Returns:
        BranchInfo object or None if error
    """
    if not upstream:
        # Try to find default branch
        success, default_branch = run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], check=False)
        if success:
            upstream = default_branch.replace("refs/remotes/", "")
        else:
            # Fallback to common default branches
            for candidate in ["origin/main", "origin/master", "main", "master"]:
                success, _ = run_git(["rev-parse", "--verify", candidate], check=False)
                if success:
                    upstream = candidate
                    break
        
        if not upstream:
            print(f"⚠️  Warning: No upstream found for {branch}, skipping...")
            return None
    
    # Get diff stat
    success, diff_stat = run_git(["diff", "--stat", f"{upstream}..{branch}"])
    if not success:
        return None
    
    # Get full diff
    success, diff_content = run_git(["diff", f"{upstream}..{branch}"])
    if not success:
        return None
    
    # Truncate if too large
    original_size = len(diff_content)
    if len(diff_content) > config.max_diff_chars:
        diff_content = diff_content[:config.max_diff_chars]
        diff_content += f"\n\n... [Truncated: showing first {config.max_diff_chars} of {original_size} characters]"
    
    # Get commit log
    success, commit_log = run_git(["log", "--oneline", f"{upstream}..{branch}"])
    if not success:
        commit_log = ""
    
    # Parse file statistics
    files_changed = 0
    insertions = 0
    deletions = 0
    
    stat_match = re.search(r'(\d+) files? changed', diff_stat)
    if stat_match:
        files_changed = int(stat_match.group(1))
    
    insert_match = re.search(r'(\d+) insertions?', diff_stat)
    if insert_match:
        insertions = int(insert_match.group(1))
    
    delete_match = re.search(r'(\d+) deletions?', diff_stat)
    if delete_match:
        deletions = int(delete_match.group(1))
    
    # Get ahead/behind info
    success, track_output = run_git([
        "for-each-ref",
        "--format=%(upstream:track)",
        f"refs/heads/{branch}"
    ])
    ahead, behind = parse_tracking_info(track_output if success else "")
    
    return BranchInfo(
        name=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        diff_stat=diff_stat,
        diff_content=diff_content,
        commit_log=commit_log,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions
    )


# ============================================================================
# GIT STATE VALIDATION
# ============================================================================

def check_git_state() -> Tuple[str, Optional[str]]:
    """
    Check if git is in a special state.
    
    Returns:
        Tuple of (state, message) where state is one of:
        'clean', 'merging', 'rebasing', 'cherry-picking', 'reverting'
    """
    git_dir = Path(get_repo_root() or ".") / ".git"
    
    # Check for merge
    if (git_dir / "MERGE_HEAD").exists():
        return 'merging', "Repository is in the middle of a merge"
    
    # Check for rebase
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return 'rebasing', "Repository is in the middle of a rebase"
    
    # Check for cherry-pick
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        return 'cherry-picking', "Repository is in the middle of a cherry-pick"
    
    # Check for revert
    if (git_dir / "REVERT_HEAD").exists():
        return 'reverting', "Repository is in the middle of a revert"
    
    return 'clean', None


def check_working_directory_clean() -> Tuple[bool, dict]:
    """
    Check if working directory is clean.
    
    Returns:
        Tuple of (is_clean, {'unstaged': [...], 'untracked': [...]})
    """
    success, output = run_git(["status", "--porcelain"], check=False)
    
    if not success:
        return True, {'unstaged': [], 'untracked': []}
    
    unstaged = []
    untracked = []
    
    for line in output.split('\n'):
        if not line:
            continue
        
        status = line[:2]
        filename = line[3:] if len(line) > 3 else ""
        
        # Untracked files
        if status == '??':
            untracked.append(filename)
        # Modified but not staged
        elif status[1] in ['M', 'D']:
            unstaged.append(filename)
        # Deleted but not staged
        elif status[0] == ' ' and status[1] == 'D':
            unstaged.append(filename)
    
    is_clean = len(unstaged) == 0 and len(untracked) == 0
    return is_clean, {'unstaged': unstaged, 'untracked': untracked}


def check_branch_divergence(branch: str) -> dict:
    """
    Check if branch has diverged from upstream.
    
    Returns:
        {
            'ahead': int,
            'behind': int,
            'diverged': bool,
            'needs_rebase': bool,
            'needs_pull': bool,
            'upstream': str
        }
    """
    # Get upstream info
    success, output = run_git([
        "for-each-ref",
        "--format=%(upstream:short)|%(upstream:track)",
        f"refs/heads/{branch}"
    ], check=False)
    
    if not success or not output:
        return {
            'ahead': 0,
            'behind': 0,
            'diverged': False,
            'needs_rebase': False,
            'needs_pull': False,
            'upstream': None
        }
    
    parts = output.split('|')
    upstream = parts[0] if parts[0] else None
    track_info = parts[1] if len(parts) > 1 else ""
    
    ahead, behind = parse_tracking_info(track_info)
    
    diverged = ahead > 0 and behind > 0
    needs_rebase = diverged
    needs_pull = behind > 0 and ahead == 0
    
    return {
        'ahead': ahead,
        'behind': behind,
        'diverged': diverged,
        'needs_rebase': needs_rebase,
        'needs_pull': needs_pull,
        'upstream': upstream
    }


def check_detached_head() -> bool:
    """Check if HEAD is detached."""
    success, output = run_git(["symbolic-ref", "-q", "HEAD"], check=False)
    return not success


def check_empty_repository() -> bool:
    """Check if repository has no commits yet."""
    success, _ = run_git(["rev-parse", "HEAD"], check=False)
    return not success


def is_interactive() -> bool:
    """Check if running in interactive terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def get_staged_changes(config: Config) -> Optional[BranchInfo]:
    """
    Get information about currently staged changes.
    
    Returns:
        BranchInfo object or None if no staged changes
    """
    # Check if there are staged changes
    success, status = run_git(["diff", "--cached", "--name-only"])
    if not success or not status:
        return None
    
    # Get diff stat
    success, diff_stat = run_git(["diff", "--cached", "--stat"])
    if not success:
        return None
    
    # Get full diff
    success, diff_content = run_git(["diff", "--cached"])
    if not success:
        return None
    
    # Truncate if too large
    original_size = len(diff_content)
    if len(diff_content) > config.max_diff_chars:
        diff_content = diff_content[:config.max_diff_chars]
        diff_content += f"\n\n... [Truncated: showing first {config.max_diff_chars} of {original_size} characters]"
    
    # Get current branch
    success, current_branch = run_git(["branch", "--show-current"])
    branch_name = current_branch if success else "HEAD"
    
    # Parse file statistics
    files_changed = 0
    insertions = 0
    deletions = 0
    
    stat_match = re.search(r'(\d+) files? changed', diff_stat)
    if stat_match:
        files_changed = int(stat_match.group(1))
    
    insert_match = re.search(r'(\d+) insertions?', diff_stat)
    if insert_match:
        insertions = int(insert_match.group(1))
    
    delete_match = re.search(r'(\d+) deletions?', diff_stat)
    if delete_match:
        deletions = int(delete_match.group(1))
    
    return BranchInfo(
        name=branch_name,
        upstream=None,
        ahead=0,
        behind=0,
        diff_stat=diff_stat,
        diff_content=diff_content,
        commit_log="",
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions
    )


# ============================================================================
# PR WORKFLOW FUNCTIONS
# ============================================================================

def get_merge_base(branch: str, base: str = "main") -> Optional[str]:
    """
    Get merge base between branch and base.
    
    Args:
        branch: Current branch name
        base: Base branch name (default: main)
        
    Returns:
        Merge base commit SHA or None if error
    """
    # Try with origin prefix first
    success, merge_base = run_git(["merge-base", "HEAD", f"origin/{base}"], check=False)
    if success and merge_base:
        return merge_base
    
    # Try without origin prefix
    success, merge_base = run_git(["merge-base", "HEAD", base], check=False)
    if success and merge_base:
        return merge_base
    
    return None


def has_gh_cli() -> bool:
    """Check if GitHub CLI is installed."""
    try:
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def safe_push(branch: str) -> bool:
    """
    Safely push branch to remote.
    
    Args:
        branch: Branch name to push
        
    Returns:
        True if successful, False otherwise
    """
    print(f"🔄 Pushing {branch} to origin...")
    success, output = run_git(["push", "origin", branch], check=False)
    
    if success:
        print("✅ Pushed successfully!")
        return True
    else:
        print(f"❌ Push failed: {output}")
        return False


def create_pr_with_gh(title: str, body: str, head: str, base: str = "main") -> bool:
    """
    Create PR using GitHub CLI.
    
    Args:
        title: PR title
        body: PR description
        head: Source branch name
        base: Base branch (default: main)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--head", head, "--base", base],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            print("✅ Pull request created successfully!")
            print(result.stdout.strip())
            return True
        else:
            print(f"❌ Failed to create PR: {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"❌ Error creating PR: {e}")
        return False


def open_pr_in_browser(branch: str, base: str = "main"):
    """
    Open GitHub PR creation page in browser.
    
    Args:
        branch: Source branch
        base: Base branch (default: main)
    """
    # Get remote URL
    success, remote_url = run_git(["config", "--get", "remote.origin.url"], check=False)
    
    if not success or not remote_url:
        print("❌ Could not determine remote URL")
        return
    
    # Parse GitHub URL
    # Handle both SSH and HTTPS formats
    if remote_url.startswith("git@github.com:"):
        # SSH format: git@github.com:user/repo.git
        repo_path = remote_url.replace("git@github.com:", "").replace(".git", "")
    elif "github.com" in remote_url:
        # HTTPS format: https://github.com/user/repo.git
        repo_path = remote_url.split("github.com/")[1].replace(".git", "")
    else:
        print("❌ Not a GitHub repository")
        return
    
    # Construct PR URL
    pr_url = f"https://github.com/{repo_path}/compare/{base}...{branch}?expand=1"
    
    print(f"\n🌐 Opening PR creation page in browser...")
    print(f"   {pr_url}")
    
    # Open in browser
    import webbrowser
    webbrowser.open(pr_url)


def generate_pr_description(branch_info: BranchInfo, config: Config) -> Optional[str]:
    """
    Generate a PR description using Claude AI.
    
    Args:
        branch_info: Information about the branch/changes
        config: Configuration object
        
    Returns:
        Generated PR description or None if error
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ Error: ANTHROPIC_API_KEY environment variable not set.")
        return None
    
    client = anthropic.Anthropic(api_key=api_key)
    
    # Build prompt for PR description
    prompt = f"""Analyze this git diff and generate a pull request description.

Branch: {branch_info.name}
Base: {branch_info.upstream}
Commits: {branch_info.ahead} commits ahead

Commit messages:
{branch_info.commit_log}

Files changed:
{branch_info.diff_stat}

Diff content:
{branch_info.diff_content}

Generate a pull request description following this format:

## Overview
[Brief summary of what this PR does]

## Changes
[Bullet points of key changes]

## Testing
[How this was tested, if applicable]

Requirements:
1. Keep the overview concise (2-3 sentences)
2. List key changes as bullet points
3. Mention testing approach if test files were modified
4. Use clear, professional language
5. Focus on WHAT changed and WHY, not HOW

Return ONLY the PR description, no explanations or additional text."""

    # Call Claude API
    for attempt in range(config.max_retries):
        try:
            message = client.messages.create(
                model=config.model,
                max_tokens=2048,
                temperature=config.temperature,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            if message.content and len(message.content) > 0:
                return message.content[0].text.strip()
            
        except anthropic.RateLimitError:
            if attempt < config.max_retries - 1:
                wait_time = 2 ** attempt
                print(f"⚠️  Rate limited. Waiting {wait_time}s before retry...")
                import time
                time.sleep(wait_time)
            else:
                print("❌ Error: Rate limit exceeded after retries.")
                return None
                
        except anthropic.APIError as e:
            print(f"❌ API Error: {e}")
            return None
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return None
    
    return None


# ============================================================================
# AI INTEGRATION
# ============================================================================

def generate_commit_message(branch_info: BranchInfo, config: Config) -> Optional[str]:
    """
    Generate a commit message using Claude AI.
    
    Args:
        branch_info: Information about the branch/changes
        config: Configuration object
        
    Returns:
        Generated commit message or None if error
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ Error: ANTHROPIC_API_KEY environment variable not set.")
        print("Set it with: export ANTHROPIC_API_KEY='your-api-key'")
        return None
    
    client = anthropic.Anthropic(api_key=api_key)
    
    # Build prompt
    prompt = f"""Analyze this git diff and generate a commit message following the Conventional Commits format.

Branch: {branch_info.name}
{f"Upstream: {branch_info.upstream}" if branch_info.upstream else "Staged changes"}
{f"Commits ahead: {branch_info.ahead}" if branch_info.ahead > 0 else ""}

{f"Existing commit messages:\n{branch_info.commit_log}\n" if branch_info.commit_log else ""}
Files changed:
{branch_info.diff_stat}

Diff content:
{branch_info.diff_content}

Generate a commit message following this format:

<type>(<scope>): <subject>

<body>

Requirements:
1. First line: type(scope): subject (max 72 characters)
2. Type must be one of: {', '.join(config.commit_types)}
3. Scope is required and should be a short noun describing the affected area
4. Subject should be imperative mood ("add" not "added")
5. Body should use bullet points starting with "-" to explain:
   - What changed
   - Why it changed (if not obvious)
6. Do NOT include any footer (no "Closes #", "Breaking Change:", etc.)
7. Keep the message concise but informative

Return ONLY the commit message, no explanations or additional text."""

    # Call Claude API with retry logic
    for attempt in range(config.max_retries):
        try:
            message = client.messages.create(
                model=config.model,
                max_tokens=1024,
                temperature=config.temperature,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            # Extract text from response
            if message.content and len(message.content) > 0:
                return message.content[0].text.strip()
            
        except anthropic.RateLimitError:
            if attempt < config.max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                print(f"⚠️  Rate limited. Waiting {wait_time}s before retry...")
                import time
                time.sleep(wait_time)
            else:
                print("❌ Error: Rate limit exceeded after retries.")
                return None
                
        except anthropic.APIError as e:
            print(f"❌ API Error: {e}")
            return None
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return None
    
    return None


# ============================================================================
# CONFIGURATION LOADING
# ============================================================================

def load_config() -> Config:
    """
    Load configuration from file or use defaults.
    
    Checks in order:
    1. {repo_root}/.git-commit-agent.yaml
    2. ~/.git-commit-agent.yaml
    3. Default configuration
    """
    config_dict = DEFAULT_CONFIG.copy()
    
    if not YAML_AVAILABLE:
        return Config(**config_dict)
    
    # Check repo root
    repo_root = get_repo_root()
    config_paths = []
    
    if repo_root:
        config_paths.append(Path(repo_root) / ".git-commit-agent.yaml")
    
    # Check home directory
    home = Path.home()
    config_paths.append(home / ".git-commit-agent.yaml")
    
    # Load first found config
    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    user_config = yaml.safe_load(f)
                    if user_config:
                        config_dict.update(user_config)
                        print(f"📝 Loaded config from: {config_path}")
                        break
            except Exception as e:
                print(f"⚠️  Warning: Could not load config from {config_path}: {e}")
    
    return Config(**config_dict)


# ============================================================================
# OUTPUT FORMATTING
# ============================================================================

def print_branch_info(branch_info: BranchInfo, commit_message: str):
    """Print formatted branch information and commit message."""
    print("\n" + "━" * 80)
    print(f"📌 {branch_info.name}", end="")
    if branch_info.ahead > 0:
        print(f" ({branch_info.ahead} commits ahead of {branch_info.upstream})")
    else:
        print(" (staged changes)")
    print("━" * 80)
    print("\nSuggested commit message:\n")
    print(commit_message)
    print(f"\nFiles changed: {branch_info.files_changed} files ", end="")
    print(f"(+{branch_info.insertions}, -{branch_info.deletions})")
    print("━" * 80)


def interactive_select_branch(branches: List[Tuple[str, Optional[str], int, int]]) -> Optional[str]:
    """
    Display interactive menu to select a branch.
    
    Args:
        branches: List of (branch_name, upstream, ahead, behind) tuples
        
    Returns:
        Selected branch name or None if cancelled
    """
    if not branches:
        return None
    
    print("\n🔍 Found branches with unpushed commits:\n")
    for idx, (branch, upstream, ahead, behind) in enumerate(branches, 1):
        print(f"  {idx}. {branch} ({ahead} commits ahead of {upstream})")
    
    print(f"  0. Exit")
    
    while True:
        try:
            choice = input("\nSelect a branch (0 to exit): ").strip()
            choice_num = int(choice)
            
            if choice_num == 0:
                return None
            
            if 1 <= choice_num <= len(branches):
                return branches[choice_num - 1][0]
            
            print("❌ Invalid selection. Please try again.")
        except ValueError:
            print("❌ Please enter a number.")
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.")
            return None


# ============================================================================
# MAIN LOGIC
# ============================================================================

def main():
    """Main entry point for the git commit agent."""
    parser = argparse.ArgumentParser(
        description="Generate AI-powered commit messages using Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Generate message for staged changes (default)
  %(prog)s --branch feature/auth    # Process specific branch
  %(prog)s --pr                     # Create PR for current branch
  %(prog)s --json                   # Output as JSON
        """
    )
    
    parser.add_argument(
        "--staged",
        action="store_true",
        help="(Deprecated: now default behavior) Generate message for staged changes"
    )
    
    parser.add_argument(
        "--branch",
        type=str,
        help="Process specific branch"
    )
    
    parser.add_argument(
        "--auto-commit",
        action="store_true",
        help="(Deprecated: now default behavior) Automatically commit with generated message"
    )
    
    parser.add_argument(
        "--pr",
        action="store_true",
        help="Create pull request for current branch (coming soon)"
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    
    args = parser.parse_args()
    
    # Show deprecation warnings
    if args.staged:
        print("⚠️  Warning: --staged is now the default behavior and will be removed in v2.0")
        print("    Simply run 'python git_commit_agent.py' instead\n")
    
    if args.auto_commit:
        print("⚠️  Warning: --auto-commit is now default behavior and will be removed in v2.0")
        print("    Use --json to disable interactive prompts\n")
    
    # Validate we're in a git repo
    if not is_git_repo():
        print("❌ Error: Not a git repository.")
        print("Run this command from within a git repository.")
        sys.exit(1)
    
    # Load configuration
    config = load_config()
    
    # Handle PR mode
    if args.pr:
        if not args.json:
            print("🔄 Preparing to create pull request...")
        
        # Get current branch
        success, current_branch = run_git(["branch", "--show-current"])
        if not success or not current_branch:
            print("❌ Error: Could not determine current branch")
            sys.exit(1)
        
        # Check git state
        state, state_msg = check_git_state()
        if state != 'clean':
            print(f"❌ Error: {state_msg}")
            print("\nPlease resolve the ongoing operation before creating a PR.")
            sys.exit(1)
        
        # Check for detached HEAD
        if check_detached_head():
            print("❌ Error: Cannot create PR from detached HEAD state")
            sys.exit(1)
        
        # Check branch divergence
        divergence = check_branch_divergence(current_branch)
        
        if divergence['needs_pull']:
            print(f"⚠️  Branch is {divergence['behind']} commits behind {divergence['upstream']}")
            if is_interactive() and not args.json:
                confirm = input("Pull latest changes? [y/N]: ").strip().lower()
                if confirm == 'y':
                    print("🔄 Pulling latest changes...")
                    success, output = run_git(["pull"], check=False)
                    if not success:
                        print(f"❌ Pull failed: {output}")
                        sys.exit(1)
                    print("✅ Pulled successfully!")
                else:
                    print("❌ Please pull latest changes before creating PR")
                    sys.exit(1)
            else:
                print("❌ Please pull latest changes before creating PR")
                sys.exit(1)
        
        if divergence['diverged']:
            print(f"⚠️  Branch has diverged: {divergence['ahead']} ahead, {divergence['behind']} behind")
            print("❌ Please rebase or merge before creating PR")
            sys.exit(1)
        
        # Check for unpushed commits
        if divergence['ahead'] == 0:
            print("ℹ️  No unpushed commits found. Nothing to create PR for.")
            sys.exit(0)
        
        # Check for unstaged changes
        is_clean, files = check_working_directory_clean()
        if not is_clean:
            if files['unstaged']:
                print(f"⚠️  Warning: {len(files['unstaged'])} unstaged file(s)")
                if is_interactive() and not args.json:
                    print("\nUnstaged files:")
                    for f in files['unstaged'][:5]:
                        print(f"  - {f}")
                    if len(files['unstaged']) > 5:
                        print(f"  ... and {len(files['unstaged']) - 5} more")
                    
                    confirm = input("\nContinue anyway? [y/N]: ").strip().lower()
                    if confirm != 'y':
                        print("❌ Cancelled")
                        sys.exit(0)
        
        # Offer to push if needed
        if divergence['ahead'] > 0:
            print(f"\n⚠️  Branch has {divergence['ahead']} unpushed commit(s)")
            if is_interactive() and not args.json:
                confirm = input("Push commits now? [y/N]: ").strip().lower()
                if confirm == 'y':
                    if not safe_push(current_branch):
                        sys.exit(1)
                else:
                    print("❌ Please push commits before creating PR")
                    sys.exit(1)
            else:
                print("❌ Please push commits before creating PR")
                sys.exit(1)
        
        # Determine base branch (default to main)
        base_branch = "main"
        
        # Get merge base
        merge_base = get_merge_base(current_branch, base_branch)
        if not merge_base:
            print(f"⚠️  Could not find merge base with {base_branch}, trying master...")
            base_branch = "master"
            merge_base = get_merge_base(current_branch, base_branch)
            if not merge_base:
                print("❌ Could not determine base branch")
                sys.exit(1)
        
        print(f"📝 Generating PR description against {base_branch}...")
        
        # Get diff from merge base
        success, diff_stat = run_git(["diff", "--stat", f"{merge_base}..HEAD"])
        if not success:
            print("❌ Error getting diff")
            sys.exit(1)
        
        success, diff_content = run_git(["diff", f"{merge_base}..HEAD"])
        if not success:
            print("❌ Error getting diff")
            sys.exit(1)
        
        # Truncate if needed
        original_size = len(diff_content)
        if len(diff_content) > config.max_diff_chars:
            diff_content = diff_content[:config.max_diff_chars]
            diff_content += f"\n\n... [Truncated: showing first {config.max_diff_chars} of {original_size} characters]"
        
        # Get commit log
        success, commit_log = run_git(["log", "--oneline", f"{merge_base}..HEAD"])
        if not success:
            commit_log = ""
        
        # Parse stats
        files_changed = 0
        insertions = 0
        deletions = 0
        
        stat_match = re.search(r'(\d+) files? changed', diff_stat)
        if stat_match:
            files_changed = int(stat_match.group(1))
        
        insert_match = re.search(r'(\d+) insertions?', diff_stat)
        if insert_match:
            insertions = int(insert_match.group(1))
        
        delete_match = re.search(r'(\d+) deletions?', diff_stat)
        if delete_match:
            deletions = int(delete_match.group(1))
        
        # Create BranchInfo for PR
        pr_branch_info = BranchInfo(
            name=current_branch,
            upstream=f"origin/{base_branch}",
            ahead=divergence['ahead'],
            behind=0,
            diff_stat=diff_stat,
            diff_content=diff_content,
            commit_log=commit_log,
            files_changed=files_changed,
            insertions=insertions,
            deletions=deletions
        )
        
        # Generate PR description
        pr_description = generate_pr_description(pr_branch_info, config)
        if not pr_description:
            sys.exit(1)
        
        # Extract title from first line of description
        lines = pr_description.split('\n')
        pr_title = lines[0].replace('## Overview', '').strip()
        if not pr_title:
            pr_title = f"PR: {current_branch}"
        
        if args.json:
            output = {
                "branch": current_branch,
                "base": base_branch,
                "title": pr_title,
                "description": pr_description,
                "files_changed": files_changed,
                "insertions": insertions,
                "deletions": deletions
            }
            print(json.dumps(output, indent=2))
        else:
            print("\n" + "━" * 80)
            print("📋 Pull Request Preview")
            print("━" * 80)
            print(f"\nTitle: {pr_title}")
            print(f"Branch: {current_branch} → {base_branch}")
            print(f"Files: {files_changed} files (+{insertions}, -{deletions})")
            print("\nDescription:")
            print(pr_description)
            print("\n" + "━" * 80)
            
            if is_interactive():
                confirm = input("Create PR with this description? [y/N]: ").strip().lower()
                
                if confirm == 'y':
                    # Try GitHub CLI first
                    if has_gh_cli():
                        if create_pr_with_gh(pr_title, pr_description, current_branch, base_branch):
                            sys.exit(0)
                        else:
                            print("\n⚠️  GitHub CLI failed, falling back to browser...")
                    
                    # Fallback to browser
                    open_pr_in_browser(current_branch, base_branch)
                else:
                    print("❌ PR creation cancelled")
        
        sys.exit(0)
    
    # Handle specific branch mode
    if args.branch:
        if not args.json:
            print(f"📝 Analyzing branch: {args.branch}...")
        
        # Get upstream for this branch
        success, output = run_git([
            "for-each-ref",
            "--format=%(upstream:short)|%(upstream:track)",
            f"refs/heads/{args.branch}"
        ])
        
        if not success:
            print(f"❌ Error: Branch '{args.branch}' not found.")
            sys.exit(1)
        
        parts = output.split('|')
        upstream = parts[0] if parts[0] else None
        track_info = parts[1] if len(parts) > 1 else ""
        ahead, behind = parse_tracking_info(track_info)
        
        if ahead == 0:
            print(f"ℹ️  Branch '{args.branch}' has no unpushed commits.")
            sys.exit(0)
        
        branch_info = get_branch_diff(args.branch, upstream, config)
        if not branch_info:
            print(f"❌ Error: Could not get diff for branch '{args.branch}'.")
            sys.exit(1)
        
        commit_message = generate_commit_message(branch_info, config)
        if not commit_message:
            sys.exit(1)
        
        if args.json:
            output = {
                "branch": branch_info.name,
                "upstream": branch_info.upstream,
                "ahead": branch_info.ahead,
                "behind": branch_info.behind,
                "suggested_message": commit_message,
                "files_changed": branch_info.files_changed,
                "insertions": branch_info.insertions,
                "deletions": branch_info.deletions
            }
            print(json.dumps(output, indent=2))
        else:
            print_branch_info(branch_info, commit_message)
        
        sys.exit(0)
    
    # Default mode: Generate message for staged changes
    if not args.json:
        # Check git state before proceeding
        state, state_msg = check_git_state()
        if state != 'clean':
            print(f"❌ Error: {state_msg}")
            print("\nPlease resolve the ongoing operation before committing:")
            if state == 'merging':
                print("  git merge --abort  # to abort the merge")
                print("  # or resolve conflicts and commit")
            elif state == 'rebasing':
                print("  git rebase --abort  # to abort the rebase")
                print("  # or resolve conflicts and continue")
            elif state == 'cherry-picking':
                print("  git cherry-pick --abort  # to abort the cherry-pick")
            elif state == 'reverting':
                print("  git revert --abort  # to abort the revert")
            sys.exit(1)
        
        # Check for detached HEAD
        if check_detached_head():
            print("⚠️  Warning: You are in 'detached HEAD' state.")
            print("    Commits made in this state may be lost.")
            if is_interactive():
                confirm = input("Continue anyway? [y/N]: ").strip().lower()
                if confirm != 'y':
                    print("❌ Cancelled.")
                    sys.exit(0)
        
        # Check for empty repository
        if check_empty_repository():
            print("ℹ️  This appears to be your first commit in this repository.")
        
        print("📝 Analyzing staged changes...")
    
    branch_info = get_staged_changes(config)
    if not branch_info:
        # Show helpful context when no staged changes
        success, status_output = run_git(["status", "--short"], check=False)
        
        print("ℹ️  No staged changes found.\n")
        
        if success and status_output:
            # Parse modified files
            modified_files = []
            for line in status_output.split('\n'):
                if line and not line.startswith('??'):
                    # Extract filename (skip status markers)
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) > 1:
                        modified_files.append(parts[1])
            
            if modified_files:
                print("Modified files (not staged):")
                for f in modified_files[:10]:  # Show max 10 files
                    print(f"  - {f}")
                if len(modified_files) > 10:
                    print(f"  ... and {len(modified_files) - 10} more")
                print()
        
        print("Stage changes with:")
        print("  git add <files>")
        print("\nOr create a PR for current branch:")
        print("  python git_commit_agent.py --pr")
        sys.exit(0)
    
    commit_message = generate_commit_message(branch_info, config)
    if not commit_message:
        sys.exit(1)
    
    if args.json:
        output = {
            "branch": branch_info.name,
            "staged": True,
            "suggested_message": commit_message,
            "files_changed": branch_info.files_changed,
            "insertions": branch_info.insertions,
            "deletions": branch_info.deletions
        }
        print(json.dumps(output, indent=2))
    else:
        print("\nSuggested commit message:\n")
        print(commit_message)
        print(f"\nFiles changed: {branch_info.files_changed} files ", end="")
        print(f"(+{branch_info.insertions}, -{branch_info.deletions})")
        
        # Always prompt for commit (new default behavior)
        print("\n" + "━" * 80)
        confirm = input("Commit with this message? [y/N]: ").strip().lower()
        
        if confirm == 'y':
            # Write message to temp file and commit
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
                f.write(commit_message)
                temp_file = f.name
            
            try:
                success, output = run_git(["commit", "-F", temp_file])
                if success:
                    print("✅ Committed successfully!")
                else:
                    print(f"❌ Commit failed: {output}")
                    sys.exit(1)
            finally:
                os.unlink(temp_file)
        else:
            print("❌ Commit cancelled.")


if __name__ == "__main__":
    main()
