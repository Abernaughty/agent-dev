"""Seed L0-Core memory from l0_core.yaml.

Loads the YAML config and inserts each entry into the memory store
with source_type: "static-config" and mutable: false.

Usage:
    cd dev-suite
    uv run python -m src.memory.seed

    # Or with a specific backend:
    MEMORY_BACKEND=chroma-ephemeral uv run python -m src.memory.seed
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .chroma_store import ChromaMemoryStore
from .factory import create_memory_store
from .protocol import MemoryStore


YAML_PATH = Path(__file__).parent / "l0_core.yaml"


def _load_yaml(path: Path | None = None) -> dict:
    """Load and parse the L0-Core YAML config."""
    yaml_file = path or YAML_PATH
    if not yaml_file.exists():
        raise FileNotFoundError(f"L0-Core config not found: {yaml_file}")
    with open(yaml_file) as f:
        return yaml.safe_load(f)


def _flatten_stack_entries(config: dict) -> list[tuple[str, str]]:
    """Flatten the stack section into (content, module) pairs."""
    entries: list[tuple[str, str]] = []
    stack = config.get("stack", {})
    for category, items in stack.items():
        if isinstance(items, list):
            for item in items:
                entries.append((str(item), "stack"))
        elif isinstance(items, str):
            entries.append((str(items), "stack"))
    return entries


def _flatten_rule_entries(config: dict) -> list[tuple[str, str]]:
    """Flatten the rules section into (content, module) pairs."""
    entries: list[tuple[str, str]] = []
    rules = config.get("rules", {})
    for category, items in rules.items():
        if isinstance(items, list):
            for item in items:
                entries.append((str(item), f"rules-{category}"))
        elif isinstance(items, str):
            entries.append((str(items), f"rules-{category}"))
    return entries


def seed_l0_core(
    store: MemoryStore | None = None,
    yaml_path: Path | None = None,
    force: bool = False,
) -> int:
    """Populate L0-Core with entries from the YAML config.

    Args:
        store: Memory store to seed. If None, creates one from config.
        yaml_path: Override path to YAML file.
        force: If True, skip the "already seeded" check.

    Returns:
        Number of entries added.
    """
    if store is None:
        store = create_memory_store()

    # Check if already seeded (unless forced)
    if not force:
        stats = store.stats()
        existing = stats["by_tier"].get("l0-core", 0)
        if existing > 0:
            print(f"L0-Core already has {existing} entries. Skipping seed.")
            print("To re-seed, delete chroma_data/ and run again (or use force=True).")
            return 0

    config = _load_yaml(yaml_path)
    print("Seeding L0-Core memory from l0_core.yaml...")

    # Project info
    project = config.get("project", {})
    project_entries = []
    if project.get("name"):
        project_entries.append((f"Project name: {project['name']}", "project"))
    if project.get("description"):
        project_entries.append(
            (f"Project description: {project['description']}", "project")
        )

    stack_entries = _flatten_stack_entries(config)
    rule_entries = _flatten_rule_entries(config)

    all_entries = project_entries + stack_entries + rule_entries
    count = 0
    for content, module in all_entries:
        store.add_l0_core(content, module=module, source_type="static-config")
        label = content[:60] + "..." if len(content) > 60 else content
        print(f"  Added: {label}")
        count += 1

    stats = store.stats()
    print(f"\nDone. L0-Core now has {stats['by_tier'].get('l0-core', 0)} entries.")
    print(f"Total memory entries: {stats['total']}")
    return count


if __name__ == "__main__":
    seed_l0_core()
