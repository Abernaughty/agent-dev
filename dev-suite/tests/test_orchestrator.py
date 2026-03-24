"""Tests for the LangGraph orchestrator.

Unit tests verify graph construction, routing logic, and state management
without calling real LLMs. Integration tests (marked) require API keys.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.orchestrator import (
    AgentState,
    WorkflowStatus,
    build_graph,
    create_workflow,
    route_after_qa,
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


# -- Routing Logic Tests --

class TestRouting:
    def test_route_pass_ends(self):
        state = AgentState(status=WorkflowStatus.PASSED)
        assert route_after_qa(state) == "__end__"

    def test_route_fail_retries_developer(self):
        state = AgentState(status=WorkflowStatus.REVIEWING, retry_count=0)
        assert route_after_qa(state) == "developer"

    def test_route_escalate_goes_to_architect(self):
        state = AgentState(status=WorkflowStatus.ESCALATED, retry_count=0)
        assert route_after_qa(state) == "architect"

    def test_route_max_retries_ends(self):
        state = AgentState(status=WorkflowStatus.REVIEWING, retry_count=MAX_RETRIES)
        assert route_after_qa(state) == "__end__"

    def test_route_token_budget_ends(self):
        state = AgentState(status=WorkflowStatus.REVIEWING, retry_count=0, tokens_used=TOKEN_BUDGET)
        assert route_after_qa(state) == "__end__"

    def test_route_escalate_but_max_retries_ends(self):
        state = AgentState(status=WorkflowStatus.ESCALATED, retry_count=MAX_RETRIES)
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
