# E2B Custom Sandbox Template: fullstack-dev
#
# Extends the code-interpreter base with Node.js 22 LTS + pnpm
# for validating SvelteKit/TypeScript/CSS alongside Python tasks.
#
# Build: e2b template build --name fullstack-dev
# Rebuild when: dashboard/package.json, pnpm-lock.yaml, or pyproject.toml changes.
#
# SECURITY: Zero secrets baked in. Toolchains and deps only.
# Secrets are injected at runtime via environment variables.

FROM e2bdev/code-interpreter:latest

# -- System packages --
# curl: for NodeSource setup
# jq: for JSON validation in agent tasks
# git: pre-installed in base, but ensure it's there
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        jq \
        ca-certificates \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# -- Node.js 22 LTS via NodeSource --
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# -- pnpm via corepack --
RUN corepack enable \
    && corepack prepare pnpm@latest --activate

# -- Python linting tools (available for QA agent) --
RUN pip install --no-cache-dir ruff

# -- Dashboard project skeleton --
# Copy the minimal SvelteKit project structure so pnpm check works.
# Agents will overwrite specific files; the skeleton provides config context.
WORKDIR /home/user/dashboard

# Copy package manifests first (Docker cache layer)
COPY dashboard/package.json dashboard/pnpm-lock.yaml ./

# Install dashboard dependencies (frozen lockfile for reproducibility)
RUN pnpm install --frozen-lockfile

# Copy SvelteKit config files (needed for svelte-check / tsc)
COPY dashboard/svelte.config.js dashboard/tsconfig.json dashboard/vite.config.ts ./

# Copy static assets and app entry
COPY dashboard/static/ ./static/
COPY dashboard/src/app.html ./src/app.html
COPY dashboard/src/app.css ./src/app.css

# SvelteKit sync generates .svelte-kit/tsconfig.json (needed by svelte-check)
RUN pnpm exec svelte-kit sync || true

# -- Dev-suite Python deps --
# Copy pyproject.toml and install Python dependencies
WORKDIR /home/user/dev-suite
COPY dev-suite/pyproject.toml ./

# Install Python deps (main group only, not dev/api groups)
RUN pip install --no-cache-dir . || pip install --no-cache-dir -e . || true

# -- Reset to home directory --
WORKDIR /home/user

# Verify installations
RUN node --version \
    && pnpm --version \
    && python3 --version \
    && ruff --version \
    && echo "fullstack-dev template ready"
