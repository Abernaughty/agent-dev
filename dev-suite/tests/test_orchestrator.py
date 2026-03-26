"""Tests for the LangGraph orchestrator.

Unit tests verify graph construction, routing logic, state management,
and memory_writes accumulation without calling real LLMs.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.orchestrator import (
    AgentState,
    GraphState,
    WorkflowStatus,
    build_graph,
    create_workflow,
    route_after_qa,
    flush_memory_node,
    _infer_module,
    MAX_RETRIES,
    TOKEN_BUDGET,
)
from src.agents.architect import Blueprint
from src.agents.qa import FailureReport


# -- Graph Construction Tests --

class TestGraphConstruction:
    def test_graph_builds(self):
        graph = build_graph()
        assert graph is not None

    def test_graph_compiles(self):
        workflow = create_workflow()
        assert workflow is not None

    def test_graph_has_expected_nodes(self):
        graph = build_graph()
        compiled = graph.compile()
        node_names = set(compiled.get_graph().nodes.keys())
        assert "architect" in node_names
        assert "developer" in node_names
        assert "qa" in node_names
        assert "flush_memory" in node_names


# -- Routing Logic Tests --

class TestRouting:
    """route_after_qa expects a GraphState (TypedDict/dict), not AgentState."""

    def test_route_pass_goes_to_flush(self):
        """PASSED now routes to flush_memory instead of END."""
        state: GraphState = {"status": WorkflowStatus.PASSED, "retry_count": 0, "tokens_used": 0}
        assert route_after_qa(state) == "flush_memory"

    def test_route_fail_retries_developer(self):
        state: GraphState = {"status": WorkflowStatus.REVIEWING, "retry_count": 0, "tokens_used": 0}
        assert route_after_qa(state) == "developer"

    def test_route_escalate_goes_to_architect(self):
        state: GraphState = {"status": WorkflowStatus.ESCALATED, "retry_count": 0, "tokens_used": 0}
        assert route_after_qa(state) == "architect"

    def test_route_max_retries_goes_to_flush(self):
        """Max retries now routes to flush_memory to save accumulated writes."""
        state: GraphState = {"status": WorkflowStatus.REVIEWING, "retry_count": MAX_RETRIES, "tokens_used": 0}
        assert route_after_qa(state) == "flush_memory"

    def test_route_token_budget_goes_to_flush(self):
        """Token budget exhaustion now routes to flush_memory."""
        state: GraphState = {"status": WorkflowStatus.REVIEWING, "retry_count": 0, "tokens_used": TOKEN_BUDGET}
        assert route_after_qa(state) == "flush_memory"

    def test_route_escalate_but_max_retries_goes_to_flush(self):
        state: GraphState = {"status": WorkflowStatus.ESCALATED, "retry_count": MAX_RETRIES, "tokens_used": 0}
        assert route_after_qa(state) == "flush_memory"

    def test_route_failed_ends(self):
        """D2: FAILED status should always exit the graph (no memory flush)."""
        state: GraphState = {"status": WorkflowStatus.FAILED, "retry_count": 0, "tokens_used": 0}
        assert route_after_qa(state) == "__end__"


# -- State Model Tests --

class TestAgentState:
    def test_default_state(self):
        state = AgentState()
        assert state.status == WorkflowStatus.PLANNING
        assert state.retry_count == 0
        assert state.tokens_used == 0
        assert state.trace == []
        assert state.blueprint is None
        assert state.memory_writes == []

    def test_state_with_blueprint(self):
        bp = Blueprint(
            task_id="test-1",
            target_files=["app.py"],
            instructions="Build a hello world app",
            constraints=["Use Python"],
            acceptance_criteria=["Prints hello world"],
        )
        state = AgentState(blueprint=bp, status=WorkflowStatus.BUILDING)
        assert state.blueprint.task_id == "test-1"
        assert len(state.blueprint.target_files) == 1

    def test_state_with_failure_report(self):
        report = FailureReport(
            task_id="test-1",
            status="fail",
            tests_passed=3,
            tests_failed=2,
            errors=["TypeError in line 42"],
            failed_files=["app.py"],
            is_architectural=False,
            recommendation="Fix the type error",
        )
        state = AgentState(failure_report=report, retry_count=1)
        assert state.failure_report.tests_failed == 2
        assert not state.failure_report.is_architectural

    def test_state_with_memory_writes(self):
        """Task 5: memory_writes accumulates in state."""
        writes = [
            {"content": "Auth uses JWT", "tier": "l1", "module": "auth", "source_agent": "developer"},
            {"content": "RLS required on all tables", "tier": "l0-discovered", "module": "auth", "source_agent": "qa"},
        ]
        state = AgentState(memory_writes=writes)
        assert len(state.memory_writes) == 2
        assert state.memory_writes[0]["tier"] == "l1"
        assert state.memory_writes[1]["tier"] == "l0-discovered"


# -- flush_memory_node Tests --

class TestFlushMemoryNode:
    def test_flush_no_writes(self):
        """flush_memory_node handles empty memory_writes gracefully."""
        state: GraphState = {"trace": [], "memory_writes": []}
        result = flush_memory_node(state)
        assert "flush_memory: no writes to flush" in result["trace"]

    @patch("src.orchestrator._get_memory_store")
    @patch("src.orchestrator.summarize_writes_sync")
    def test_flush_writes_to_store(self, mock_summarizer, mock_get_store):
        """flush_memory_node writes entries to the memory store."""
        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        writes = [
            {"content": "Auth uses JWT", "tier": "l1", "module": "auth",
             "source_agent": "developer", "confidence": 1.0,
             "sandbox_origin": "locked-down", "related_files": "auth.js",
             "task_id": "task-1"},
        ]
        mock_summarizer.return_value = writes

        state: GraphState = {"trace": [], "memory_writes": writes}
        result = flush_memory_node(state)

        mock_store.add_l1.assert_called_once()
        assert "flush_memory: wrote 1 entries to store" in result["trace"]

    @patch("src.orchestrator._get_memory_store")
    @patch("src.orchestrator.summarize_writes_sync")
    def test_flush_routes_tiers_correctly(self, mock_summarizer, mock_get_store):
        """flush_memory_node routes entries to the correct tier methods."""
        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        writes = [
            {"content": "L1 entry", "tier": "l1", "module": "auth", "source_agent": "dev"},
            {"content": "L2 entry", "tier": "l2", "module": "auth", "source_agent": "qa"},
            {"content": "L0-D entry", "tier": "l0-discovered", "module": "auth", "source_agent": "qa"},
        ]
        mock_summarizer.return_value = writes

        state: GraphState = {"trace": [], "memory_writes": writes}
        flush_memory_node(state)

        assert mock_store.add_l1.call_count == 1
        assert mock_store.add_l2.call_count == 1
        assert mock_store.add_l0_discovered.call_count == 1

    @patch("src.orchestrator._get_memory_store")
    @patch("src.orchestrator.summarize_writes_sync")
    def test_flush_survives_store_failure(self, mock_summarizer, mock_get_store):
        """flush_memory_node degrades gracefully if store is unreachable."""
        mock_get_store.side_effect = Exception("Chroma unreachable")
        mock_summarizer.return_value = [{"content": "test", "tier": "l1"}]

        state: GraphState = {"trace": [], "memory_writes": [{"content": "test", "tier": "l1"}]}
        result = flush_memory_node(state)
        assert any("store write failed" in t for t in result["trace"])


# -- _infer_module Tests --

class TestInferModule:
    def test_infer_from_src_path(self):
        assert _infer_module(["src/middleware/auth.js"]) == "middleware"

    def test_infer_from_nested_path(self):
        assert _infer_module(["src/lib/supabase.js"]) == "lib"

    def test_infer_from_top_level(self):
        assert _infer_module(["tests/auth.test.js"]) == "tests"

    def test_infer_from_flat_file(self):
        assert _infer_module(["app.py"]) == "global"

    def test_infer_empty(self):
        assert _infer_module([]) == "global"
