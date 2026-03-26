# Contributing to Agent Dev

## Project Structure

```
agent-dev/
├── dev-suite/           # Stateful AI Workforce orchestrator
│   ├── src/
│   │   ├── orchestrator.py   # LangGraph state machine
│   │   ├── cli.py            # CLI runner (dev-suite run)
│   │   ├── agents/           # Architect, Developer, QA agents
│   │   ├── memory/           # Chroma tiered memory (L0/L1/L2)
│   │   ├── sandbox/          # E2B execution with output wrapper
│   │   └── tools/            # MCP integrations
│   ├── tests/
│   ├── pyproject.toml
│   └── .env.example
├── containers/          # MCP server containers
├── tools/               # Standalone dev tools
└── docs/                # Documentation
```

## Development Setup

```bash
cd dev-suite
uv sync --extra dev
cp .env.example .env
# Fill in your API keys
```

## Running Tests

```bash
# All unit tests
uv run pytest tests/ -v -m "not integration"

# Integration tests (requires API keys in .env)
uv run pytest tests/ -v -m integration

# Full suite
uv run pytest tests/ -v
```

## Branch Strategy

- `main` — stable, all tests passing
- `feat/*` — new features (branch from main)
- `fix/*` — bug fixes
- `chore/*` — maintenance, refactoring, docs

## Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Langfuse tracing to orchestrator
fix: E2B SDK uses Sandbox.create() factory method
test: add integration tests for sandbox runner
docs: update README with Phase 1 progress
chore: register integration pytest marker
```

## PR Review Workflow (Codex)

WARNING: Codex Reviews are currently on hold due to subscription limits. Perform your own in-depth PR reviews and merge after pushing any necessary fixes.

PRs are auto-reviewed by Codex (ChatGPT Codex Connector).

### Trigger rules
- **Creating a PR** auto-triggers the first Codex review — do NOT comment `@codex review` on new PRs. 
- **After pushing fix commits**, comment `@codex review` to trigger a re-review.

### Responding to Codex findings
- Reply to Codex inline comments using GitHub's PR review API (COMMENT event, matching file/line).
- Do NOT use top-level issue comments for replies — Codex won't see them as threaded responses.

### ⚠️ Known limitation: reading Codex sub-comments
The GitHub MCP `get_pull_request_comments` tool does **not** return Codex's sub-comments (they're nested under review objects, not top-level PR comments). To fetch them, use the REST API:

```bash
# 1. Get review IDs
curl -s "https://api.github.com/repos/OWNER/REPO/pulls/NUM/reviews" \
  -H "Accept: application/vnd.github+json"

# 2. Get sub-comments for a specific review
curl -s "https://api.github.com/repos/OWNER/REPO/pulls/NUM/reviews/REVIEW_ID/comments" \
  -H "Accept: application/vnd.github+json"
```

**Never assume "no comments" from the MCP tool means a clean review.** Always verify with the REST endpoint for each Codex review.

## Issue Labels

### Type
- `type/feature` — new capability
- `type/bug` — something broken
- `type/task` — implementation work
- `type/docs` — documentation
- `type/infra` — CI/CD, tooling, config

### Priority
- `priority/P0` — blocking, do now
- `priority/P1` — important, do this sprint
- `priority/P2` — nice to have

### Component
- `component/orchestrator`
- `component/memory`
- `component/sandbox`
- `component/mcp`
- `component/dashboard`

### Phase
- `phase/1-foundation`
- `phase/2-integration`
- `phase/3-hardening`

## Architecture Decisions

See [Pro Development Stack v2.1](docs/) for the full architecture document.
Key decisions are tracked as GitHub Issues with the `decision` label.
