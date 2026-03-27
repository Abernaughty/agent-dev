# Agent Workforce Dashboard

SvelteKit dashboard for the Pro Development Stack agent workforce.
Provides visual oversight with a VS Code-style layout.

## Tech Stack

- SvelteKit 2.50+ with Svelte 5
- Tailwind CSS v4 (Vite plugin)
- TypeScript
- pnpm

## Getting Started

```bash
cd dashboard
pnpm install
pnpm dev
```

Open [http://localhost:5173](http://localhost:5173).

The dashboard connects to the FastAPI backend at `BACKEND_URL` (default `http://localhost:8000`).
If the backend isn't running, each panel degrades gracefully — agents show idle, tasks/memory/PRs show empty state, and the connection banner indicates disconnected status.

## Layout

VS Code-style shell with:
- **Activity Bar** (left) — switch between panels
- **Sidebar** — contextual list for active panel
- **Main Content** — panel-specific views
- **Bottom Panel** — resizable terminal output
- **Status Bar** — agent status, token usage, cost

## Related

- Issue: [#17](https://github.com/Abernaughty/agent-dev/issues/17)
- Mockup: `streamlit-v4-vertical-tabs.jsx` (project files)
- Spec: `DASHBOARD-SPEC.md`
