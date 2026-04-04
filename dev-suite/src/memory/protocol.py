"""MemoryStore protocol — backend-agnostic interface for tiered memory.

All memory backends (Chroma ephemeral, Chroma persistent, Chroma HTTP,
future pgvector) implement this protocol. The orchestrator and API layer
depend only on this interface, never on a concrete backend.

Design notes:
- Uses typing.Protocol (structural subtyping) so backends don't need
  to inherit from a base class — they just need matching method signatures.
- All methods are synchronous for Phase 1. Async wrappers can be added
  at the protocol level in Phase 2 without changing backends.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

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


# ── Memory Entry Model ──


class MemoryEntry(BaseModel):
    """A single memory entry with full metadata.

    This is the canonical data model used across all backends.
    Chroma stores these as document + metadata dict; pgvector
    will store them as rows.
    """

    content: str
    tier: MemoryTier
    module: str = "global"
    source_agent: str = "human"
    source_type: str = "manual"  # static-config | task-output | discovery | manual
    task_id: str = ""
    related_files: str = ""  # comma-separated (Chroma metadata is flat)
    confidence: float = 1.0  # 0.0-1.0
    verified: bool = True
    sandbox_origin: str = "none"  # locked-down | permissive | none
    mutable: bool = True  # false for L0-Core static entries
    created_at: float = 0.0
    expires_at: float = 0.0  # 0.0 = never


class MemoryQueryResult(BaseModel):
    """Result from a memory query, including similarity score."""

    content: str
    tier: str
    module: str = "global"
    source_agent: str = "unknown"
    source_type: str = "manual"
    task_id: str = ""
    related_files: str = ""
    confidence: float = 1.0
    verified: bool = True
    sandbox_origin: str = "none"
    mutable: bool = True
    score: float = 0.0  # similarity score (1.0 = exact match)


# ── TTL Constants ──

L0_DISCOVERED_TTL = 48 * 60 * 60  # 48 hours in seconds
L2_TTL = 4 * 60 * 60  # 4 hours in seconds


# ── Protocol ──


@runtime_checkable
class MemoryStore(Protocol):
    """Protocol that all memory backends must satisfy.

    Methods mirror the existing ChromaMemoryStore API so the current
    implementation satisfies this protocol without changes (after the
    metadata enrichment in Task 2).
    """

    # ── Write Methods ──

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
        ...

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
        ...

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
        ...

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
        ...

    # ── Read Methods ──

    def query(
        self,
        query_text: str,
        module: str | None = None,
        tiers: list[MemoryTier] | None = None,
        n_results: int = 10,
        task_id: str | None = None,
    ) -> list[MemoryQueryResult]:
        """Query memory with optional filters. Excludes expired entries."""
        ...

    def get_pending_approvals(self) -> list[dict]:
        """Get L0-Discovered entries awaiting human approval."""
        ...

    # ── Approval ──

    def approve_discovered(self, entry_id: str) -> bool:
        """Approve an L0-Discovered entry (promote to verified, remove expiry)."""
        ...

    def reject_discovered(self, entry_id: str) -> bool:
        """Reject an L0-Discovered entry (delete it)."""
        ...

    # ── Maintenance ──

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        ...

    def stats(self) -> dict:
        """Get memory store statistics by tier."""
        ...
