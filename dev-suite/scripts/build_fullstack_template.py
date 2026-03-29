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
  - pnpm latest (via corepack)
  - ruff latest (Python linter)
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

    The code-interpreter-v1 base includes:
      - Python 3.x + Jupyter + pip
      - Node.js 20 LTS (via NodeSource apt repo)
      - curl, jq, gnupg, ca-certificates

    We upgrade Node.js to 24 LTS, add pnpm + ruff, and verify.
    All apt/system operations run as root.
    """
    template = (
        Template()
        # Base: E2B code-interpreter (Python 3.x + Jupyter + Node.js 20)
        .from_template("code-interpreter-v1")
        # Upgrade Node.js 20 -> 24 LTS via NodeSource (must run as root)
        .run_cmd(
            "curl -fsSL https://deb.nodesource.com/setup_24.x | bash -",
            user="root",
        )
        .run_cmd(
            "apt-get install -y nodejs",
            user="root",
        )
        # pnpm via corepack (needs root for symlink into /usr/bin)
        .run_cmd([
            "corepack enable",
            "corepack prepare pnpm@latest --activate",
        ], user="root")
        # Python linting tools
        .pip_install(["ruff"])
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
    print("Target: Node.js 24 LTS + pnpm + ruff")
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
    print("Then test with:")
    print('  uv run python -c "from e2b_code_interpreter import Sandbox; '
          f"sbx = Sandbox.create(template='{build_info.name}'); "
          "print(sbx.commands.run('node --version').stdout); "
          "print(sbx.commands.run('pnpm --version').stdout); sbx.kill()\"")
    print()


if __name__ == "__main__":
    build()
