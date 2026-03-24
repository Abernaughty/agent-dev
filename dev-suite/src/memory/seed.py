"""Seed L0-Core memory with the reference stack.

Run once to populate the foundational project knowledge
that all agents query before generating Blueprints.

Usage:
    cd dev-suite
    uv run python -m src.memory.seed
"""

from .chroma_store import ChromaMemoryStore


def seed_l0_core(store: ChromaMemoryStore | None = None) -> None:
    """Populate L0-Core with the reference stack and project rules."""
    if store is None:
        store = ChromaMemoryStore()

    # Check if already seeded
    existing = store.query("reference stack", tiers=["l0-core"] if False else None, n_results=1)
    # Simple check: if we have L0-Core entries, skip
    stats = store.stats()
    if stats["by_tier"].get("l0-core", 0) > 0:
        print(f"L0-Core already has {stats['by_tier']['l0-core']} entries. Skipping seed.")
        print("To re-seed, delete chroma_data/ and run again.")
        return

    print("Seeding L0-Core memory...")

    # ── Reference Stack ──
    stack_entries = [
        ("Languages: Python, TypeScript, JavaScript", "stack"),
        ("Frontend framework: SvelteKit with Svelte 5, TailwindCSS, ESLint", "stack"),
        ("Databases: Redis, CosmosDB", "stack"),
        ("Data and tooling: Firecrawl for web scraping and crawling", "stack"),
        ("Infrastructure: Azure (Container Apps/AKS, Static Web Apps, Functions, App Service), Vercel, Cloudflare, Docker/Docker Compose, Terraform", "stack"),
        ("CI/CD: GitHub Actions, Azure DevOps Pipelines, Vercel (repo-connected)", "stack"),
        ("Source control: GitHub", "stack"),
        ("Package managers: npm/pnpm/bun for JS, pip/poetry/uv for Python", "stack"),
        ("Dev environment: VS Code with Dev Containers", "stack"),
    ]

    # ── Project Rules (customize these for your project) ──
    rule_entries = [
        ("All agent-generated code must run in E2B sandboxes, never on the host machine", "rules"),
        ("Agents communicate via structured JSON Blueprints, not natural language", "rules"),
        ("Maximum 3 retries per task, each retry includes the QA failure report", "rules"),
        ("L0-Core memory is human-only write access. Agents cannot modify project rules.", "rules"),
        ("Agent-discovered constraints (L0-Discovered) expire after 48 hours if not approved", "rules"),
        ("All secrets are injected via environment variables, never hardcoded or logged", "rules"),
        ("MCP server versions are pinned in mcp-config.json and reviewed monthly", "rules"),
    ]

    for content, module in stack_entries + rule_entries:
        entry_id = store.add_l0_core(content, module=module)
        print(f"  Added: {content[:60]}..." if len(content) > 60 else f"  Added: {content}")

    stats = store.stats()
    print(f"\nDone. L0-Core now has {stats['by_tier'].get('l0-core', 0)} entries.")
    print(f"Total memory entries: {stats['total']}")


if __name__ == "__main__":
    seed_l0_core()
