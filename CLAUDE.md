# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Stateful AI Workforce — a LangGraph-orchestrated team of three AI agents (Architect, Lead Dev, QA) with a SvelteKit dashboard for real-time monitoring. Agents collaborate via structured JSON blueprints, execute code in E2B sandboxes, and persist knowledge through Chroma tiered memory.

## Commands

### Orchestrator (dev-suite)

```bash
cd dev-suite
uv sync                                    # Install all dependencies
uv run python -m src                       # Run orchestrator
uv run python -m src --task "description"  # Run with a task
uv run pytest tests/ -v                    # Run all tests
uv run pytest tests/test_api.py -v         # Run API tests only
uv run pytest tests/test_e2e.py -v         # Run E2E tests
```

### Dashboard API

```bash
cd dev-suite
uv sync --group api
uv run --group api uvicorn src.api.main:app --reload --port 8000
```

### Dashboard Frontend

```bash
cd dashboard
pnpm install
pnpm dev          # Start dev server at http://localhost:5173
pnpm build        # Production build
pnpm check        # Type checking
```

## Architecture

### Orchestrator (dev-suite/)

LangGraph state machine orchestrating three agents in a plan → build → test loop:

- **Architect** (Gemini 2.5 Flash) — Creates structured JSON blueprints. Never writes code.
- **Lead Dev** (Claude Sonnet 4) — Executes blueprints. Writes and refactors code in E2B sandboxes.
- **QA Agent** (Claude Sonnet 4) — Runs tests, audits security, writes structured failure reports.

Key modules:
- `src/orchestrator.py` — LangGraph state machine with retry logic (max 3 attempts + token budget)
- `src/agents/` — Agent definitions (architect.py, developer.py, qa.py)
- `src/memory/` — Chroma vector store with L0/L1/L2 tiered metadata
- `src/sandbox/` — E2B sandbox runner with structured JSON output wrappers
- `src/tools/` — MCP bridge (mcp_bridge.py), tool providers (provider.py, mcp_provider.py)
- `src/api/` — FastAPI backend with REST + SSE endpoints, Bearer auth, mock StateManager
- `src/cli.py` — CLI interface
- `src/tracing.py` — Langfuse observability integration

### Dashboard (dashboard/)

SvelteKit app (Svelte 5 + TailwindCSS v4 + pnpm) with VS Code-inspired layout:

- Uses Svelte context API (setDashboardContext/getDashboardContext in dashboard.svelte.ts)
- +layout.svelte = chrome shell, +page.svelte renders MainContent via context
- SSE client with window event dispatch for real-time updates
- Stores: agents, tasks, memory, prs, connection, dashboard
- Mock mode via PUBLIC_USE_MOCK_DATA=true

**CRITICAL**: SvelteKit PUBLIC_* env vars use `import { X } from '$env/static/public'` — NOT import.meta.env.
**CRITICAL**: Google Fonts @import MUST precede @import "tailwindcss" in app.css.

### Python Packaging

Uses `uv` with PEP 735 dependency-groups in pyproject.toml (not optional-dependencies). Python 3.13.

### MCP Configuration

MCP server versions pinned in `dev-suite/mcp-config.json`. Filesystem MCP via npx, GitHub MCP via Docker.

## File Structure

```
agent-dev/
├── dev-suite/                  # Python orchestrator
│   ├── src/
│   │   ├── agents/             # Architect, Lead Dev, QA
│   │   ├── api/                # FastAPI backend (main, auth, events, models, state)
│   │   ├── memory/             # Chroma store + seed data
│   │   ├── sandbox/            # E2B runner
│   │   ├── tools/              # MCP bridge + providers
│   │   ├── orchestrator.py
│   │   ├── cli.py
│   │   └── tracing.py
│   ├── tests/                  # 12 test files
│   ├── pyproject.toml
│   ├── mcp-config.json
│   └── .env.example
├── dashboard/                  # SvelteKit frontend
│   ├── src/
│   │   ├── lib/                # Stores, SSE client, context
│   │   └── routes/             # Layout + page
│   ├── package.json
│   ├── svelte.config.js
│   └── .env.example
├── .github/                    # Issue templates, labels
├── CONTRIBUTING.md
└── CLAUDE.md                   # This file
```

## Environment Variables

### dev-suite/.env
- `ANTHROPIC_API_KEY` — Claude API
- `GOOGLE_API_KEY` — Gemini API
- `E2B_API_KEY` — Sandbox execution
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` — Observability
- `API_SECRET` — Dashboard API auth
- `ARCHITECT_MODEL` / `DEVELOPER_MODEL` / `QA_MODEL` — Override agent models

### dashboard/.env
- `PUBLIC_USE_MOCK_DATA` — `true` for mock mode
- `BACKEND_URL` — API base URL
- `API_SECRET` — Must match API's secret

## GitHub Workflow

- **Repo**: Abernaughty/agent-dev
- **Project board**: github.com/users/Abernaughty/projects/3
- **PR workflow**: Create PR → Codex auto-reviews → fix if needed → merge
- **Labels**: 18 configured, 3 milestones (Phase 1/2/3)
- **PAT**: Fine-grained for Issues/PRs/Contents; classic for Projects v2 GraphQL
