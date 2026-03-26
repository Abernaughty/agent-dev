"""Memory store factory — creates the right backend from config.

Reads MEMORY_BACKEND env var (or accepts explicit argument) and returns
a MemoryStore-compatible instance.

Usage:
    from src.memory.factory import create_memory_store

    store = create_memory_store()  # reads MEMORY_BACKEND env var
    store = create_memory_store("chroma-ephemeral")  # explicit
"""

from __future__ import annotations

import os

from .chroma_store import ChromaMemoryStore
from .protocol import MemoryStore


def create_memory_store(
    backend: str | None = None,
    **kwargs,
) -> MemoryStore:
    """Create a memory store from config.

    Args:
        backend: Override for MEMORY_BACKEND env var.
            One of: "chroma-ephemeral", "chroma-local", "chroma-server".
            Defaults to MEMORY_BACKEND env var, then "chroma-local".
        **kwargs: Passed to ChromaMemoryStore.from_config().
            - persist_dir: str (chroma-local, default ./chroma_data)
            - host: str (chroma-server, default localhost)
            - port: int (chroma-server, default 8000)
            - collection_name: str (all backends, default dev_suite_memory)

    Returns:
        A MemoryStore-compatible instance.
    """
    if backend is None:
        backend = os.getenv("MEMORY_BACKEND", "chroma-local")

    # Apply env var defaults for backend-specific config
    if "persist_dir" not in kwargs:
        kwargs["persist_dir"] = os.getenv("CHROMA_PERSIST_DIR", "./chroma_data")
    if "host" not in kwargs:
        kwargs["host"] = os.getenv("CHROMA_HOST", "localhost")
    if "port" not in kwargs:
        port_str = os.getenv("CHROMA_PORT", "8000")
        try:
            kwargs["port"] = int(port_str)
        except (ValueError, TypeError):
            kwargs["port"] = 8000

    return ChromaMemoryStore.from_config(backend=backend, **kwargs)
