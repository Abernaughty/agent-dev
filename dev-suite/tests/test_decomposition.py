"""Tests for multi-file task decomposition (Issue #58).

Unit tests for the decomposition threshold logic, decompose_task_node,
advance_subtask_node, route_next_subtask, and architect sub-task scoping.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.architect import Blueprint, SubTask, TaskDecomposition
from src.orchestrator import (
    DECOMPOSE_FILE_THRESHOLD,
    GraphState,
    _needs_decomposition,
    advance_subtask_node,
    build_graph,
    create_workflow,
    decompose_task_node,
    route_next_subtask,
)

# -- Model Tests --


class TestSubTaskModel:
    def test_subtask_creation(self):
        st = SubTask(
            sub_task_id="task-1-1",
            parent_task_id="task-1",
            sequence=0,
            depends_on=[],
            target_files=["src/models.py"],
            instructions="Create data models",
            description="Add data models",
        )
        assert st.sub_task_id == "task-1-1"
        assert st.sequence == 0
        assert st.depends_on == []

    def test_subtask_with_dependencies(self):
        st = SubTask(
            sub_task_id="task-1-2",
            parent_task_id="task-1",
            sequence=1,
            depends_on=["task-1-1"],
            target_files=["src/api.py"],
            instructions="Create API endpoints using models from sub-task 1",
            description="Add API endpoints",
        )
        assert st.depends_on == ["task-1-1"]


class TestTaskDecompositionModel:
    def test_decomposition_creation(self):
        decomp = TaskDecomposition(
            parent_task_id="task-1",
            sub_tasks=[
                SubTask(
                    sub_task_id="task-1-1",
                    parent_task_id="task-1",
                    sequence=0,
                    target_files=["src/models.py", "src/schema.py"],
                    instructions="Create data models",
                    description="Add data models",
                ),
                SubTask(
                    sub_task_id="task-1-2",
                    parent_task_id="task-1",
                    sequence=1,
                    depends_on=["task-1-1"],
                    target_files=["src/api.py", "src/routes.py"],
                    instructions="Create API routes",
                    description="Add API routes",
                ),
            ],
            rationale="Separate models from API layer for independent testing",
        )
        assert len(decomp.sub_tasks) == 2
        assert decomp.rationale != ""

    def test_decomposition_json_roundtrip(self):
        decomp = TaskDecomposition(
            parent_task_id="task-1",
            sub_tasks=[
                SubTask(
                    sub_task_id="task-1-1",
                    parent_task_id="task-1",
                    sequence=0,
                    target_files=["a.py"],
                    instructions="Do thing",
                    description="Thing",
                ),
            ],
            rationale="Single sub-task",
        )
        data = json.loads(decomp.model_dump_json())
        roundtripped = TaskDecomposition(**data)
        assert roundtripped.sub_tasks[0].sub_task_id == "task-1-1"


# -- Threshold Detection Tests --


class TestNeedsDecomposition:
    def test_none_context_returns_false(self):
        assert _needs_decomposition("some task", None) is False

    def test_empty_context_returns_false(self):
        assert _needs_decomposition("some task", []) is False

    def test_below_file_threshold(self):
        context = [{"path": f"src/file{i}.py"} for i in range(DECOMPOSE_FILE_THRESHOLD - 1)]
        assert _needs_decomposition("some task", context) is False

    def test_at_file_threshold(self):
        context = [{"path": f"src/file{i}.py"} for i in range(DECOMPOSE_FILE_THRESHOLD)]
        assert _needs_decomposition("some task", context) is True

    def test_above_file_threshold(self):
        context = [{"path": f"src/file{i}.py"} for i in range(DECOMPOSE_FILE_THRESHOLD + 2)]
        assert _needs_decomposition("some task", context) is True

    def test_below_dir_threshold_single_dir(self):
        context = [
            {"path": "src/a.py"},
            {"path": "src/b.py"},
        ]
        assert _needs_decomposition("some task", context) is False

    def test_at_dir_threshold(self):
        context = [
            {"path": "src/a.py"},
            {"path": "tests/test_a.py"},
        ]
        assert _needs_decomposition("some task", context) is True

    def test_files_without_dirs_not_counted(self):
        """Top-level files (no slash) don't count toward directory threshold."""
        context = [
            {"path": "setup.py"},
            {"path": "README.md"},
            {"path": "main.py"},
        ]
        assert _needs_decomposition("some task", context) is False

    def test_mixed_dirs_and_files(self):
        context = [
            {"path": "src/models.py"},
            {"path": "tests/test_models.py"},
            {"path": "setup.py"},
        ]
        # 2 top-level dirs (src, tests) >= threshold
        assert _needs_decomposition("some task", context) is True


# -- Decompose Task Node Tests --


SAMPLE_DECOMPOSITION = TaskDecomposition(
    parent_task_id="decomp-1",
    sub_tasks=[
        SubTask(
            sub_task_id="decomp-1-1",
            parent_task_id="decomp-1",
            sequence=0,
            target_files=["src/models/user.py", "src/models/post.py"],
            instructions="Create User and Post data models",
            description="Add data models",
        ),
        SubTask(
            sub_task_id="decomp-1-2",
            parent_task_id="decomp-1",
            sequence=1,
            depends_on=["decomp-1-1"],
            target_files=["src/api/users.py", "src/api/posts.py"],
            instructions="Create REST endpoints for users and posts",
            description="Add API endpoints",
        ),
        SubTask(
            sub_task_id="decomp-1-3",
            parent_task_id="decomp-1",
            sequence=2,
            depends_on=["decomp-1-1", "decomp-1-2"],
            target_files=["tests/test_users.py", "tests/test_posts.py"],
            instructions="Write integration tests",
            description="Add integration tests",
        ),
    ],
    rationale="Separate models, API, and tests for independent implementation",
)


def _make_llm_response(content: str, total_tokens: int = 500) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.usage_metadata = {"total_tokens": total_tokens}
    return resp


class TestDecomposeTaskNode:
    @pytest.fixture
    def small_state(self) -> GraphState:
        return {
            "task_description": "Add a greet function",
            "gathered_context": [
                {"path": "src/utils.py", "content": "# utils"},
                {"path": "src/main.py", "content": "# main"},
            ],
            "tokens_used": 0,
            "trace": [],
        }

    @pytest.fixture
    def large_state(self) -> GraphState:
        return {
            "task_description": "Implement full user management system",
            "gathered_context": [
                {"path": f"src/models/file{i}.py", "content": f"# file{i}"} for i in range(6)
            ],
            "tokens_used": 0,
            "trace": [],
        }

    async def test_skips_small_task(self, small_state):
        result = await decompose_task_node(small_state)
        assert result["decomposition"] is None
        assert result["current_subtask_index"] == 0
        assert result["completed_subtasks"] == []
        assert any("below threshold" in t for t in result["trace"])

    @patch("src.orchestrator._get_architect_llm")
    async def test_decomposes_large_task(self, mock_get_llm, large_state):
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm
        mock_llm.ainvoke = AsyncMock(
            return_value=_make_llm_response(SAMPLE_DECOMPOSITION.model_dump_json())
        )

        result = await decompose_task_node(large_state)
        assert result["decomposition"] is not None
        assert len(result["decomposition"].sub_tasks) == 3
        assert result["current_subtask_index"] == 0
        assert result["completed_subtasks"] == []

    @patch("src.orchestrator._get_architect_llm")
    async def test_fallback_on_llm_failure(self, mock_get_llm, large_state):
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm
        mock_llm.ainvoke = AsyncMock(
            return_value=_make_llm_response("not valid json at all")
        )

        result = await decompose_task_node(large_state)
        assert result["decomposition"] is None
        assert any("falling back" in t for t in result["trace"])

    @patch("src.orchestrator._get_architect_llm")
    async def test_fallback_on_overlapping_files(self, mock_get_llm, large_state):
        """Sub-tasks with overlapping target_files fall back to single blueprint."""
        overlapping = TaskDecomposition(
            parent_task_id="overlap-1",
            sub_tasks=[
                SubTask(
                    sub_task_id="o-1",
                    parent_task_id="overlap-1",
                    sequence=0,
                    target_files=["src/shared.py", "src/a.py"],
                    instructions="Do A",
                    description="Task A",
                ),
                SubTask(
                    sub_task_id="o-2",
                    parent_task_id="overlap-1",
                    sequence=1,
                    target_files=["src/shared.py", "src/b.py"],  # overlaps with o-1
                    instructions="Do B",
                    description="Task B",
                ),
            ],
            rationale="Test overlap",
        )
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm
        mock_llm.ainvoke = AsyncMock(
            return_value=_make_llm_response(overlapping.model_dump_json())
        )

        result = await decompose_task_node(large_state)
        assert result["decomposition"] is None
        assert any("overlapping" in t for t in result["trace"])

    @patch("src.orchestrator._get_architect_llm")
    async def test_tokens_tracked(self, mock_get_llm, large_state):
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm
        mock_llm.ainvoke = AsyncMock(
            return_value=_make_llm_response(SAMPLE_DECOMPOSITION.model_dump_json(), total_tokens=750)
        )
        large_state["tokens_used"] = 100

        result = await decompose_task_node(large_state)
        assert result["tokens_used"] == 850  # 100 + 750


# -- Advance Subtask Node Tests --


class TestAdvanceSubtaskNode:
    @pytest.fixture
    def decomposed_state(self) -> GraphState:
        bp = Blueprint(
            task_id="decomp-1-1",
            target_files=["src/models/user.py"],
            instructions="Create User model",
            constraints=["Use Pydantic"],
            acceptance_criteria=["Model validates correctly"],
        )
        return {
            "decomposition": SAMPLE_DECOMPOSITION,
            "current_subtask_index": 0,
            "completed_subtasks": [],
            "blueprint": bp,
            "generated_code": "class User: pass",
            "failure_report": None,
            "retry_count": 1,
            "parsed_files": [{"path": "src/models/user.py", "content": "class User: pass"}],
            "sandbox_result": None,
            "tool_calls_log": [{"tool": "write"}],
            "trace": [],
            "tokens_used": 1000,
            "memory_writes": [{"content": "User model", "tier": "l1"}],
        }

    async def test_snapshots_completed_subtask(self, decomposed_state):
        result = await advance_subtask_node(decomposed_state)
        assert len(result["completed_subtasks"]) == 1
        assert result["completed_subtasks"][0]["sub_task_id"] == "decomp-1-1"
        assert result["completed_subtasks"][0]["status"] == "passed"

    async def test_increments_index(self, decomposed_state):
        result = await advance_subtask_node(decomposed_state)
        assert result["current_subtask_index"] == 1

    async def test_resets_per_subtask_state(self, decomposed_state):
        result = await advance_subtask_node(decomposed_state)
        assert result["blueprint"] is None
        assert result["generated_code"] == ""
        assert result["failure_report"] is None
        assert result["retry_count"] == 0
        assert result["parsed_files"] == []
        assert result["sandbox_result"] is None
        assert result["tool_calls_log"] == []

    async def test_passthrough_without_decomposition(self):
        state: GraphState = {
            "decomposition": None,
            "trace": [],
        }
        result = await advance_subtask_node(state)
        assert "pass-through" in result["trace"][-1]

    async def test_accumulates_completed_subtasks(self):
        """Second sub-task completion appends to existing list."""
        bp = Blueprint(
            task_id="decomp-1-2",
            target_files=["src/api/users.py"],
            instructions="Create API",
            constraints=[],
            acceptance_criteria=["API works"],
        )
        state: GraphState = {
            "decomposition": SAMPLE_DECOMPOSITION,
            "current_subtask_index": 1,
            "completed_subtasks": [
                {"sub_task_id": "decomp-1-1", "status": "passed", "blueprint": None, "description": "Add data models"},
            ],
            "blueprint": bp,
            "trace": [],
        }
        result = await advance_subtask_node(state)
        assert len(result["completed_subtasks"]) == 2
        assert result["completed_subtasks"][1]["sub_task_id"] == "decomp-1-2"
        assert result["current_subtask_index"] == 2


# -- Route Next Subtask Tests --


class TestRouteNextSubtask:
    def test_no_decomposition_goes_to_flush(self):
        state: GraphState = {"decomposition": None, "current_subtask_index": 0}
        assert route_next_subtask(state) == "flush_memory"

    def test_more_subtasks_goes_to_architect(self):
        state: GraphState = {
            "decomposition": SAMPLE_DECOMPOSITION,
            "current_subtask_index": 1,  # sub-task 2 of 3
        }
        assert route_next_subtask(state) == "architect"

    def test_all_subtasks_done_goes_to_flush(self):
        state: GraphState = {
            "decomposition": SAMPLE_DECOMPOSITION,
            "current_subtask_index": 3,  # all 3 done
        }
        assert route_next_subtask(state) == "flush_memory"

    def test_last_subtask_goes_to_flush(self):
        state: GraphState = {
            "decomposition": SAMPLE_DECOMPOSITION,
            "current_subtask_index": len(SAMPLE_DECOMPOSITION.sub_tasks),
        }
        assert route_next_subtask(state) == "flush_memory"


# -- Graph Structure Tests --


class TestDecompositionGraphStructure:
    def test_graph_has_decomposition_nodes(self):
        graph = build_graph()
        compiled = graph.compile()
        node_names = set(compiled.get_graph().nodes.keys())
        assert "decompose_task" in node_names
        assert "advance_subtask" in node_names

    def test_graph_compiles_with_new_nodes(self):
        workflow = create_workflow()
        assert workflow is not None
