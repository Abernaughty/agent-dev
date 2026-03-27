"""Tests for the TaskRunner — async orchestrator bridge.

Issue #48: StateManager <-> Orchestrator bridge

Covers:
- TaskRunner lifecycle (submit, cancel, shutdown)
- Node completion handling (architect, developer, QA)
- SSE event emission per node
- Error handling (exception, cancellation)
- Blueprint and budget propagation
- Duplicate submit rejection
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.architect import Blueprint
from src.agents.qa import FailureReport
from src.api.events import EventBus, EventType, SSEEvent, event_bus
from src.api.models import (
    AgentStatus,
    TaskBudget,
    TaskDetail,
    TaskStatus,
    TimelineEvent,
)
from src.api.runner import (
    COST_PER_TOKEN,
    NODE_TO_AGENT,
    WORKFLOW_TO_TASK_STATUS,
    TaskRunner,
    _blueprint_to_response,
    _now_str,
)
from src.api.state import StateManager
from src.orchestrator import WorkflowStatus


# ── Fixtures ──


@pytest.fixture(autouse=True)
async def _reset_event_bus():
    """Clear subscribers between tests."""
    await event_bus.clear()
    yield
    await event_bus.clear()


@pytest.fixture
def runner():
    """Fresh TaskRunner for each test."""
    return TaskRunner()


@pytest.fixture
def fresh_state():
    """Fresh StateManager (empty state)."""
    return StateManager()


SAMPLE_BLUEPRINT = Blueprint(
    task_id="test-task-001",
    target_files=["src/auth.py", "tests/test_auth.py"],
    instructions="Implement authentication middleware",
    constraints=["Use JWT tokens", "No plaintext passwords"],
    acceptance_criteria=["All auth tests pass", "401 on invalid token"],
)

SAMPLE_QA_PASS = FailureReport(
    task_id="test-task-001",
    status="pass",
    tests_passed=8,
    tests_failed=0,
    errors=[],
    failed_files=[],
    is_architectural=False,
    recommendation="All tests passing. Ready for merge.",
)

SAMPLE_QA_FAIL = FailureReport(
    task_id="test-task-001",
    status="fail",
    tests_passed=6,
    tests_failed=2,
    errors=["TypeError in auth.py line 42", "AssertionError in test_auth.py"],
    failed_files=["src/auth.py"],
    is_architectural=False,
    recommendation="Fix type error in auth middleware.",
)

SAMPLE_QA_ESCALATE = FailureReport(
    task_id="test-task-001",
    status="fail",
    tests_passed=0,
    tests_failed=8,
    errors=["Wrong file targeted — auth should be in middleware/"],
    failed_files=["src/auth.py"],
    is_architectural=True,
    recommendation="Re-plan with correct file structure.",
)


# ── Helper Utilities ──


class TestHelperUtilities:
    """Tests for module-level helper functions."""

    def test_now_str_returns_hhmm_format(self):
        result = _now_str()
        assert len(result) == 5
        assert result[2] == ":"
        int(result[:2])  # hours
        int(result[3:])  # minutes

    def test_blueprint_to_response_converts_fields(self):
        resp = _blueprint_to_response(SAMPLE_BLUEPRINT)
        assert resp.task_id == "test-task-001"
        assert resp.target_files == ["src/auth.py", "tests/test_auth.py"]
        assert len(resp.constraints) == 2
        assert len(resp.acceptance_criteria) == 2

    def test_node_to_agent_mapping(self):
        assert "architect" in NODE_TO_AGENT
        assert "developer" in NODE_TO_AGENT
        assert "qa" in NODE_TO_AGENT
        assert NODE_TO_AGENT["architect"] == ("arch", AgentStatus.PLANNING)
        assert NODE_TO_AGENT["developer"] == ("dev", AgentStatus.CODING)
        assert NODE_TO_AGENT["qa"] == ("qa", AgentStatus.REVIEWING)

    def test_workflow_to_task_status_mapping(self):
        assert WORKFLOW_TO_TASK_STATUS[WorkflowStatus.PLANNING] == TaskStatus.PLANNING
        assert WORKFLOW_TO_TASK_STATUS[WorkflowStatus.PASSED] == TaskStatus.PASSED
        assert WORKFLOW_TO_TASK_STATUS[WorkflowStatus.FAILED] == TaskStatus.FAILED
        assert WORKFLOW_TO_TASK_STATUS[WorkflowStatus.ESCALATED] == TaskStatus.ESCALATED

    def test_cost_per_token_is_reasonable(self):
        assert 0 < COST_PER_TOKEN < 0.001


# ── TaskRunner Lifecycle ──


class TestTaskRunnerLifecycle:
    """Tests for submit, cancel, and shutdown."""

    async def test_submit_creates_background_task(self, runner):
        """Submit should create an asyncio.Task tracked in _tasks."""
        with patch.object(runner, "_run_task", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None
            runner.submit("task-1", "Build auth")
            assert "task-1" in runner._tasks
            assert runner.running_count == 1
            # Let the task complete
            await asyncio.sleep(0.05)

    async def test_submit_duplicate_is_rejected(self, runner):
        """Submitting the same task_id twice should be ignored."""
        with patch.object(runner, "_run_task", new_callable=AsyncMock) as mock_run:
            # Make _run_task hang so the first submit stays active
            mock_run.side_effect = lambda *a: asyncio.sleep(10)
            runner.submit("task-dup", "Build auth")
            runner.submit("task-dup", "Build auth again")
            assert runner.running_count == 1
            # Cleanup
            await runner.shutdown()

    async def test_cancel_running_task(self, runner):
        """Cancel should cancel the asyncio.Task."""
        with patch.object(runner, "_run_task", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = lambda *a: asyncio.sleep(10)
            runner.submit("task-cancel", "Build auth")
            result = await runner.cancel("task-cancel")
            assert result is True
            await asyncio.sleep(0.05)

    async def test_cancel_nonexistent_returns_false(self, runner):
        result = await runner.cancel("nonexistent")
        assert result is False

    async def test_shutdown_cancels_all_tasks(self, runner):
        """Shutdown should cancel all running tasks and clear the dict."""
        with patch.object(runner, "_run_task", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = lambda *a: asyncio.sleep(10)
            runner.submit("task-a", "Task A")
            runner.submit("task-b", "Task B")
            assert runner.running_count == 2
            await runner.shutdown()
            assert runner.running_count == 0

    async def test_running_count_reflects_active_tasks(self, runner):
        assert runner.running_count == 0
        with patch.object(runner, "_run_task", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = lambda *a: asyncio.sleep(10)
            runner.submit("task-x", "Task X")
            assert runner.running_count == 1
            await runner.shutdown()
            assert runner.running_count == 0

    async def test_completed_task_auto_removes_from_dict(self, runner):
        """When _run_task completes, the done callback should remove the task."""
        with patch.object(runner, "_run_task", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None
            runner.submit("task-done", "Quick task")
            await asyncio.sleep(0.05)
            assert "task-done" not in runner._tasks
            assert runner.running_count == 0


# ── Node Completion Handling ──


class TestNodeCompletionHandling:
    """Tests for _handle_node_completion and per-node handlers."""

    def _make_task(self, task_id="test-task") -> TaskDetail:
        return TaskDetail(
            id=task_id,
            description="Test task",
            status=TaskStatus.PLANNING,
            created_at=datetime.now(timezone.utc),
            budget=TaskBudget(),
        )

    async def test_handle_architect_success(self, runner):
        """Architect node with a valid Blueprint should update task."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "blueprint": SAMPLE_BLUEPRINT,
            "status": WorkflowStatus.BUILDING,
            "tokens_used": 1200,
            "retry_count": 0,
        }

        await runner._handle_node_completion("test-task", "architect", output, sm, None)

        assert task.blueprint is not None
        assert task.blueprint.task_id == "test-task-001"
        assert task.budget.tokens_used == 1200
        assert len(task.timeline) == 1
        assert task.timeline[0].type == "plan"

    async def test_handle_architect_failure(self, runner):
        """Architect node without a Blueprint should log a failure."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "blueprint": None,
            "error_message": "Model returned unparseable JSON",
            "status": WorkflowStatus.FAILED,
            "tokens_used": 500,
            "retry_count": 0,
        }

        await runner._handle_node_completion("test-task", "architect", output, sm, None)

        assert task.blueprint is None
        assert len(task.timeline) == 1
        assert task.timeline[0].type == "fail"
        assert "failed" in task.timeline[0].action.lower()

    async def test_handle_developer_success(self, runner):
        """Developer node with generated code should update task."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "generated_code": "def auth_middleware(request): ...",
            "status": WorkflowStatus.REVIEWING,
            "tokens_used": 3500,
            "retry_count": 0,
        }

        await runner._handle_node_completion("test-task", "developer", output, sm, None)

        assert task.generated_code == "def auth_middleware(request): ..."
        assert task.budget.tokens_used == 3500
        assert len(task.timeline) == 1
        assert task.timeline[0].type == "code"

    async def test_handle_developer_retry(self, runner):
        """Developer on retry should produce a 'retry' timeline event."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        task.budget.retries_used = 1
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "generated_code": "def auth_middleware_v2(request): ...",
            "status": WorkflowStatus.REVIEWING,
            "tokens_used": 6000,
            "retry_count": 1,
        }

        await runner._handle_node_completion("test-task", "developer", output, sm, None)

        assert len(task.timeline) == 1
        assert task.timeline[0].type == "retry"
        assert "Retry" in task.timeline[0].action

    async def test_handle_qa_pass(self, runner):
        """QA pass should produce a 'success' timeline event."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "failure_report": SAMPLE_QA_PASS,
            "status": WorkflowStatus.PASSED,
            "tokens_used": 5000,
            "retry_count": 0,
        }

        await runner._handle_node_completion("test-task", "qa", output, sm, None)

        assert task.status == TaskStatus.PASSED
        assert len(task.timeline) == 1
        assert task.timeline[0].type == "success"
        assert task.completed_at is not None

    async def test_handle_qa_fail(self, runner):
        """QA fail should produce a 'fail' timeline event with errors."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "failure_report": SAMPLE_QA_FAIL,
            "status": WorkflowStatus.REVIEWING,
            "tokens_used": 5000,
            "retry_count": 1,
        }

        await runner._handle_node_completion("test-task", "qa", output, sm, None)

        assert len(task.timeline) == 1
        assert task.timeline[0].type == "fail"
        assert "2 test" in task.timeline[0].action

    async def test_handle_qa_escalate(self, runner):
        """QA architectural escalation should produce fail + escalation message."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "failure_report": SAMPLE_QA_ESCALATE,
            "status": WorkflowStatus.ESCALATED,
            "tokens_used": 5000,
            "retry_count": 0,
        }

        await runner._handle_node_completion("test-task", "qa", output, sm, None)

        assert task.status == TaskStatus.ESCALATED
        assert len(task.timeline) == 1
        assert task.timeline[0].type == "fail"
        assert "escalat" in task.timeline[0].action.lower()

    async def test_budget_propagation(self, runner):
        """Token and cost budget should update from node output."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "blueprint": SAMPLE_BLUEPRINT,
            "status": WorkflowStatus.BUILDING,
            "tokens_used": 12500,
            "retry_count": 0,
        }

        await runner._handle_node_completion("test-task", "architect", output, sm, None)

        assert task.budget.tokens_used == 12500
        expected_cost = round(12500 * COST_PER_TOKEN, 4)
        assert task.budget.cost_used == expected_cost

    async def test_previous_agent_set_idle(self, runner):
        """When a new node completes, the previous agent should go idle."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "generated_code": "code here",
            "status": WorkflowStatus.REVIEWING,
            "tokens_used": 3000,
            "retry_count": 0,
        }

        # Simulate architect was the previous node
        await runner._handle_node_completion("test-task", "developer", output, sm, "architect")

        # Check that architect was set idle
        idle_calls = [
            c for c in sm.update_agent_status.call_args_list
            if c.args == ("arch", AgentStatus.IDLE)
        ]
        assert len(idle_calls) >= 1

    async def test_unknown_node_skipped(self, runner):
        """Nodes not in NODE_TO_AGENT should be silently skipped."""
        sm = MagicMock(spec=StateManager)
        task = self._make_task()
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        # __start__ or __end__ nodes should not cause errors
        await runner._handle_node_completion("test-task", "__start__", {}, sm, None)
        assert len(task.timeline) == 0

    async def test_missing_task_is_safe(self, runner):
        """If state_manager returns None for the task, no crash."""
        sm = MagicMock(spec=StateManager)
        sm.get_task.return_value = None
        sm.update_agent_status = AsyncMock()

        # Should not raise
        await runner._handle_node_completion(
            "gone-task", "architect",
            {"blueprint": SAMPLE_BLUEPRINT, "status": WorkflowStatus.BUILDING},
            sm, None,
        )


# ── SSE Event Emission ──


class TestSSEEventEmission:
    """Tests for SSE events emitted during task execution."""

    async def test_architect_emits_task_progress(self, runner):
        """Architect completion should emit a TASK_PROGRESS event."""
        queue = await event_bus.subscribe()
        sm = MagicMock(spec=StateManager)
        task = TaskDetail(
            id="sse-test",
            description="Test",
            status=TaskStatus.PLANNING,
            created_at=datetime.now(timezone.utc),
            budget=TaskBudget(),
        )
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "blueprint": SAMPLE_BLUEPRINT,
            "status": WorkflowStatus.BUILDING,
            "tokens_used": 1000,
            "retry_count": 0,
        }
        await runner._handle_node_completion("sse-test", "architect", output, sm, None)

        # Drain events — expect at least one TASK_PROGRESS
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        progress_events = [e for e in events if e.type == EventType.TASK_PROGRESS]
        assert len(progress_events) >= 1
        assert progress_events[0].data["task_id"] == "sse-test"
        assert progress_events[0].data["agent"] == "arch"

        await event_bus.unsubscribe(queue)

    async def test_qa_pass_emits_log_line(self, runner):
        """QA pass should emit LOG_LINE events."""
        queue = await event_bus.subscribe()
        sm = MagicMock(spec=StateManager)
        task = TaskDetail(
            id="sse-qa",
            description="Test",
            status=TaskStatus.REVIEWING,
            created_at=datetime.now(timezone.utc),
            budget=TaskBudget(),
        )
        sm.get_task.return_value = task
        sm.update_agent_status = AsyncMock()

        output = {
            "failure_report": SAMPLE_QA_PASS,
            "status": WorkflowStatus.PASSED,
            "tokens_used": 5000,
            "retry_count": 0,
        }
        await runner._handle_node_completion("sse-qa", "qa", output, sm, None)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        log_events = [e for e in events if e.type == EventType.LOG_LINE]
        assert len(log_events) >= 1
        assert any("passing" in e.data.get("message", "").lower() for e in log_events)

        await event_bus.unsubscribe(queue)


# ── Full Run Integration (mocked orchestrator) ──


class TestFullRunIntegration:
    """Tests for _run_task with a mocked LangGraph workflow."""

    async def test_successful_run_emits_complete(self, runner):
        """A full successful run should emit TASK_COMPLETE at the end."""
        queue = await event_bus.subscribe()

        # Create a real state manager task
        from src.api import state as state_mod
        sm = StateManager()
        state_mod.state_manager = sm
        task_id = await sm.create_task("Build auth middleware")

        # Drain the create event
        await asyncio.sleep(0.01)
        while not queue.empty():
            queue.get_nowait()

        # Mock the orchestrator graph
        async def fake_astream(initial_state, config=None):
            yield {"architect": {"blueprint": SAMPLE_BLUEPRINT, "status": WorkflowStatus.BUILDING, "tokens_used": 1200, "retry_count": 0}}
            yield {"developer": {"generated_code": "def auth(): pass", "status": WorkflowStatus.REVIEWING, "tokens_used": 3500, "retry_count": 0}}
            yield {"qa": {"failure_report": SAMPLE_QA_PASS, "status": WorkflowStatus.PASSED, "tokens_used": 5000, "retry_count": 0}}

        mock_graph = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.astream = fake_astream
        mock_graph.compile.return_value = mock_workflow

        with patch("src.api.runner.build_graph", return_value=mock_graph):
            await runner._run_task(task_id, "Build auth middleware")

        # Collect all events
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        # Should have TASK_COMPLETE
        complete_events = [e for e in events if e.type == EventType.TASK_COMPLETE]
        assert len(complete_events) >= 1
        assert complete_events[0].data["task_id"] == task_id
        assert complete_events[0].data["status"] == "passed"

        # Task should be PASSED in state manager
        task = sm.get_task(task_id)
        assert task.status == TaskStatus.PASSED
        assert task.budget.tokens_used == 5000

        await event_bus.unsubscribe(queue)

    async def test_exception_sets_task_failed(self, runner):
        """If the orchestrator throws, the task should be marked FAILED."""
        from src.api import state as state_mod
        sm = StateManager()
        state_mod.state_manager = sm
        task_id = await sm.create_task("Failing task")

        mock_graph = MagicMock()
        mock_workflow = MagicMock()

        async def exploding_astream(initial_state, config=None):
            raise RuntimeError("LLM provider timeout")
            yield  # make it an async generator

        mock_workflow.astream = exploding_astream
        mock_graph.compile.return_value = mock_workflow

        with patch("src.api.runner.build_graph", return_value=mock_graph):
            await runner._run_task(task_id, "Failing task")

        task = sm.get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert "timeout" in task.error_message.lower()

    async def test_cancellation_sets_task_cancelled(self, runner):
        """If the task is cancelled, status should be CANCELLED."""
        from src.api import state as state_mod
        sm = StateManager()
        state_mod.state_manager = sm
        task_id = await sm.create_task("Cancellable task")

        mock_graph = MagicMock()
        mock_workflow = MagicMock()

        async def slow_astream(initial_state, config=None):
            await asyncio.sleep(10)
            yield {}

        mock_workflow.astream = slow_astream
        mock_graph.compile.return_value = mock_workflow

        with patch("src.api.runner.build_graph", return_value=mock_graph):
            runner.submit(task_id, "Cancellable task")
            await asyncio.sleep(0.05)
            await runner.cancel(task_id)
            await asyncio.sleep(0.1)

        task = sm.get_task(task_id)
        assert task.status == TaskStatus.CANCELLED
