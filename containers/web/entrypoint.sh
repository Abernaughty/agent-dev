#!/bin/sh
set -euo pipefail

cd /workspace

# Scaffold once (into an empty temp dir), then copy into /workspace
if [ ! -f package.json ]; then
  echo "🧱 No project found. Scaffolding SvelteKit (minimal, TS)…"
  rm -rf /tmp/svapp && mkdir -p /tmp/svapp

  npx -y sv create /tmp/svapp \
    --template minimal \
    --types ts \
    --no-add-ons \
    --no-install

  # Safe copy for Windows/WSL mounts: don't preserve owner/perm/timestamps
  tar -C /tmp/svapp -cf - . | tar -C /workspace --no-same-owner --no-same-permissions -xf -
fi

# Install deps unless we've already done it in this volume
if [ ! -f node_modules/.installed ]; then
  echo "📦 Installing dependencies…"
  npm ci || npm install
  date > node_modules/.installed
fi

echo "🚀 Starting dev server on 0.0.0.0:3000"
exec npm run dev -- --host 0.0.0.0 --port 3000
