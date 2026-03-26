"""Tiered memory layer (Chroma -> pgvector migration path).

Public API:
    MemoryStore      - Protocol (interface) for all backends
    MemoryTier       - Enum: L0_CORE, L0_DISCOVERED, L1, L2
    MemoryEntry      - Pydantic model for memory entries
    MemoryQueryResult - Pydantic model for query results
    ChromaMemoryStore - Chroma-backed implementation
    create_memory_store - Factory: creates the right backend from config
    seed_l0_core     - Populate L0-Core from l0_core.yaml
"""

from .chroma_store import ChromaMemoryStore
from .factory import create_memory_store
from .protocol import MemoryEntry, MemoryQueryResult, MemoryStore, MemoryTier
from .seed import seed_l0_core

__all__ = [
    "ChromaMemoryStore",
    "MemoryEntry",
    "MemoryQueryResult",
    "MemoryStore",
    "MemoryTier",
    "create_memory_store",
    "seed_l0_core",
]
