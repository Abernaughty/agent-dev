# Contributing to Agent Dev

## Project Structure

```
agent-dev/
├── dev-suite/           # Stateful AI Workforce orchestrator
│   ├── src/
│   │   ├── orchestrator.py   # LangGraph state machine
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
