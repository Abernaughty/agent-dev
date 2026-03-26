# Agent Dev — Stateful AI Workforce

A LangGraph-orchestrated AI development team with a SvelteKit dashboard for real-time monitoring. Three specialized agents (Architect, Lead Dev, QA) collaborate through structured JSON blueprints, execute code in E2B sandboxes, and persist knowledge via Chroma tiered memory.

## Project Structure

```
agent-dev/
├── dev-suite/                  # Python orchestrator (LangGraph)
│   ├── src/
│   │   ├── agents/             # Architect, Lead Dev, QA agent definitions
│   │   ├── api/                # FastAPI backend (REST + SSE)
│   │   ├── memory/             # Chroma vector store with tiered metadata
│   │   ├── sandbox/            # E2B sandbox runner
│   │   ├── tools/              # MCP bridge and tool providers
│   │   ├── orchestrator.py     # LangGraph state machine
│   │   ├── cli.py              # CLI interface
│   │   └── tracing.py          # Langfuse observability
│   ├── tests/                  # Comprehensive test suite
│   ├── pyproject.toml          # uv/PEP 735 dependencies
│   └── mcp-config.json         # MCP server version pins
├── dashboard/                  # SvelteKit frontend
│   ├── src/
│   │   ├── lib/                # Stores, SSE client, context
│   │   └── routes/             # Layout + page components
│   ├── package.json            # pnpm dependencies
│   └── svelte.config.js
├── .github/                    # Issue templates, labels, project config
├── CLAUDE.md                   # Claude Code context
└── CONTRIBUTING.md             # Contribution guidelines
```

## Architecture

**Orchestrator**: LangGraph state machine with explicit transitions. Three agents collaborate in a plan → build → test loop with structured JSON blueprints, max 3 retries per task, and human escalation on budget exhaustion.

**Agent Team**:
| Role | Model | Responsibility |
|---|---|---|
| Architect | Gemini 2.5 Flash | Creates structured blueprints. Never writes code. |
| Lead Dev | Claude Sonnet 4 | Executes blueprints. Writes and refactors code. |
| QA Agent | Claude Sonnet 4 | Runs tests, audits security, writes failure reports. |

**Memory**: Chroma with tiered metadata (L0-Core human-only, L0-Discovered agent-writable with 48h expiry, L1 module context, L2 ephemeral).

**Execution**: E2B sandboxed micro-VMs with structured JSON output wrappers. Role-specific sandbox profiles (locked-down for Dev/QA, permissive for research).

**Dashboard**: SvelteKit (Svelte 5 + TailwindCSS v4) with SSE real-time streaming. VS Code-inspired layout with activity bar, sidebar panels, and bottom terminal.

## Quick Start

### Orchestrator (Python)

```bash
cd dev-suite
uv sync                          # Install dependencies
cp .env.example .env             # Configure API keys
uv run python -m src             # Run orchestrator
uv run pytest tests/ -v          # Run tests
```

### Dashboard API

```bash
cd dev-suite
uv sync --group api
uv run --group api uvicorn src.api.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

### Dashboard Frontend

```bash
cd dashboard
pnpm install
cp .env.example .env             # Set BACKEND_URL
pnpm dev                         # http://localhost:5173
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check (no auth) |
| `GET` | `/agents` | Agent status list |
| `GET` | `/tasks` | Task list with timelines |
| `GET` | `/tasks/{id}` | Task detail with blueprint |
| `POST` | `/tasks` | Create new task |
| `POST` | `/tasks/{id}/cancel` | Cancel running task |
| `POST` | `/tasks/{id}/retry` | Retry failed task |
| `GET` | `/memory` | Memory entries (filterable) |
| `PATCH` | `/memory/{id}` | Approve/reject memory |
| `GET` | `/prs` | Pull request list |
| `GET` | `/events` | SSE stream |

## Environment Variables

Set in `dev-suite/.env`:
- `ANTHROPIC_API_KEY` — Claude API access
- `GOOGLE_API_KEY` — Gemini API access
- `E2B_API_KEY` — Sandbox execution
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` — Observability (optional)
- `API_SECRET` — Dashboard API auth token

Set in `dashboard/.env`:
- `PUBLIC_USE_MOCK_DATA` — `true` for mock mode, `false` for live API
- `BACKEND_URL` — API base URL (e.g. `http://localhost:8000`)
- `API_SECRET` — Must match the API's secret

## Roadmap

- ✅ LangGraph orchestrator with 3-agent team
- ✅ E2B sandbox execution
- ✅ Chroma tiered memory
- ✅ MCP tool bridge (Filesystem + GitHub)
- ✅ FastAPI dashboard backend (REST + SSE)
- ✅ SvelteKit dashboard with full data integration
- 🚧 Live SSE wiring (dashboard ↔ running orchestrator)
- 🚧 Memory approval UI
- 🚧 Blueprint editor
- 📋 CI/CD Pipeline MCP
- 📋 Langfuse integration
- 📋 Cost alerting thresholds
