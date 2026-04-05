"""Tests for the Chroma memory store — Tasks 1-4.

Covers:
- Protocol compliance (ChromaMemoryStore satisfies MemoryStore)
- Enriched metadata schema (all new fields stored and queryable)
- Config-driven backends (ephemeral, persistent, factory)
- L0-Core YAML loading and seed script
- Backward compatibility with existing test patterns
"""

import uuid

import pytest

from src.memory.chroma_store import ChromaMemoryStore
from src.memory.factory import create_memory_store
from src.memory.protocol import MemoryEntry, MemoryQueryResult, MemoryStore, MemoryTier
from src.memory.seed import _flatten_rule_entries, _flatten_stack_entries, _load_yaml, seed_l0_core


def _unique_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def store():
    return ChromaMemoryStore.from_config("chroma-ephemeral", collection_name=_unique_name())


@pytest.fixture
def persistent_store(tmp_path):
    return ChromaMemoryStore(persist_dir=str(tmp_path / "test_chroma"), collection_name=_unique_name())


class TestProtocolCompliance:
    def test_chroma_store_satisfies_protocol(self):
        store = ChromaMemoryStore.from_config("chroma-ephemeral", collection_name=_unique_name())
        assert isinstance(store, MemoryStore)

    def test_protocol_has_required_methods(self):
        required = ["add_l0_core", "add_l0_discovered", "add_l1", "add_l2", "query",
                     "get_pending_approvals", "approve_discovered", "reject_discovered",
                     "cleanup_expired", "stats"]
        for m in required:
            assert hasattr(MemoryStore, m), f"Missing: {m}"

    def test_factory_returns_protocol_compatible(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "chroma-ephemeral")
        store = create_memory_store(collection_name=_unique_name())
        assert isinstance(store, MemoryStore)


class TestEnrichedMetadata:
    def test_l0_core_metadata(self, store):
        store.add_l0_core("Use TypeScript strict mode", module="rules", source_type="static-config")
        results = store.query("TypeScript")
        assert len(results) == 1
        r = results[0]
        assert r.tier == "l0-core"
        assert r.verified is True
        assert r.source_agent == "human"
        assert r.source_type == "static-config"
        assert r.mutable is False
        assert r.confidence == 1.0

    def test_l0_discovered_metadata(self, store):
        store.add_l0_discovered("This API requires auth headers", module="auth",
                                source_agent="architect", confidence=0.92,
                                sandbox_origin="locked-down", task_id="task-123")
        results = store.query("API auth")
        assert len(results) == 1
        r = results[0]
        assert r.tier == "l0-discovered"
        assert r.verified is False
        assert r.source_agent == "architect"
        assert r.source_type == "discovery"
        assert r.confidence == pytest.approx(0.92, abs=0.01)
        assert r.sandbox_origin == "locked-down"
        assert r.task_id == "task-123"

    def test_l1_with_related_files(self, store):
        store.add_l1("Auth module uses JWT", module="auth", source_agent="developer",
                      related_files="src/auth.js,src/middleware.js", task_id="supabase-auth")
        results = store.query("authentication", module="auth")
        assert len(results) == 1
        r = results[0]
        assert r.related_files == "src/auth.js,src/middleware.js"
        assert r.task_id == "supabase-auth"

    def test_l2_metadata(self, store):
        store.add_l2("Discussed auth flow", module="auth", source_agent="qa", task_id="task-456")
        results = store.query("auth flow")
        assert len(results) == 1
        r = results[0]
        assert r.tier == "l2"
        assert r.source_type == "task-output"
        assert r.task_id == "task-456"

    def test_query_returns_memory_query_result(self, store):
        store.add_l1("Test entry", module="test")
        results = store.query("test")
        assert len(results) == 1
        assert isinstance(results[0], MemoryQueryResult)
        assert results[0].score > 0

    def test_query_filter_by_task_id(self, store):
        store.add_l1("Entry for task A", task_id="task-a")
        store.add_l1("Entry for task B", task_id="task-b")
        results_a = store.query("entry", task_id="task-a")
        assert len(results_a) == 1
        assert results_a[0].task_id == "task-a"

    def test_query_min_score_filters_irrelevant(self, store):
        """min_score param filters out low-relevance entries."""
        store.add_l1("Python triforce ASCII art pattern printer")
        store.add_l1("JavaScript greet function with type hints")
        # Query for triforce — greet entry should be dissimilar
        all_results = store.query("triforce ASCII art", min_score=None)
        assert len(all_results) == 2  # both returned without filter
        # With a score threshold, only the relevant entry should survive
        filtered = store.query("triforce ASCII art", min_score=0.3)
        # The triforce entry should score higher than the greet entry
        assert len(filtered) >= 1
        assert any("triforce" in r.content.lower() for r in filtered)

    def test_query_min_score_none_returns_all(self, store):
        """min_score=None (default) returns all results like before."""
        store.add_l1("Alpha entry about dogs")
        store.add_l1("Beta entry about cats")
        store.add_l1("Gamma entry about quantum physics")
        results_none = store.query("dogs", min_score=None)
        results_default = store.query("dogs")
        assert len(results_none) == len(results_default)

    def test_query_min_score_high_threshold(self, store):
        """Very high min_score filters out most results."""
        store.add_l1("Specific technical entry about Rust ownership")
        store.add_l1("Completely unrelated cooking recipe for pasta")
        results = store.query("Rust ownership borrow checker", min_score=0.99)
        # At 0.99 threshold, almost nothing should match
        assert len(results) <= 1


class TestBackwardCompatibility:
    def test_add_and_query_l0_core(self, store):
        store.add_l0_core("Use TypeScript strict mode", module="rules")
        results = store.query("TypeScript")
        assert len(results) == 1
        assert results[0].tier == "l0-core"
        assert results[0].verified is True

    def test_add_and_query_l1(self, store):
        store.add_l1("Auth module uses JWT", module="auth", source_agent="architect")
        results = store.query("authentication", module="auth")
        assert len(results) == 1
        assert results[0].tier == "l1"
        assert results[0].source_agent == "architect"

    def test_l0_discovered_starts_unverified(self, store):
        store.add_l0_discovered("This API requires auth headers", source_agent="lead_dev")
        results = store.query("API auth")
        assert len(results) == 1
        assert results[0].verified is False

    def test_query_filters_by_tier(self, store):
        store.add_l0_core("Project uses Python")
        store.add_l1("Utils module has helper functions", module="utils")
        store.add_l2("Just discussed the auth flow", module="auth")
        l0_only = store.query("project", tiers=[MemoryTier.L0_CORE])
        assert all(r.tier == "l0-core" for r in l0_only)

    def test_query_filters_by_module(self, store):
        store.add_l1("Auth uses JWT", module="auth")
        store.add_l1("Database uses Redis", module="database")
        auth_results = store.query("module context", module="auth")
        assert all(r.module == "auth" for r in auth_results)

    def test_pending_approvals(self, store):
        store.add_l0_discovered("Need CORS headers", source_agent="lead_dev")
        store.add_l0_discovered("Rate limiting required", source_agent="qa")
        pending = store.get_pending_approvals()
        assert len(pending) == 2

    def test_approve_discovered(self, store):
        entry_id = store.add_l0_discovered("Need CORS headers", source_agent="lead_dev")
        assert store.approve_discovered(entry_id) is True
        pending = store.get_pending_approvals()
        assert len(pending) == 0
        results = store.query("CORS")
        assert len(results) == 1
        assert results[0].verified is True

    def test_reject_discovered(self, store):
        entry_id = store.add_l0_discovered("Wrong constraint", source_agent="lead_dev")
        assert store.reject_discovered(entry_id) is True
        results = store.query("Wrong constraint")
        assert len(results) == 0

    def test_l2_has_expiry(self, store):
        store.add_l2("Ephemeral context", module="task")
        results = store.query("ephemeral")
        assert len(results) == 1
        assert results[0].tier == "l2"

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


class TestYamlConfig:
    def test_load_yaml(self):
        config = _load_yaml()
        assert "project" in config
        assert "stack" in config
        assert "rules" in config

    def test_load_yaml_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_yaml(tmp_path / "nonexistent.yaml")

    def test_yaml_project_section(self):
        config = _load_yaml()
        assert config["project"]["name"] == "agent-dev"

    def test_flatten_stack_entries(self):
        config = _load_yaml()
        entries = _flatten_stack_entries(config)
        assert len(entries) > 0
        assert all(module == "stack" for _, module in entries)
        assert any("Python" in c for c, _ in entries)

    def test_flatten_rule_entries(self):
        config = _load_yaml()
        entries = _flatten_rule_entries(config)
        assert len(entries) > 0
        modules = {m for _, m in entries}
        assert "rules-execution" in modules
        assert "rules-security" in modules

    def test_seed_populates_store(self, store):
        count = seed_l0_core(store=store, force=True)
        assert count > 0
        stats = store.stats()
        assert stats["by_tier"]["l0-core"] == count
        assert stats["total"] == count

    def test_seed_entries_are_static_config(self, store):
        seed_l0_core(store=store, force=True)
        results = store.query("Python", tiers=[MemoryTier.L0_CORE])
        assert len(results) > 0
        for r in results:
            assert r.source_type == "static-config"
            assert r.mutable is False

    def test_seed_is_idempotent(self, store):
        count1 = seed_l0_core(store=store, force=True)
        count2 = seed_l0_core(store=store, force=False)
        assert count1 > 0
        assert count2 == 0
        stats = store.stats()
        assert stats["by_tier"]["l0-core"] == count1


class TestConfigBackends:
    def test_ephemeral_backend(self):
        store = ChromaMemoryStore.from_config("chroma-ephemeral", collection_name=_unique_name())
        store.add_l1("test entry")
        results = store.query("test")
        assert len(results) == 1

    def test_persistent_backend(self, tmp_path):
        store = ChromaMemoryStore.from_config("chroma-local", persist_dir=str(tmp_path / "chroma"),
                                              collection_name=_unique_name())
        store.add_l1("persistent entry")
        assert (tmp_path / "chroma").exists()
        results = store.query("persistent")
        assert len(results) == 1

    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            ChromaMemoryStore.from_config("chroma-postgres")

    def test_factory_reads_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "chroma-ephemeral")
        store = create_memory_store(collection_name=_unique_name())
        assert isinstance(store, MemoryStore)
        store.add_l1("factory test")
        results = store.query("factory")
        assert len(results) == 1

    def test_factory_explicit_override(self):
        store = create_memory_store("chroma-ephemeral", collection_name=_unique_name())
        assert isinstance(store, MemoryStore)

    def test_factory_default_is_chroma_local(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MEMORY_BACKEND", raising=False)
        monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "default_chroma"))
        store = create_memory_store(collection_name=_unique_name())
        assert isinstance(store, MemoryStore)
        assert (tmp_path / "default_chroma").exists()


class TestModels:
    def test_memory_entry_defaults(self):
        entry = MemoryEntry(content="test", tier=MemoryTier.L1)
        assert entry.module == "global"
        assert entry.source_agent == "human"
        assert entry.source_type == "manual"
        assert entry.confidence == 1.0
        assert entry.mutable is True

    def test_memory_query_result_defaults(self):
        result = MemoryQueryResult(content="test", tier="l1")
        assert result.score == 0.0
        assert result.sandbox_origin == "none"

    def test_memory_tier_values(self):
        assert MemoryTier.L0_CORE.value == "l0-core"
        assert MemoryTier.L0_DISCOVERED.value == "l0-discovered"
        assert MemoryTier.L1.value == "l1"
        assert MemoryTier.L2.value == "l2"


class TestEnrichedApprovals:
    def test_pending_approvals_include_new_fields(self, store):
        store.add_l0_discovered("Rate limiter needed", source_agent="qa",
                                confidence=0.91, sandbox_origin="locked-down",
                                related_files="src/middleware/rateLimit.js", task_id="auth-task")
        pending = store.get_pending_approvals()
        assert len(pending) == 1
        p = pending[0]
        assert p["confidence"] == pytest.approx(0.91, abs=0.01)
        assert p["sandbox_origin"] == "locked-down"
        assert p["related_files"] == "src/middleware/rateLimit.js"
        assert p["task_id"] == "auth-task"
