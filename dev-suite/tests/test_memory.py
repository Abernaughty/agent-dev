"""Tests for the Chroma memory store."""

import time

import pytest

from src.memory.chroma_store import ChromaMemoryStore, MemoryTier


@pytest.fixture
def store(tmp_path):
    """Create a fresh in-memory store for each test."""
    return ChromaMemoryStore(
        persist_dir=str(tmp_path / "test_chroma"),
        collection_name="test_memory",
    )


class TestMemoryTiers:
    def test_add_and_query_l0_core(self, store):
        store.add_l0_core("Use TypeScript strict mode", module="rules")
        results = store.query("TypeScript")
        assert len(results) == 1
        assert results[0]["tier"] == "l0-core"
        assert results[0]["verified"] is True

    def test_add_and_query_l1(self, store):
        store.add_l1("Auth module uses JWT with refresh tokens", module="auth", source_agent="architect")
        results = store.query("authentication", module="auth")
        assert len(results) == 1
        assert results[0]["tier"] == "l1"
        assert results[0]["source_agent"] == "architect"

    def test_l0_discovered_starts_unverified(self, store):
        store.add_l0_discovered("This API requires auth headers", source_agent="lead_dev")
        results = store.query("API auth")
        assert len(results) == 1
        assert results[0]["verified"] is False

    def test_query_filters_by_tier(self, store):
        store.add_l0_core("Project uses Python")
        store.add_l1("Utils module has helper functions", module="utils")
        store.add_l2("Just discussed the auth flow", module="auth")

        l0_only = store.query("project", tiers=[MemoryTier.L0_CORE])
        assert all(r["tier"] == "l0-core" for r in l0_only)

    def test_query_filters_by_module(self, store):
        store.add_l1("Auth uses JWT", module="auth")
        store.add_l1("Database uses Redis", module="database")

        auth_results = store.query("module context", module="auth")
        assert all(r["module"] == "auth" for r in auth_results)


class TestApprovalWorkflow:
    def test_pending_approvals(self, store):
        store.add_l0_discovered("Need CORS headers", source_agent="lead_dev")
        store.add_l0_discovered("Rate limiting required", source_agent="qa")

        pending = store.get_pending_approvals()
        assert len(pending) == 2
        assert all(p["hours_remaining"] is not None for p in pending)

    def test_approve_discovered(self, store):
        entry_id = store.add_l0_discovered("Need CORS headers", source_agent="lead_dev")
        assert store.approve_discovered(entry_id) is True

        # Should no longer appear in pending
        pending = store.get_pending_approvals()
        assert len(pending) == 0

        # Should still be queryable and now verified
        results = store.query("CORS")
        assert len(results) == 1
        assert results[0]["verified"] is True

    def test_reject_discovered(self, store):
        entry_id = store.add_l0_discovered("Wrong constraint", source_agent="lead_dev")
        assert store.reject_discovered(entry_id) is True

        results = store.query("Wrong constraint")
        assert len(results) == 0


class TestExpiry:
    def test_l2_has_expiry(self, store):
        store.add_l2("Ephemeral context", module="task")
        results = store.query("ephemeral")
        # Should be visible immediately (not yet expired)
        assert len(results) == 1
        assert results[0]["tier"] == "l2"


class TestStats:
    def test_stats_by_tier(self, store):
        store.add_l0_core("Rule 1")
        store.add_l0_core("Rule 2")
        store.add_l1("Context 1")
        store.add_l2("Chat 1")

        stats = store.stats()
        assert stats["total"] == 4
        assert stats["by_tier"]["l0-core"] == 2
        assert stats["by_tier"]["l1"] == 1
        assert stats["by_tier"]["l2"] == 1
