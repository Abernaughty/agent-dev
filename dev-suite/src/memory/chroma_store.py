"""Chroma-backed tiered memory store.

Implements L0-Core, L0-Discovered, L1, L2 memory tiers
using Chroma with metadata tagging.

Designed to be database-agnostic for future pgvector migration.
"""

import time
from enum import Enum
from pathlib import Path

import chromadb
from pydantic import BaseModel


# ── Tier Definitions ──

class MemoryTier(str, Enum):
    """Memory tier levels.

    L0_CORE: Project rules, stack, constraints. Human-only writes.
    L0_DISCOVERED: Agent-discovered constraints. 48h expiry, requires approval.
    L1: Module-level context, dependencies, decisions. Agent-writable.
    L2: Ephemeral task context, chat history. Auto-expires.
    """
    L0_CORE = "l0-core"
    L0_DISCOVERED = "l0-discovered"
    L1 = "l1"
    L2 = "l2"


class MemoryEntry(BaseModel):
    """A single memory entry with metadata."""
    content: str
    tier: MemoryTier
    module: str = "global"        # Which module/area this relates to
    verified: bool = True          # L0-Discovered starts as False
    source_agent: str = "human"   # Who wrote this entry
    created_at: float = 0.0
    expires_at: float | None = None  # None = never expires


# ── TTL Constants ──

L0_DISCOVERED_TTL = 48 * 60 * 60  # 48 hours in seconds
L2_TTL = 4 * 60 * 60              # 4 hours in seconds


# ── Memory Store ──

class ChromaMemoryStore:
    """Tiered memory store backed by Chroma.

    Usage:
        store = ChromaMemoryStore()
        store.seed_l0_core()  # One-time setup
        store.add_l1("auth module uses JWT", module="auth")
        context = store.query("how does auth work", module="auth")
    """

    def __init__(self, persist_dir: str = "./chroma_data", collection_name: str = "dev_suite_memory"):
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # ── Write Methods ──

    def add_l0_core(self, content: str, module: str = "global") -> str:
        """Add a core project rule. Human-only."""
        return self._add(content, MemoryTier.L0_CORE, module=module, source_agent="human", verified=True)

    def add_l0_discovered(self, content: str, module: str = "global", source_agent: str = "unknown") -> str:
        """Add an agent-discovered constraint. Expires in 48h if not approved."""
        return self._add(
            content, MemoryTier.L0_DISCOVERED,
            module=module, source_agent=source_agent,
            verified=False,
            ttl=L0_DISCOVERED_TTL,
        )

    def add_l1(self, content: str, module: str = "global", source_agent: str = "unknown") -> str:
        """Add module-level context."""
        return self._add(content, MemoryTier.L1, module=module, source_agent=source_agent, verified=True)

    def add_l2(self, content: str, module: str = "global", source_agent: str = "unknown") -> str:
        """Add ephemeral task context. Auto-expires."""
        return self._add(
            content, MemoryTier.L2,
            module=module, source_agent=source_agent,
            verified=True,
            ttl=L2_TTL,
        )

    def _add(self, content: str, tier: MemoryTier, module: str, source_agent: str,
             verified: bool, ttl: int | None = None) -> str:
        """Internal: add an entry to Chroma."""
        now = time.time()
        entry_id = f"{tier.value}_{module}_{int(now * 1000)}"
        expires_at = now + ttl if ttl else 0.0  # 0.0 = never

        self._collection.add(
            ids=[entry_id],
            documents=[content],
            metadatas=[{
                "tier": tier.value,
                "module": module,
                "verified": verified,
                "source_agent": source_agent,
                "created_at": now,
                "expires_at": expires_at,
            }],
        )
        return entry_id

    # ── Read Methods ──

    def query(self, query_text: str, module: str | None = None,
              tiers: list[MemoryTier] | None = None, n_results: int = 10) -> list[dict]:
        """Query memory with optional tier and module filters.

        Returns list of {content, tier, module, verified, source_agent, score}.
        Automatically excludes expired entries.
        """
        where_clauses = []
        if tiers:
            where_clauses.append({"tier": {"$in": [t.value for t in tiers]}})
        if module:
            where_clauses.append({"module": module})

        where = None
        if len(where_clauses) == 1:
            where = where_clauses[0]
        elif len(where_clauses) > 1:
            where = {"$and": where_clauses}

        try:
            results = self._collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            return []

        entries = []
        now = time.time()
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # Skip expired entries
            expires = meta.get("expires_at", 0.0)
            if expires > 0 and expires < now:
                continue

            entries.append({
                "content": doc,
                "tier": meta["tier"],
                "module": meta.get("module", "global"),
                "verified": meta.get("verified", True),
                "source_agent": meta.get("source_agent", "unknown"),
                "score": 1 - dist,  # Convert distance to similarity
            })

        return entries

    def get_pending_approvals(self) -> list[dict]:
        """Get L0-Discovered entries awaiting human approval."""
        results = self._collection.get(
            where={"$and": [
                {"tier": MemoryTier.L0_DISCOVERED.value},
                {"verified": False},
            ]},
            include=["documents", "metadatas"],
        )
        if not results["ids"]:
            return []

        now = time.time()
        pending = []
        for entry_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            expires = meta.get("expires_at", 0.0)
            if expires > 0 and expires < now:
                continue  # Already expired
            pending.append({
                "id": entry_id,
                "content": doc,
                "module": meta.get("module", "global"),
                "source_agent": meta.get("source_agent", "unknown"),
                "hours_remaining": max(0, (expires - now) / 3600) if expires > 0 else None,
            })
        return pending

    def approve_discovered(self, entry_id: str) -> bool:
        """Approve an L0-Discovered entry (promote to verified, remove expiry)."""
        try:
            self._collection.update(
                ids=[entry_id],
                metadatas=[{"verified": True, "expires_at": 0.0}],
            )
            return True
        except Exception:
            return False

    def reject_discovered(self, entry_id: str) -> bool:
        """Reject an L0-Discovered entry (delete it)."""
        try:
            self._collection.delete(ids=[entry_id])
            return True
        except Exception:
            return False

    # ── Cleanup ──

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        now = time.time()
        # Get all entries with expiry
        results = self._collection.get(
            where={"expires_at": {"$gt": 0.0}},
            include=["metadatas"],
        )
        if not results["ids"]:
            return 0

        expired_ids = [
            eid for eid, meta in zip(results["ids"], results["metadatas"])
            if meta.get("expires_at", 0.0) > 0 and meta["expires_at"] < now
        ]

        if expired_ids:
            self._collection.delete(ids=expired_ids)
        return len(expired_ids)

    # ── Stats ──

    def stats(self) -> dict:
        """Get memory store statistics by tier."""
        total = self._collection.count()
        tier_counts = {}
        for tier in MemoryTier:
            try:
                results = self._collection.get(
                    where={"tier": tier.value},
                    include=[],
                )
                tier_counts[tier.value] = len(results["ids"])
            except Exception:
                tier_counts[tier.value] = 0
        return {"total": total, "by_tier": tier_counts}
