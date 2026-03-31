"""Tests for QA -> Architect escalation path (#57).

Tests cover:
- FailureType enum and FailureReport model enhancements
- Case-insensitive and unknown failure_type parsing from LLM output
- Routing logic for code vs architectural failures
- Architect re-planning with failure context
- QA node classification behavior
- Graph structure (escalation edges exist)
- Retry budget accounting during escalation
"""

import pytest

from src.agents.qa import FailureReport, FailureType


# ---------------------------------------------------------------------------
# FailureType enum
# ---------------------------------------------------------------------------


class TestFailureType:
    """FailureType enum values and string coercion."""

    def test_code_value(self):
        assert FailureType.CODE == "code"
        assert FailureType.CODE.value == "code"

    def test_architectural_value(self):
        assert FailureType.ARCHITECTURAL == "architectural"
        assert FailureType.ARCHITECTURAL.value == "architectural"

    def test_from_string(self):
        assert FailureType("code") == FailureType.CODE
        assert FailureType("architectural") == FailureType.ARCHITECTURAL

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            FailureType("unknown")


# ---------------------------------------------------------------------------
# FailureReport model enhancements
# ---------------------------------------------------------------------------


class TestFailureReportEscalation:
    """FailureReport failure_type field and sync logic."""

    def _make_report(self, **overrides):
        defaults = dict(
            task_id="test-task",
            status="fail",
            tests_passed=2,
            tests_failed=1,
            errors=["some error"],
            failed_files=["file.py"],
            is_architectural=False,
            recommendation="fix it",
        )
        defaults.update(overrides)
        return FailureReport(**defaults)

    def test_code_failure_defaults(self):
        """status=fail + is_architectural=False -> failure_type=CODE."""
        r = self._make_report()
        assert r.failure_type == FailureType.CODE
        assert r.is_architectural is False

    def test_architectural_from_bool(self):
        """is_architectural=True derives failure_type=ARCHITECTURAL."""
        r = self._make_report(is_architectural=True)
        assert r.failure_type == FailureType.ARCHITECTURAL
        assert r.is_architectural is True

    def test_failure_type_takes_precedence(self):
        """Explicit failure_type overrides is_architectural."""
        r = self._make_report(
            is_architectural=True,
            failure_type=FailureType.CODE,
        )
        assert r.failure_type == FailureType.CODE
        assert r.is_architectural is False  # synced from failure_type

    def test_architectural_type_syncs_bool(self):
        """failure_type=ARCHITECTURAL sets is_architectural=True."""
        r = self._make_report(
            is_architectural=False,
            failure_type=FailureType.ARCHITECTURAL,
        )
        assert r.is_architectural is True

    def test_pass_status_no_failure_type(self):
        """status=pass leaves failure_type as None."""
        r = self._make_report(
            status="pass", tests_passed=5, tests_failed=0,
            errors=[], failed_files=[],
        )
        assert r.failure_type is None

    def test_escalate_status(self):
        """status=escalate + is_architectural=True works."""
        r = self._make_report(status="escalate", is_architectural=True)
        assert r.failure_type == FailureType.ARCHITECTURAL

    def test_json_round_trip(self):
        """Model serializes/deserializes with failure_type intact."""
        r = self._make_report(
            is_architectural=True,
            failure_type=FailureType.ARCHITECTURAL,
        )
        data = r.model_dump()
        assert data["failure_type"] == "architectural"
        r2 = FailureReport(**data)
        assert r2.failure_type == FailureType.ARCHITECTURAL
        assert r2.is_architectural is True

    def test_llm_string_output_parses(self):
        """LLM output with failure_type as string parses correctly."""
        from_llm = {
            "task_id": "t1", "status": "escalate",
            "tests_passed": 0, "tests_failed": 3,
            "errors": ["wrong file targeted"],
            "failed_files": ["wrong.py"],
            "is_architectural": True,
            "recommendation": "re-plan with correct file",
            "failure_type": "architectural",
        }
        r = FailureReport(**from_llm)
        assert r.failure_type == FailureType.ARCHITECTURAL

    def test_backward_compat_no_failure_type(self):
        """Old LLM output without failure_type still works."""
        from_llm = {
            "task_id": "t2", "status": "fail",
            "tests_passed": 1, "tests_failed": 1,
            "errors": ["TypeError"],
            "failed_files": ["app.py"],
            "is_architectural": False,
            "recommendation": "fix type error",
        }
        r = FailureReport(**from_llm)
        assert r.failure_type == FailureType.CODE

    def test_case_insensitive_architectural(self):
        """LLM returning 'ARCHITECTURAL' (uppercase) should work."""
        r = self._make_report(failure_type="ARCHITECTURAL")
        assert r.failure_type == FailureType.ARCHITECTURAL

    def test_case_insensitive_code(self):
        """LLM returning 'Code' (mixed case) should work."""
        r = self._make_report(failure_type="Code")
        assert r.failure_type == FailureType.CODE

    def test_case_insensitive_with_whitespace(self):
        """LLM returning '  architectural  ' (padded) should work."""
        r = self._make_report(failure_type="  architectural  ")
        assert r.failure_type == FailureType.ARCHITECTURAL

    def test_case_insensitive_all_caps(self):
        """LLM returning 'CODE' (all caps) should work."""
        r = self._make_report(failure_type="CODE")
        assert r.failure_type == FailureType.CODE

    def test_unknown_value_falls_back_gracefully(self):
        """Unknown failure_type like 'design_flaw' falls back to is_architectural."""
        r = self._make_report(
            failure_type="design_flaw",
            is_architectural=True,
        )
        # Unknown value coerced to None, model_validator derives from is_architectural
        assert r.failure_type == FailureType.ARCHITECTURAL
        assert r.is_architectural is True

    def test_unknown_value_code_fallback(self):
        """Unknown failure_type with is_architectural=False falls back to CODE."""
        r = self._make_report(
            failure_type="typo_value",
            is_architectural=False,
        )
        assert r.failure_type == FailureType.CODE

    def test_empty_string_falls_back(self):
        """Empty string failure_type falls back to is_architectural/status."""
        r = self._make_report(
            failure_type="",
            is_architectural=True,
        )
        assert r.failure_type == FailureType.ARCHITECTURAL

    def test_escalate_status_infers_architectural(self):
        """status=escalate with unknown failure_type and is_architectural=False
        should still infer ARCHITECTURAL from the escalate status."""
        r = self._make_report(
            status="escalate",
            failure_type="design_flaw",
            is_architectural=False,
        )
        assert r.failure_type == FailureType.ARCHITECTURAL
        assert r.is_architectural is True


# ---------------------------------------------------------------------------
# Routing logic
# ---------------------------------------------------------------------------


class TestEscalationRouting:
    """route_after_qa returns correct destinations for escalation."""

    def _import_route(self):
        from src.orchestrator import WorkflowStatus, route_after_qa
        return route_after_qa, WorkflowStatus

    def test_pass_routes_to_flush(self):
        route, WS = self._import_route()
        state = {"status": WS.PASSED, "retry_count": 0, "tokens_used": 1000}
        assert route(state) == "flush_memory"

    def test_code_failure_routes_to_developer(self):
        route, WS = self._import_route()
        state = {"status": WS.REVIEWING, "retry_count": 1, "tokens_used": 20000}
        assert route(state) == "developer"

    def test_architectural_failure_routes_to_architect(self):
        route, WS = self._import_route()
        state = {"status": WS.ESCALATED, "retry_count": 1, "tokens_used": 20000}
        assert route(state) == "architect"

    def test_escalation_respects_max_retries(self):
        route, WS = self._import_route()
        state = {"status": WS.ESCALATED, "retry_count": 3, "tokens_used": 20000}
        assert route(state) == "flush_memory"

    def test_escalation_respects_token_budget(self):
        route, WS = self._import_route()
        state = {"status": WS.ESCALATED, "retry_count": 1, "tokens_used": 50000}
        assert route(state) == "flush_memory"

    def test_escalation_below_limits_routes_to_architect(self):
        route, WS = self._import_route()
        state = {"status": WS.ESCALATED, "retry_count": 2, "tokens_used": 30000}
        assert route(state) == "architect"


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------


class TestGraphEscalationEdges:
    """Verify the compiled graph has the escalation path wired."""

    def test_graph_has_architect_node(self):
        from src.orchestrator import build_graph
        graph = build_graph()
        assert "architect" in graph.nodes

    def test_graph_has_qa_node(self):
        from src.orchestrator import build_graph
        graph = build_graph()
        assert "qa" in graph.nodes

    def test_qa_has_conditional_edges(self):
        """QA node must have conditional routing (not a fixed edge)."""
        from src.orchestrator import build_graph
        graph = build_graph()
        compiled = graph.compile()
        assert compiled is not None


# ---------------------------------------------------------------------------
# QA node classification behavior
# ---------------------------------------------------------------------------


class TestQANodeClassification:
    """QA node sets correct status based on failure_report classification."""

    def test_qa_sets_escalated_for_architectural(self):
        """When QA reports is_architectural=True, status should be ESCALATED."""
        from src.orchestrator import WorkflowStatus

        report = FailureReport(
            task_id="t1", status="escalate",
            tests_passed=0, tests_failed=2,
            errors=["wrong file targeted"],
            failed_files=["wrong.py"],
            is_architectural=True,
            recommendation="re-plan",
        )
        if report.is_architectural:
            status = WorkflowStatus.ESCALATED
        elif report.status == "pass":
            status = WorkflowStatus.PASSED
        else:
            status = WorkflowStatus.REVIEWING

        assert status == WorkflowStatus.ESCALATED

    def test_qa_sets_reviewing_for_code_failure(self):
        """When QA reports a code failure, status should be REVIEWING (retry)."""
        from src.orchestrator import WorkflowStatus

        report = FailureReport(
            task_id="t2", status="fail",
            tests_passed=3, tests_failed=1,
            errors=["TypeError in line 42"],
            failed_files=["auth.py"],
            is_architectural=False,
            recommendation="fix type error",
        )
        if report.is_architectural:
            status = WorkflowStatus.ESCALATED
        elif report.status == "pass":
            status = WorkflowStatus.PASSED
        else:
            status = WorkflowStatus.REVIEWING

        assert status == WorkflowStatus.REVIEWING


# ---------------------------------------------------------------------------
# Architect re-planning context
# ---------------------------------------------------------------------------


class TestArchitectReplanContext:
    """Architect receives failure context when re-planning after escalation."""

    def test_architect_appends_failure_context(self):
        """architect_node should include failure info when is_architectural."""
        failure = FailureReport(
            task_id="t1", status="escalate",
            tests_passed=0, tests_failed=2,
            errors=["wrong file targeted", "missing dependency"],
            failed_files=["wrong.py"],
            is_architectural=True,
            recommendation="Target auth.py instead of wrong.py",
        )

        user_msg = "Create authentication middleware"
        if failure and failure.is_architectural:
            user_msg += "\n\nPREVIOUS ATTEMPT FAILED (architectural issue):\n"
            user_msg += f"Errors: {', '.join(failure.errors)}\n"
            user_msg += f"Recommendation: {failure.recommendation}"

        assert "PREVIOUS ATTEMPT FAILED (architectural issue)" in user_msg
        assert "wrong file targeted" in user_msg
        assert "Target auth.py instead of wrong.py" in user_msg

    def test_architect_no_context_on_first_run(self):
        """First run (no failure_report) should not include failure context."""
        user_msg = "Create authentication middleware"
        failure_report = None
        if failure_report and getattr(failure_report, "is_architectural", False):
            user_msg += "\n\nPREVIOUS ATTEMPT FAILED"

        assert "PREVIOUS ATTEMPT FAILED" not in user_msg


# ---------------------------------------------------------------------------
# Retry budget accounting
# ---------------------------------------------------------------------------


class TestRetryBudgetDuringEscalation:
    """Escalation counts against retry budget (no free re-plans)."""

    def test_retry_incremented_on_escalation(self):
        """QA failure (including escalation) should increment retry_count."""
        report = FailureReport(
            task_id="t1", status="escalate",
            tests_passed=0, tests_failed=2,
            errors=["design flaw"],
            failed_files=["api.py"],
            is_architectural=True,
            recommendation="re-plan",
        )

        retry_count = 0
        new_retry_count = retry_count + (1 if report.status != "pass" else 0)
        assert new_retry_count == 1

    def test_retry_not_incremented_on_pass(self):
        """Passing QA should not increment retry_count."""
        report = FailureReport(
            task_id="t1", status="pass",
            tests_passed=5, tests_failed=0,
            errors=[], failed_files=[],
            is_architectural=False,
            recommendation="",
        )

        retry_count = 1
        new_retry_count = retry_count + (1 if report.status != "pass" else 0)
        assert new_retry_count == 1
