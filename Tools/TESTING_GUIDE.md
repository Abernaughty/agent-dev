# Git Commit Agent - Comprehensive Testing Guide

This guide provides detailed instructions for testing all features of the redesigned Git Commit Agent.

## Prerequisites

1. **Python 3.8+** installed
2. **Anthropic API key** set: `export ANTHROPIC_API_KEY='your-key'`
3. **Git repository** with remote configured
4. **GitHub CLI (optional)**: `gh` for PR creation testing

## Test Environment Setup

```bash
# Create a test repository
mkdir test-git-agent
cd test-git-agent
git init
git remote add origin git@github.com:yourusername/test-repo.git

# Create initial commit
echo "# Test Repo" > README.md
git add README.md
git commit -m "Initial commit"
git push -u origin main
```

---

## Phase 1: Core Refactoring Tests

### Test 1.1: Default Behavior (Staged Changes)

**Objective**: Verify staged changes are now the default mode

```bash
# Make changes
echo "New feature" > feature.txt
git add feature.txt

# Run without flags (should analyze staged changes)
python git_commit_agent.py
```

**Expected**:
- ✅ Shows "📝 Analyzing staged changes..."
- ✅ Generates commit message
- ✅ Prompts: "Commit with this message? [y/N]:"
- ✅ Commits when 'y' is entered

**Pass Criteria**: Tool analyzes staged changes and prompts for commit

---

### Test 1.2: Deprecation Warnings

**Objective**: Verify deprecation warnings are shown

```bash
# Test --staged flag
python git_commit_agent.py --staged

# Test --auto-commit flag
python git_commit_agent.py --auto-commit
```

**Expected**:
- ⚠️ Shows warning: "--staged is now the default behavior"
- ⚠️ Shows warning: "--auto-commit is now default behavior"
- ✅ Still functions correctly

**Pass Criteria**: Warnings displayed, functionality preserved

---

### Test 1.3: No Staged Changes

**Objective**: Verify helpful message when no staged changes

```bash
# Modify file but don't stage
echo "Unstaged change" >> feature.txt

# Run tool
python git_commit_agent.py
```

**Expected**:
- ℹ️ Shows "No staged changes found"
- ℹ️ Lists modified files (not staged)
- ℹ️ Shows helpful commands: `git add <files>`
- ℹ️ Suggests PR creation option

**Pass Criteria**: Clear guidance provided for next steps

---

### Test 1.4: JSON Output

**Objective**: Verify JSON mode works

```bash
# Stage changes
git add feature.txt

# Run with --json
python git_commit_agent.py --json
```

**Expected**:
- ✅ Outputs valid JSON
- ✅ Contains: branch, staged, suggested_message, files_changed, insertions, deletions
- ✅ No interactive prompts

**Pass Criteria**: Valid JSON output, no prompts

---

## Phase 2: Git State Validation Tests

### Test 2.1: Merge in Progress

**Objective**: Verify tool blocks during merge

```bash
# Create merge conflict
git checkout -b test-branch
echo "Conflict" > feature.txt
git add feature.txt
git commit -m "Create conflict"
git checkout main
echo "Different" > feature.txt
git add feature.txt
git commit -m "Different change"
git merge test-branch  # This will conflict

# Try to use tool
python git_commit_agent.py
```

**Expected**:
- ❌ Shows "Repository is in the middle of a merge"
- ❌ Shows resolution commands
- ❌ Exits with error

**Pass Criteria**: Tool blocks operation, shows helpful guidance

**Cleanup**: `git merge --abort`

---

### Test 2.2: Rebase in Progress

**Objective**: Verify tool blocks during rebase

```bash
# Start rebase
git rebase main test-branch  # If conflicts occur

# Try to use tool
python git_commit_agent.py
```

**Expected**:
- ❌ Shows "Repository is in the middle of a rebase"
- ❌ Shows resolution commands
- ❌ Exits with error

**Pass Criteria**: Tool blocks operation

**Cleanup**: `git rebase --abort`

---

### Test 2.3: Detached HEAD

**Objective**: Verify warning for detached HEAD

```bash
# Checkout specific commit
git checkout HEAD~1

# Make changes
echo "Detached change" > test.txt
git add test.txt

# Run tool
python git_commit_agent.py
```

**Expected**:
- ⚠️ Shows "You are in 'detached HEAD' state"
- ⚠️ Warns about potential commit loss
- ⚠️ Prompts for confirmation
- ✅ Allows proceeding if confirmed

**Pass Criteria**: Warning shown, user can choose to proceed

**Cleanup**: `git checkout main`

---

### Test 2.4: Empty Repository

**Objective**: Verify first commit detection

```bash
# Create new empty repo
mkdir empty-repo
cd empty-repo
git init

# Create first file
echo "First" > README.md
git add README.md

# Run tool
python git_commit_agent.py
```

**Expected**:
- ℹ️ Shows "This appears to be your first commit"
- ✅ Generates commit message
- ✅ Allows committing

**Pass Criteria**: First commit detected, tool works normally

---

## Phase 3: PR Workflow Tests

### Test 3.1: Basic PR Creation

**Objective**: Verify PR workflow with clean state

```bash
# Create feature branch
git checkout -b feature/test-pr
echo "PR feature" > pr-feature.txt
git add pr-feature.txt
git commit -m "Add PR feature"
git push -u origin feature/test-pr

# Run PR mode
python git_commit_agent.py --pr
```

**Expected**:
- ✅ Detects unpushed commits (or offers to push)
- ✅ Generates PR description
- ✅ Shows PR preview
- ✅ Prompts: "Create PR with this description? [y/N]:"
- ✅ Creates PR via gh CLI or opens browser

**Pass Criteria**: PR created successfully

---

### Test 3.2: PR with Unpushed Commits

**Objective**: Verify push prompt

```bash
# Create branch with unpushed commits
git checkout -b feature/unpushed
echo "Unpushed" > unpushed.txt
git add unpushed.txt
git commit -m "Unpushed commit"
# Don't push

# Run PR mode
python git_commit_agent.py --pr
```

**Expected**:
- ⚠️ Shows "Branch has X unpushed commit(s)"
- ⚠️ Prompts: "Push commits now? [y/N]:"
- ✅ Pushes if confirmed
- ✅ Continues to PR creation

**Pass Criteria**: Offers to push, handles response correctly

---

### Test 3.3: PR with Branch Behind

**Objective**: Verify pull prompt when behind

```bash
# Simulate branch behind (push to main from another location)
# Then create feature branch from old main

git checkout -b feature/behind
# (Assume main has moved forward)

# Run PR mode
python git_commit_agent.py --pr
```

**Expected**:
- ⚠️ Shows "Branch is X commits behind"
- ⚠️ Prompts: "Pull latest changes? [y/N]:"
- ✅ Pulls if confirmed
- ✅ Continues to PR creation

**Pass Criteria**: Detects behind state, offers to pull

---

### Test 3.4: PR with Diverged Branch

**Objective**: Verify divergence detection

```bash
# Create diverged branch
git checkout -b feature/diverged
echo "Local" > diverged.txt
git add diverged.txt
git commit -m "Local commit"

# Simulate remote divergence (push different commit from another location)
# Then try PR

python git_commit_agent.py --pr
```

**Expected**:
- ⚠️ Shows "Branch has diverged: X ahead, Y behind"
- ❌ Shows "Please rebase or merge before creating PR"
- ❌ Exits with error

**Pass Criteria**: Detects divergence, blocks PR creation

---

### Test 3.5: PR with Unstaged Changes

**Objective**: Verify unstaged changes warning

```bash
# Create branch with unstaged changes
git checkout -b feature/unstaged
echo "Committed" > committed.txt
git add committed.txt
git commit -m "Committed change"
git push -u origin feature/unstaged

# Add unstaged change
echo "Unstaged" > unstaged.txt

# Run PR mode
python git_commit_agent.py --pr
```

**Expected**:
- ⚠️ Shows "Warning: X unstaged file(s)"
- ⚠️ Lists unstaged files
- ⚠️ Prompts: "Continue anyway? [y/N]:"
- ✅ Continues if confirmed

**Pass Criteria**: Warns about unstaged files, allows proceeding

---

### Test 3.6: PR with No Commits

**Objective**: Verify no-op when no commits

```bash
# Create branch with no new commits
git checkout -b feature/empty
git push -u origin feature/empty

# Run PR mode
python git_commit_agent.py --pr
```

**Expected**:
- ℹ️ Shows "No unpushed commits found"
- ℹ️ Shows "Nothing to create PR for"
- ✅ Exits gracefully

**Pass Criteria**: Detects no changes, exits cleanly

---

### Test 3.7: PR JSON Output

**Objective**: Verify PR JSON mode

```bash
# Create branch with commits
git checkout -b feature/json-pr
echo "JSON test" > json.txt
git add json.txt
git commit -m "JSON test"
git push -u origin feature/json-pr

# Run with --json
python git_commit_agent.py --pr --json
```

**Expected**:
- ✅ Outputs valid JSON
- ✅ Contains: branch, base, title, description, files_changed, insertions, deletions
- ✅ No interactive prompts
- ✅ No PR created (just outputs data)

**Pass Criteria**: Valid JSON output, no PR created

---

### Test 3.8: PR with GitHub CLI

**Objective**: Verify gh CLI integration

```bash
# Ensure gh CLI is installed
gh --version

# Create branch
git checkout -b feature/gh-cli
echo "GH CLI test" > gh-test.txt
git add gh-test.txt
git commit -m "GH CLI test"
git push -u origin feature/gh-cli

# Run PR mode
python git_commit_agent.py --pr
# Confirm PR creation
```

**Expected**:
- ✅ Detects gh CLI
- ✅ Creates PR via gh CLI
- ✅ Shows PR URL
- ✅ PR appears on GitHub

**Pass Criteria**: PR created via gh CLI successfully

---

### Test 3.9: PR Browser Fallback

**Objective**: Verify browser fallback when gh CLI unavailable

```bash
# Temporarily rename gh CLI (or test on system without it)
# Create branch
git checkout -b feature/browser
echo "Browser test" > browser.txt
git add browser.txt
git commit -m "Browser test"
git push -u origin feature/browser

# Run PR mode
python git_commit_agent.py --pr
# Confirm PR creation
```

**Expected**:
- ℹ️ Shows "GitHub CLI not found" (if applicable)
- ℹ️ Shows "Opening PR creation page in browser..."
- ✅ Opens browser to GitHub PR page
- ✅ PR form pre-filled with branch info

**Pass Criteria**: Browser opens to correct PR creation page

---

## Edge Cases & Error Handling

### Test E.1: Very Large Diff

**Objective**: Verify diff truncation

```bash
# Create large file
python -c "print('x' * 100000)" > large.txt
git add large.txt

# Run tool
python git_commit_agent.py
```

**Expected**:
- ✅ Truncates diff at max_diff_chars
- ✅ Shows truncation message
- ✅ Still generates commit message

**Pass Criteria**: Handles large diffs gracefully

---

### Test E.2: Binary Files

**Objective**: Verify binary file handling

```bash
# Add binary file
cp /path/to/image.png .
git add image.png

# Run tool
python git_commit_agent.py
```

**Expected**:
- ✅ Handles binary files in diff
- ✅ Generates appropriate commit message
- ✅ No errors

**Pass Criteria**: Binary files handled correctly

---

### Test E.3: No API Key

**Objective**: Verify error when API key missing

```bash
# Unset API key
unset ANTHROPIC_API_KEY

# Run tool
python git_commit_agent.py
```

**Expected**:
- ❌ Shows "ANTHROPIC_API_KEY environment variable not set"
- ❌ Shows how to set it
- ❌ Exits with error

**Pass Criteria**: Clear error message, helpful guidance

---

### Test E.4: API Rate Limiting

**Objective**: Verify retry logic

```bash
# Make many rapid requests to trigger rate limit
for i in {1..10}; do
  echo "Change $i" >> test.txt
  git add test.txt
  python git_commit_agent.py --json
done
```

**Expected**:
- ⚠️ Shows "Rate limited. Waiting Xs before retry..."
- ✅ Retries with exponential backoff
- ✅ Eventually succeeds or shows final error

**Pass Criteria**: Handles rate limiting gracefully

---

### Test E.5: Network Failure

**Objective**: Verify error handling for network issues

```bash
# Disconnect network or block API endpoint
# Run tool
python git_commit_agent.py
```

**Expected**:
- ❌ Shows appropriate error message
- ❌ Exits gracefully
- ✅ No crashes or hangs

**Pass Criteria**: Handles network errors gracefully

---

## Integration Tests

### Test I.1: Complete Workflow

**Objective**: Test full commit → PR workflow

```bash
# 1. Create feature branch
git checkout -b feature/complete-workflow

# 2. Make changes and commit with tool
echo "Feature 1" > feature1.txt
git add feature1.txt
python git_commit_agent.py
# Confirm commit

# 3. Make more changes and commit
echo "Feature 2" > feature2.txt
git add feature2.txt
python git_commit_agent.py
# Confirm commit

# 4. Create PR
python git_commit_agent.py --pr
# Confirm PR creation
```

**Expected**:
- ✅ Both commits created successfully
- ✅ PR created with both commits
- ✅ PR description includes both changes

**Pass Criteria**: Complete workflow works end-to-end

---

### Test I.2: Multiple Branches

**Objective**: Test switching between branches

```bash
# Create multiple feature branches
git checkout -b feature/branch1
echo "Branch 1" > b1.txt
git add b1.txt
python git_commit_agent.py

git checkout -b feature/branch2
echo "Branch 2" > b2.txt
git add b2.txt
python git_commit_agent.py

# Create PRs for both
git checkout feature/branch1
python git_commit_agent.py --pr

git checkout feature/branch2
python git_commit_agent.py --pr
```

**Expected**:
- ✅ Each branch handled independently
- ✅ Correct commits on each branch
- ✅ Separate PRs created

**Pass Criteria**: Multiple branches work correctly

---

## Performance Tests

### Test P.1: Large Repository

**Objective**: Test performance with large repo

```bash
# Clone large repository
git clone https://github.com/torvalds/linux.git
cd linux

# Make small change
echo "// Test" >> README
git add README

# Run tool
time python git_commit_agent.py
```

**Expected**:
- ✅ Completes in reasonable time (<30s)
- ✅ Handles large git history
- ✅ No memory issues

**Pass Criteria**: Performs acceptably on large repos

---

## Test Summary Checklist

Use this checklist to track testing progress:

### Phase 1: Core Refactoring
- [ ] Test 1.1: Default Behavior
- [ ] Test 1.2: Deprecation Warnings
- [ ] Test 1.3: No Staged Changes
- [ ] Test 1.4: JSON Output

### Phase 2: Git State Validation
- [ ] Test 2.1: Merge in Progress
- [ ] Test 2.2: Rebase in Progress
- [ ] Test 2.3: Detached HEAD
- [ ] Test 2.4: Empty Repository

### Phase 3: PR Workflow
- [ ] Test 3.1: Basic PR Creation
- [ ] Test 3.2: PR with Unpushed Commits
- [ ] Test 3.3: PR with Branch Behind
- [ ] Test 3.4: PR with Diverged Branch
- [ ] Test 3.5: PR with Unstaged Changes
- [ ] Test 3.6: PR with No Commits
- [ ] Test 3.7: PR JSON Output
- [ ] Test 3.8: PR with GitHub CLI
- [ ] Test 3.9: PR Browser Fallback

### Edge Cases
- [ ] Test E.1: Very Large Diff
- [ ] Test E.2: Binary Files
- [ ] Test E.3: No API Key
- [ ] Test E.4: API Rate Limiting
- [ ] Test E.5: Network Failure

### Integration Tests
- [ ] Test I.1: Complete Workflow
- [ ] Test I.2: Multiple Branches

### Performance Tests
- [ ] Test P.1: Large Repository

---

## Reporting Issues

When reporting issues, include:

1. **Test number** (e.g., Test 3.2)
2. **Command run**: Exact command used
3. **Expected behavior**: What should happen
4. **Actual behavior**: What actually happened
5. **Error messages**: Full error output
6. **Environment**:
   - OS and version
   - Python version
   - Git version
   - Tool version/commit

---

## Automated Testing

For automated testing, use the test script:

```bash
# Run all tests
python test_git_commit_agent.py -v

# Run specific test
python test_git_commit_agent.py TestClassName.test_method_name -v
```

---

## Success Criteria

The implementation is considered successful when:

✅ All Phase 1 tests pass
✅ All Phase 2 tests pass  
✅ All Phase 3 tests pass
✅ At least 90% of edge case tests pass
✅ Integration tests complete successfully
✅ Performance is acceptable (<30s for large repos)
✅ No critical bugs or crashes
