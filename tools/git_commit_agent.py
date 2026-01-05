#!/usr/bin/env python3
"""
Git Commit Message Agent

Automatically generates meaningful commit messages using Claude AI by analyzing
git diffs and branch changes.

Usage:
    python git_commit_agent.py                    # Scan all branches
    python git_commit_agent.py --staged           # Generate message for staged changes
    python git_commit_agent.py --branch feature/x # Process specific branch
    python git_commit_agent.py --staged --auto-commit  # Generate and commit
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
  %(prog)s                          # Scan all branches
  %(prog)s --staged                 # Generate message for staged changes
  %(prog)s --branch feature/auth    # Process specific branch
  %(prog)s --staged --auto-commit   # Generate and commit automatically
  %(prog)s --json                   # Output as JSON
        """
    )
    
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Generate message for staged changes"
    )
    
    parser.add_argument(
        "--branch",
        type=str,
        help="Process specific branch"
    )
    
    parser.add_argument(
        "--auto-commit",
        action="store_true",
        help="Automatically commit with generated message (requires --staged)"
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    
    args = parser.parse_args()
    
    # Validate we're in a git repo
    if not is_git_repo():
        print("❌ Error: Not a git repository.")
        print("Run this command from within a git repository.")
        sys.exit(1)
    
    # Load configuration
    config = load_config()
    
    # Handle staged changes mode
    if args.staged:
        if not args.json:
            print("📝 Analyzing staged changes...")
        
        branch_info = get_staged_changes(config)
        if not branch_info:
            print("ℹ️  No staged changes found.")
            print("Stage changes with: git add <files>")
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
        
        # Handle auto-commit
        if args.auto_commit:
            if args.json:
                print("❌ Error: --auto-commit cannot be used with --json", file=sys.stderr)
                sys.exit(1)
            
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
    
    # Default mode: scan all branches
    if not args.json:
        print("🔍 Scanning repository for unpushed changes...")
    
    branches = get_all_branches_with_status()
    
    if not branches:
        print("ℹ️  No branches with unpushed commits found.")
        sys.exit(0)
    
    # Interactive selection
    if not args.json:
        selected_branch = interactive_select_branch(branches)
        if not selected_branch:
            print("\n👋 Exiting.")
            sys.exit(0)
        
        # Process selected branch
        branch_tuple = next(b for b in branches if b[0] == selected_branch)
        branch_name, upstream, ahead, behind = branch_tuple
        
        print(f"\n📝 Analyzing branch: {branch_name}...")
        
        branch_info = get_branch_diff(branch_name, upstream, config)
        if not branch_info:
            print(f"❌ Error: Could not get diff for branch '{branch_name}'.")
            sys.exit(1)
        
        commit_message = generate_commit_message(branch_info, config)
        if not commit_message:
            sys.exit(1)
        
        print_branch_info(branch_info, commit_message)
        print(f"\n💡 Tip: Use --branch {branch_name} to process this branch directly")
    else:
        # JSON mode: process all branches
        results = []
        for branch_name, upstream, ahead, behind in branches:
            branch_info = get_branch_diff(branch_name, upstream, config)
            if branch_info:
                commit_message = generate_commit_message(branch_info, config)
                if commit_message:
                    results.append({
                        "branch": branch_info.name,
                        "upstream": branch_info.upstream,
                        "ahead": branch_info.ahead,
                        "behind": branch_info.behind,
                        "suggested_message": commit_message,
                        "files_changed": branch_info.files_changed,
                        "insertions": branch_info.insertions,
                        "deletions": branch_info.deletions
                    })
        
        print(json.dumps({"branches": results}, indent=2))


if __name__ == "__main__":
    main()
