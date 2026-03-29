# E2B Sandbox Templates

Custom E2B sandbox templates for the agent workforce.

## Templates

### `fullstack-dev`

Full-stack development sandbox with Python + Node.js toolchains.

**Includes:**
- Python 3.x + pip + ruff (from base `e2bdev/code-interpreter`)
- Node.js 22 LTS + pnpm (via corepack)
- SvelteKit dashboard skeleton pre-installed (svelte-check, tsc, vite, tailwindcss)
- jq for JSON processing

**Used by:** Lead Dev and QA agents for tasks targeting `.svelte`, `.ts`, `.js`, `.css` files.

**Zero secrets baked in.** Toolchains and dependencies only. Secrets are injected at sandbox runtime via environment variables.

## Building Templates

### Prerequisites

1. Install E2B CLI: `npm install -g @e2b/cli`
2. Authenticate: `e2b auth login`
3. Have E2B API key configured

### Build the fullstack-dev template

The build context needs access to the dashboard and dev-suite directories. Run from the **repo root**:

```bash
cd /path/to/agent-dev

e2b template build \
  --name fullstack-dev \
  --dockerfile dev-suite/sandbox-templates/fullstack-dev/e2b.Dockerfile \
  --path .
```

This will output a template ID like `fullstack-dev-abc123`. Copy this ID.

### Configure the template ID

Add the template ID to your `dev-suite/.env`:

```bash
E2B_TEMPLATE_FULLSTACK=fullstack-dev-abc123
```

Also update `.env.example` so others know about it.

## Rebuild Triggers

Rebuild the template when any of these change:
- `dashboard/package.json` or `dashboard/pnpm-lock.yaml`
- `dev-suite/pyproject.toml`
- Node.js or Python major version bump
- Monthly review (alongside MCP version pinning schedule)

Currently a manual process. CI-triggered rebuilds deferred to Phase 3.

## Template Size

The fullstack-dev template is expected to be 500MB-1GB due to:
- Node.js runtime (~100MB)
- `node_modules` from dashboard deps (~300-500MB)
- Python deps from dev-suite (~100-200MB)

Check E2B plan limits if concerned about template storage.

## Planned Templates

| Template | Status | Description |
|---|---|---|
| `fullstack-dev` | **This PR** | Python + Node.js + SvelteKit |
| `python-dev` | Deferred | Formalized Python-only (current default works) |
| `research` | Phase 3 | Permissive profile, open egress, zero secrets |
