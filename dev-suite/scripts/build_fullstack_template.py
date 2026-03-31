"""Build the fullstack-dev E2B sandbox template.

Uses E2B Build System 2.0 (code-based, no CLI needed).
Requires E2B_API_KEY in your environment or .env file.

Usage:
    cd dev-suite
    uv run python scripts/build_fullstack_template.py

The script will:
1. Define the template using the E2B Python SDK
2. Build it on E2B's cloud infrastructure
3. Print the template ID/name to add to your .env

Estimated build time: 3-8 minutes.

Target versions (as of March 2026):
  - Node.js 24 LTS (v24.14.1 'Krypton') — supported through April 2028
  - pnpm 10.33+ (via corepack)
  - Python 3.x + pip + ruff (Python linter)

Why NOT code-interpreter-v1 as base:
  The code-interpreter template includes a Jupyter server + FastAPI proxy
  (port 49999) with an IJavaScript kernel compiled against Node.js 20.
  Upgrading Node 20->24 breaks the Jupyter startup chain — the IJavaScript
  kernel's native modules are ABI-incompatible with Node 24, so the
  FastAPI health check never passes and the build times out.

  We don't need Jupyter for this template. Our E2BRunner uses
  subprocess.run() via run_code() for test commands. The fullstack
  template just needs a shell with Node.js, pnpm, Python, and ruff.
  We use Sandbox.commands.run() from the base e2b SDK instead.
"""

import sys

from dotenv import load_dotenv

load_dotenv()

try:
    from e2b import Template, default_build_logger
except ImportError:
    print("ERROR: e2b SDK not found. Run: uv sync")
    sys.exit(1)


def define_template():
    """Define the fullstack-dev sandbox template.

    Built from a clean Ubuntu base with:
      - Node.js 24 LTS via NodeSource
      - pnpm via corepack
      - Python 3 + pip + ruff
      - Common tools (curl, jq, git, gnupg, ca-certificates)

    No Jupyter, no code-interpreter overhead. Clean and fast.
    """
    template = (
        Template()
        .from_ubuntu("24.04")
        # System packages
        .apt_install([
            "curl", "jq", "git", "gnupg", "ca-certificates",
            "python3", "python3-pip", "python3-venv",
        ])
        # Node.js 24 LTS via NodeSource
        .run_cmd(
            "curl -fsSL https://deb.nodesource.com/setup_24.x | bash -",
            user="root",
        )
        .apt_install(["nodejs"])
        # pnpm via corepack
        .run_cmd([
            "corepack enable",
            "corepack prepare pnpm@latest --activate",
        ], user="root")
        # Python linting tools
        .pip_install(["ruff", "pytest"])
        # Verify installations
        .run_cmd([
            "node --version",
            "pnpm --version",
            "python3 --version",
            "ruff --version",
            "echo 'fullstack-dev template ready'",
        ])
    )
    return template


def build():
    """Build the template and print the result."""
    print("=" * 60)
    print("Building fullstack-dev E2B sandbox template")
    print("=" * 60)
    print()
    print("Target: Ubuntu 24.04 + Node.js 24 LTS + pnpm + Python 3 + ruff")
    print("This will take 3-8 minutes. Build logs will stream below.")
    print()

    template = define_template()

    build_info = Template.build(
        template,
        "fullstack-dev",
        cpu_count=2,
        memory_mb=2048,
        on_build_logs=default_build_logger(),
    )

    print()
    print("=" * 60)
    print("BUILD COMPLETE")
    print("=" * 60)
    print()
    print(f"  Template name: {build_info.name}")
    print(f"  Template ID:   {build_info.template_id}")
    print(f"  Build ID:      {build_info.build_id}")
    print()
    print("Add to your dev-suite/.env:")
    print(f"  E2B_TEMPLATE_FULLSTACK={build_info.name}")
    print()
    print("Test with:")
    print('  uv run python -c "')
    print("from e2b import Sandbox")
    print(f"sbx = Sandbox.create(template='{build_info.name}')")
    print("print(sbx.commands.run('node --version').stdout)")
    print("print(sbx.commands.run('pnpm --version').stdout)")
    print("print(sbx.commands.run('python3 --version').stdout)")
    print('sbx.kill()"')
    print()


if __name__ == "__main__":
    build()
