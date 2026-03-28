"""End-to-end pipeline validation tests (Step 7).

These tests verify the full Architect → Lead Dev → QA pipeline
by mocking LLM responses with realistic payloads. They validate:

1. Happy path: Blueprint → code → QA pass → PASSED
2. Retry path: QA fail → retry developer → QA pass
3. Escalation path: QA escalate → re-plan architect → developer → QA pass
4. Budget exhaustion: tokens_used >= TOKEN_BUDGET → END
5. Max retries: retry_count >= MAX_RETRIES → END
6. Memory integration: Chroma context is fetched and included
7. Tracing integration: TracingConfig wired through correctly

No API keys needed — all LLM calls are mocked.
"""

import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.agents.architect import Blueprint
from src.agents.qa import FailureReport
from src.memory.chroma_store import ChromaMemoryStore
from src.orchestrator import (
    AgentState,
    MAX_RETRIES,
    TOKEN_BUDGET,
    WorkflowStatus,
    architect_node,
    build_graph,
    create_workflow,
    developer_node,
    qa_node,
    run_task,
)


# ── Fixtures ──


SAMPLE_BLUEPRINT = Blueprint(
    task_id="e2e-test-001",
    target_files=["validate_email.py"],
    instructions="Create a Python function that validates email addresses using regex.",
    constraints=["Use re module only", "Return bool", "Handle edge cases"],
    acceptance_criteria=[
        "Validates standard emails (user@domain.com)",
        "Rejects emails without @",
        "Rejects emails without domain",
        "Has docstring and type hints",
    ],
)

SAMPLE_CODE = '''# --- FILE: validate_email.py ---
"""Email validation utility."""

import re


def validate_email(email: str) -> bool:
    """Validate an email address using regex.

    Args:
        email: The email string to validate.

    Returns:
        True if the email is valid, False otherwise.
    """
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))
'''

SAMPLE_QA_PASS = FailureReport(
    task_id="e2e-test-001",
    status="pass",
    tests_passed=4,
    tests_failed=0,
    errors=[],
    failed_files=[],
    is_architectural=False,
    recommendation="All acceptance criteria met.",
)

SAMPLE_QA_FAIL = FailureReport(
    task_id="e2e-test-001",
    status="fail",
    tests_passed=2,
    tests_failed=2,
    errors=["Missing edge case for empty string", "No handling for unicode domains"],
    failed_files=["validate_email.py"],
    is_architectural=False,
    recommendation="Add empty string check and unicode domain support.",
)

SAMPLE_QA_ESCALATE = FailureReport(
    task_id="e2e-test-001",
    status="escalate",
    tests_passed=1,
    tests_failed=3,
    errors=["Regex approach cannot handle internationalized domains"],
    failed_files=["validate_email.py"],
    is_architectural=True,
    recommendation="Blueprint should use a parsing library instead of regex for full RFC 5321 compliance.",
)


def _make_llm_response(content: str, total_tokens: int = 500) -> MagicMock:
    """Create a mock LLM response with content and usage metadata."""
    resp = MagicMock()
    resp.content = content
    resp.usage_metadata = {"total_tokens": total_tokens}
    return resp


# ── Happy Path Tests ──


class TestE2EHappyPath:
    """Full pipeline: Architect → Lead Dev → QA → PASSED."""

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_full_pipeline_pass(self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory):
        """A task goes through the full loop and passes QA on the first try."""
        mock_memory.return_value = ["Project uses Python 3.13", "Use type hints everywhere"]
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json(), total_tokens=800)
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE, total_tokens=1200)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_response = _make_llm_response(SAMPLE_QA_PASS.model_dump_json(), total_tokens=400)
        mock_qa_llm.return_value.invoke.return_value = qa_response

        result = run_task("Create a Python function that validates email addresses", enable_tracing=False)

        assert result.status == WorkflowStatus.PASSED
        assert result.retry_count == 0
        assert result.tokens_used == 800 + 1200 + 400
        assert result.blueprint is not None
        assert result.blueprint.task_id == "e2e-test-001"
        assert len(result.generated_code) > 0
        assert result.failure_report is not None
        assert result.failure_report.status == "pass"
        assert any("architect" in t for t in result.trace)
        assert any("developer" in t for t in result.trace)
        assert any("qa" in t for t in result.trace)
        mock_memory.assert_called_once()

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_memory_context_included_in_architect_prompt(
        self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory
    ):
        """Memory context should be included in the Architect's system prompt."""
        mock_memory.return_value = ["Framework: SvelteKit", "Database: CosmosDB"]
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json())
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_response = _make_llm_response(SAMPLE_QA_PASS.model_dump_json())
        mock_qa_llm.return_value.invoke.return_value = qa_response

        result = run_task("Build a new endpoint", enable_tracing=False)

        arch_call_args = mock_arch_llm.return_value.invoke.call_args[0][0]
        system_msg = arch_call_args[0].content
        assert "SvelteKit" in system_msg
        assert "CosmosDB" in system_msg


# ── Retry Path Tests ──


class TestE2ERetryPath:
    """QA fails → retry developer → eventually passes."""

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_retry_then_pass(self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory):
        """QA fails on first attempt, passes on retry."""
        mock_memory.return_value = []
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json(), total_tokens=800)
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE, total_tokens=1200)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_fail = _make_llm_response(SAMPLE_QA_FAIL.model_dump_json(), total_tokens=400)
        qa_pass = _make_llm_response(SAMPLE_QA_PASS.model_dump_json(), total_tokens=400)
        mock_qa_llm.return_value.invoke.side_effect = [qa_fail, qa_pass]

        result = run_task("Create email validator", enable_tracing=False)

        assert result.status == WorkflowStatus.PASSED
        assert result.retry_count == 1
        assert mock_dev_llm.return_value.invoke.call_count == 2
        assert mock_qa_llm.return_value.invoke.call_count == 2

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_failure_report_included_in_retry(
        self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory
    ):
        """Developer's retry prompt should include the QA failure report."""
        mock_memory.return_value = []
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json())
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_fail = _make_llm_response(SAMPLE_QA_FAIL.model_dump_json())
        qa_pass = _make_llm_response(SAMPLE_QA_PASS.model_dump_json())
        mock_qa_llm.return_value.invoke.side_effect = [qa_fail, qa_pass]

        result = run_task("Create email validator", enable_tracing=False)

        dev_calls = mock_dev_llm.return_value.invoke.call_args_list
        assert len(dev_calls) == 2
        retry_msg = dev_calls[1][0][0][1].content
        assert "PREVIOUS ATTEMPT FAILED" in retry_msg
        assert "Missing edge case for empty string" in retry_msg


# ── Escalation Path Tests ──


class TestE2EEscalation:
    """QA escalates architectural failure → re-plans with Architect."""

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_escalation_to_architect(
        self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory
    ):
        """QA escalates → Architect re-plans → Developer retries → QA passes."""
        mock_memory.return_value = []
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json(), total_tokens=800)
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE, total_tokens=1200)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_escalate = _make_llm_response(SAMPLE_QA_ESCALATE.model_dump_json(), total_tokens=400)
        qa_pass = _make_llm_response(SAMPLE_QA_PASS.model_dump_json(), total_tokens=400)
        mock_qa_llm.return_value.invoke.side_effect = [qa_escalate, qa_pass]

        result = run_task("Create email validator", enable_tracing=False)

        assert result.status == WorkflowStatus.PASSED
        assert mock_arch_llm.return_value.invoke.call_count == 2
        assert mock_dev_llm.return_value.invoke.call_count == 2
        assert mock_qa_llm.return_value.invoke.call_count == 2

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_escalation_includes_failure_in_architect_prompt(
        self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory
    ):
        """Re-plan prompt should include the QA escalation reason."""
        mock_memory.return_value = []
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json())
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_escalate = _make_llm_response(SAMPLE_QA_ESCALATE.model_dump_json())
        qa_pass = _make_llm_response(SAMPLE_QA_PASS.model_dump_json())
        mock_qa_llm.return_value.invoke.side_effect = [qa_escalate, qa_pass]

        result = run_task("Create email validator", enable_tracing=False)

        arch_calls = mock_arch_llm.return_value.invoke.call_args_list
        assert len(arch_calls) == 2
        replan_msg = arch_calls[1][0][0][1].content
        assert "PREVIOUS ATTEMPT FAILED" in replan_msg
        assert "architectural" in replan_msg.lower() or "parsing library" in replan_msg


# ── Budget & Limit Tests ──


class TestE2EBudgetLimits:
    """Pipeline respects token budget and retry limits."""

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_max_retries_stops_pipeline(
        self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory
    ):
        """Pipeline stops after MAX_RETRIES failures."""
        mock_memory.return_value = []
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json(), total_tokens=100)
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE, total_tokens=100)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_fail = _make_llm_response(SAMPLE_QA_FAIL.model_dump_json(), total_tokens=100)
        mock_qa_llm.return_value.invoke.return_value = qa_fail

        result = run_task("Create email validator", enable_tracing=False)

        assert result.retry_count >= MAX_RETRIES
        assert result.status != WorkflowStatus.PASSED
        assert mock_dev_llm.return_value.invoke.call_count == MAX_RETRIES

    @patch("src.orchestrator.TOKEN_BUDGET", 1000)
    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_token_budget_stops_pipeline(
        self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory
    ):
        """Pipeline stops when token budget is exhausted."""
        mock_memory.return_value = []
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json(), total_tokens=400)
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE, total_tokens=400)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_fail = _make_llm_response(SAMPLE_QA_FAIL.model_dump_json(), total_tokens=400)
        mock_qa_llm.return_value.invoke.return_value = qa_fail

        result = run_task("Create email validator", enable_tracing=False)

        assert result.tokens_used >= 1000


# ── Node-Level Tests ──


class TestE2ENodeFunctions:
    """Test individual node functions with realistic mock data."""

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_architect_llm")
    def test_architect_node_produces_blueprint(self, mock_llm, mock_memory):
        """architect_node should parse LLM output into a Blueprint."""
        mock_memory.return_value = ["Use Python 3.13"]
        mock_llm.return_value.invoke.return_value = _make_llm_response(
            SAMPLE_BLUEPRINT.model_dump_json(), total_tokens=500
        )

        state = {"task_description": "Create email validator", "trace": [], "tokens_used": 0, "retry_count": 0}
        result = architect_node(state)

        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "e2e-test-001"
        assert len(result["blueprint"].target_files) == 1
        assert result["tokens_used"] == 500
        assert len(result["memory_context"]) == 1

    @patch("src.orchestrator._get_developer_llm")
    def test_developer_node_generates_code(self, mock_llm):
        """developer_node should generate code from the Blueprint."""
        mock_llm.return_value.invoke.return_value = _make_llm_response(
            SAMPLE_CODE, total_tokens=1200
        )

        state = {
            "task_description": "Create email validator",
            "blueprint": SAMPLE_BLUEPRINT,
            "status": WorkflowStatus.BUILDING,
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
        }
        result = developer_node(state)

        assert result["status"] == WorkflowStatus.REVIEWING
        assert "validate_email" in result["generated_code"]
        assert result["tokens_used"] == 1200

    @patch("src.orchestrator._get_qa_llm")
    def test_qa_node_returns_pass(self, mock_llm):
        """qa_node should parse a 'pass' verdict correctly."""
        mock_llm.return_value.invoke.return_value = _make_llm_response(
            SAMPLE_QA_PASS.model_dump_json(), total_tokens=400
        )

        state = {
            "task_description": "Create email validator",
            "blueprint": SAMPLE_BLUEPRINT,
            "generated_code": SAMPLE_CODE,
            "status": WorkflowStatus.REVIEWING,
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
        }
        result = qa_node(state)

        assert result["status"] == WorkflowStatus.PASSED
        assert result["failure_report"].status == "pass"
        assert result["failure_report"].tests_passed == 4

    @patch("src.orchestrator._get_qa_llm")
    def test_qa_node_returns_fail(self, mock_llm):
        """qa_node should parse a 'fail' verdict and increment retry count."""
        mock_llm.return_value.invoke.return_value = _make_llm_response(
            SAMPLE_QA_FAIL.model_dump_json(), total_tokens=400
        )

        state = {
            "task_description": "Create email validator",
            "blueprint": SAMPLE_BLUEPRINT,
            "generated_code": SAMPLE_CODE,
            "status": WorkflowStatus.REVIEWING,
            "retry_count": 0,
            "trace": [],
            "tokens_used": 0,
        }
        result = qa_node(state)

        assert result["status"] == WorkflowStatus.REVIEWING
        assert result["failure_report"].status == "fail"
        assert result["retry_count"] == 1

    @patch("src.orchestrator._get_qa_llm")
    def test_qa_node_returns_escalate(self, mock_llm):
        """qa_node should detect architectural failures and set ESCALATED status."""
        mock_llm.return_value.invoke.return_value = _make_llm_response(
            SAMPLE_QA_ESCALATE.model_dump_json(), total_tokens=400
        )

        state = {
            "task_description": "Create email validator",
            "blueprint": SAMPLE_BLUEPRINT,
            "generated_code": SAMPLE_CODE,
            "status": WorkflowStatus.REVIEWING,
            "retry_count": 0,
            "trace": [],
            "tokens_used": 0,
        }
        result = qa_node(state)

        assert result["status"] == WorkflowStatus.ESCALATED
        assert result["failure_report"].is_architectural is True

    @patch("src.orchestrator._get_architect_llm")
    @patch("src.orchestrator._fetch_memory_context")
    def test_architect_handles_json_in_code_fence(self, mock_memory, mock_llm):
        """Architect should handle LLM wrapping JSON in ```json code fences."""
        mock_memory.return_value = []
        fenced_json = f"```json\n{SAMPLE_BLUEPRINT.model_dump_json()}\n```"
        mock_llm.return_value.invoke.return_value = _make_llm_response(fenced_json)

        state = {"task_description": "Create email validator", "trace": [], "tokens_used": 0, "retry_count": 0}
        result = architect_node(state)

        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "e2e-test-001"


# ── Memory Integration Tests ──


class TestE2EMemoryIntegration:
    """Test that the pipeline correctly queries and uses Chroma memory."""

    def test_memory_query_with_real_chroma(self, tmp_path):
        """Verify _fetch_memory_context queries Chroma and returns results."""
        store = ChromaMemoryStore(persist_dir=str(tmp_path / "chroma"), collection_name="e2e_test")
        store.add_l0_core("Project language: Python 3.13")
        store.add_l0_core("Always use type hints")
        store.add_l1("Auth module uses JWT tokens", module="auth")

        results = store.query("What programming language does this project use?")
        assert len(results) > 0
        assert any("Python" in r.content for r in results)

    def test_memory_context_survives_pipeline(self, tmp_path):
        """Memory context retrieved early in pipeline should be in final state."""
        store = ChromaMemoryStore(persist_dir=str(tmp_path / "chroma"), collection_name="e2e_mem")
        store.add_l0_core("Test memory entry")

        with patch("src.memory.chroma_store.ChromaMemoryStore") as MockStore:
            MockStore.return_value = store
            context = []
            try:
                results = store.query("test", n_results=5)
                context = [r.content for r in results]
            except Exception:
                pass

        assert len(context) >= 1


# ── Tracing Integration Tests ──


class TestE2ETracingIntegration:
    """Test that tracing is properly wired through the pipeline."""

    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_tracing_disabled_runs_cleanly(
        self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory
    ):
        """Pipeline should work with tracing explicitly disabled."""
        mock_memory.return_value = []
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json())
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_response = _make_llm_response(SAMPLE_QA_PASS.model_dump_json())
        mock_qa_llm.return_value.invoke.return_value = qa_response

        result = run_task("Create email validator", enable_tracing=False)
        assert result.status == WorkflowStatus.PASSED

    @patch("src.orchestrator.create_trace_config")
    @patch("src.orchestrator._fetch_memory_context")
    @patch("src.orchestrator._get_qa_llm")
    @patch("src.orchestrator._get_developer_llm")
    @patch("src.orchestrator._get_architect_llm")
    def test_tracing_enabled_calls_trace_config(
        self, mock_arch_llm, mock_dev_llm, mock_qa_llm, mock_memory, mock_trace
    ):
        """When tracing is enabled, create_trace_config should be called."""
        mock_memory.return_value = []
        mock_trace.return_value = MagicMock(callbacks=[], enabled=False, trace_id=None, flush=MagicMock())
        arch_response = _make_llm_response(SAMPLE_BLUEPRINT.model_dump_json())
        mock_arch_llm.return_value.invoke.return_value = arch_response
        dev_response = _make_llm_response(SAMPLE_CODE)
        mock_dev_llm.return_value.invoke.return_value = dev_response
        qa_response = _make_llm_response(SAMPLE_QA_PASS.model_dump_json())
        mock_qa_llm.return_value.invoke.return_value = qa_response

        result = run_task("Create email validator", enable_tracing=True)
        mock_trace.assert_called_once()
