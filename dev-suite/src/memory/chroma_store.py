"""Chroma-backed tiered memory store.

Implements the MemoryStore protocol using Chroma with metadata tagging.
Supports three backend modes via config:
  - chroma-ephemeral: In-memory, for tests (EphemeralClient)
  - chroma-local: SQLite-backed persistent (PersistentClient) [default]
  - chroma-server: HTTP client for multi-agent (HttpClient)

Designed to be database-agnostic for future pgvector migration.
"""

from __future__ import annotations

import time
from pathlib import Path

import chromadb
from chromadb.api import ClientAPI

from .protocol import (
    L0_DISCOVERED_TTL,
    L2_TTL,
    MemoryEntry,
    MemoryQueryResult,
    MemoryTier,
)


class ChromaMemoryStore:
    """Tiered memory store backed by Chroma.

    Satisfies the MemoryStore protocol. All metadata fields from the
    enriched schema are stored and queryable.

    Usage:
        # Local persistent (default)
        store = ChromaMemoryStore()

        # Ephemeral (tests)
        store = ChromaMemoryStore.from_config("chroma-ephemeral")

        # HTTP client (multi-agent)
        store = ChromaMemoryStore.from_config("chroma-server", host="localhost", port=8000)
    """

    def __init__(
        self,
        client: ClientAPI | None = None,
        persist_dir: str = "./chroma_data",
        collection_name: str = "dev_suite_memory",
    ):
        if client is not None:
            self._client = client
        else:
            Path(persist_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=persist_dir)

        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @classmethod
    def from_config(
        cls,
        backend: str = "chroma-local",
        persist_dir: str = "./chroma_data",
        host: str = "localhost",
        port: int = 8000,
        collection_name: str = "dev_suite_memory",
    ) -> "ChromaMemoryStore":
        """Create a store from a config string.

        Args:
            backend: One of "chroma-ephemeral", "chroma-local", "chroma-server".
            persist_dir: Directory for persistent storage (chroma-local only).
            host: Chroma server host (chroma-server only).
            port: Chroma server port (chroma-server only).
            collection_name: Name of the Chroma collection.
        """
        if backend == "chroma-ephemeral":
            client = chromadb.EphemeralClient()
        elif backend == "chroma-server":
            client = chromadb.HttpClient(host=host, port=port)
        elif backend == "chroma-local":
            Path(persist_dir).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=persist_dir)
        else:
            raise ValueError(
                f"Unknown backend: {backend!r}. "
                "Expected: chroma-ephemeral, chroma-local, chroma-server"
            )
        return cls(client=client, collection_name=collection_name)

    # -- Write Methods --

    def add_l0_core(
        self,
        content: str,
        module: str = "global",
        *,
        source_type: str = "manual",
        related_files: str = "",
        task_id: str = "",
    ) -> str:
        """Add a core project rule. Human-only."""
        return self._add(
            content,
            MemoryTier.L0_CORE,
            module=module,
            source_agent="human",
            source_type=source_type,
            verified=True,
            mutable=False if source_type == "static-config" else True,
            confidence=1.0,
            sandbox_origin="none",
            related_files=related_files,
            task_id=task_id,
        )

    def add_l0_discovered(
        self,
        content: str,
        module: str = "global",
        source_agent: str = "unknown",
        *,
        confidence: float = 0.8,
        sandbox_origin: str = "none",
        related_files: str = "",
        task_id: str = "",
    ) -> str:
        """Add an agent-discovered constraint. Expires in 48h if not approved."""
        return self._add(
            content,
            MemoryTier.L0_DISCOVERED,
            module=module,
            source_agent=source_agent,
            source_type="discovery",
            verified=False,
            mutable=True,
            confidence=confidence,
            sandbox_origin=sandbox_origin,
            related_files=related_files,
            task_id=task_id,
            ttl=L0_DISCOVERED_TTL,
        )

    def add_l1(
        self,
        content: str,
        module: str = "global",
        source_agent: str = "unknown",
        *,
        confidence: float = 1.0,
        sandbox_origin: str = "none",
        related_files: str = "",
        task_id: str = "",
    ) -> str:
        """Add module-level context."""
        return self._add(
            content,
            MemoryTier.L1,
            module=module,
            source_agent=source_agent,
            source_type="task-output",
            verified=True,
            mutable=True,
            confidence=confidence,
            sandbox_origin=sandbox_origin,
            related_files=related_files,
            task_id=task_id,
        )

    def add_l2(
        self,
        content: str,
        module: str = "global",
        source_agent: str = "unknown",
        *,
        related_files: str = "",
        task_id: str = "",
    ) -> str:
        """Add ephemeral task context. Auto-expires."""
        return self._add(
            content,
            MemoryTier.L2,
            module=module,
            source_agent=source_agent,
            source_type="task-output",
            verified=True,
            mutable=True,
            confidence=1.0,
            sandbox_origin="none",
            related_files=related_files,
            task_id=task_id,
            ttl=L2_TTL,
        )

    def _add(
        self,
        content: str,
        tier: MemoryTier,
        module: str,
        source_agent: str,
        source_type: str,
        verified: bool,
        mutable: bool,
        confidence: float,
        sandbox_origin: str,
        related_files: str,
        task_id: str,
        ttl: int | None = None,
    ) -> str:
        """Internal: add an entry to Chroma with full metadata."""
        now = time.time()
        entry_id = f"{tier.value}_{module}_{int(now * 1000)}"
        expires_at = now + ttl if ttl else 0.0

        self._collection.add(
            ids=[entry_id],
            documents=[content],
            metadatas=[
                {
                    "tier": tier.value,
                    "module": module,
                    "source_agent": source_agent,
                    "source_type": source_type,
                    "task_id": task_id,
                    "related_files": related_files,
                    "confidence": confidence,
                    "verified": verified,
                    "sandbox_origin": sandbox_origin,
                    "mutable": mutable,
                    "created_at": now,
                    "expires_at": expires_at,
                }
            ],
        )
        return entry_id

    # -- Read Methods --

    def query(
        self,
        query_text: str,
        module: str | None = None,
        tiers: list[MemoryTier] | None = None,
        n_results: int = 10,
        task_id: str | None = None,
    ) -> list[MemoryQueryResult]:
        """Query memory with optional tier, module, and task_id filters.

        Returns list of MemoryQueryResult. Automatically excludes expired entries.
        """
        where_clauses: list[dict] = []
        if tiers:
            where_clauses.append({"tier": {"$in": [t.value for t in tiers]}})
        if module:
            where_clauses.append({"module": module})
        if task_id:
            where_clauses.append({"task_id": task_id})

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

        entries: list[MemoryQueryResult] = []
        now = time.time()
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            expires = meta.get("expires_at", 0.0)
            if expires > 0 and expires < now:
                continue

            entries.append(
                MemoryQueryResult(
                    content=doc,
                    tier=meta["tier"],
                    module=meta.get("module", "global"),
                    source_agent=meta.get("source_agent", "unknown"),
                    source_type=meta.get("source_type", "manual"),
                    task_id=meta.get("task_id", ""),
                    related_files=meta.get("related_files", ""),
                    confidence=meta.get("confidence", 1.0),
                    verified=meta.get("verified", True),
                    sandbox_origin=meta.get("sandbox_origin", "none"),
                    mutable=meta.get("mutable", True),
                    score=1 - dist,
                )
            )

        return entries

    def get_pending_approvals(self) -> list[dict]:
        """Get L0-Discovered entries awaiting human approval."""
        results = self._collection.get(
            where={
                "$and": [
                    {"tier": MemoryTier.L0_DISCOVERED.value},
                    {"verified": False},
                ]
            },
            include=["documents", "metadatas"],
        )
        if not results["ids"]:
            return []

        now = time.time()
        pending = []
        for entry_id, doc, meta in zip(
            results["ids"], results["documents"], results["metadatas"]
        ):
            expires = meta.get("expires_at", 0.0)
            if expires > 0 and expires < now:
                continue
            pending.append(
                {
                    "id": entry_id,
                    "content": doc,
                    "module": meta.get("module", "global"),
                    "source_agent": meta.get("source_agent", "unknown"),
                    "confidence": meta.get("confidence", 0.8),
                    "sandbox_origin": meta.get("sandbox_origin", "none"),
                    "related_files": meta.get("related_files", ""),
                    "task_id": meta.get("task_id", ""),
                    "hours_remaining": (
                        max(0, (expires - now) / 3600) if expires > 0 else None
                    ),
                }
            )
        return pending

    # -- Approval --

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

    # -- Cleanup --

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = time.time()
        results = self._collection.get(
            where={"expires_at": {"$gt": 0.0}},
            include=["metadatas"],
        )
        if not results["ids"]:
            return 0

        expired_ids = [
            eid
            for eid, meta in zip(results["ids"], results["metadatas"])
            if meta.get("expires_at", 0.0) > 0 and meta["expires_at"] < now
        ]

        if expired_ids:
            self._collection.delete(ids=expired_ids)
        return len(expired_ids)

    # -- Stats --

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
