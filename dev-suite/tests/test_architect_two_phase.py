"""Architect two-phase tool-access tests (Issue #193 PR 3).

Phase 1 keeps the pre-#193 behavior intact: the Architect generates a
Blueprint from memory + gathered_context without any tool access. When
that Blueprint is empty or placeholder-shaped, and READONLY_TOOLS are
available in the RunnableConfig, the Architect re-runs with tools bound
via `_run_tool_loop` (max = MAX_ARCHITECT_TOOL_TURNS) and re-parses.

These tests mock `_get_architect_llm` so no API calls are made.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import Tool

from src.agents.architect import Blueprint
from src.orchestrator import (
    MAX_ARCHITECT_TOOL_TURNS,
    WorkflowStatus,
    _blueprint_is_sufficient,
    architect_node,
)

# ---------------------------------------------------------------------------
# _blueprint_is_sufficient heuristic
# ---------------------------------------------------------------------------


def _bp(**kwargs: Any) -> Blueprint:
    """Build a Blueprint with sensible defaults."""
    return Blueprint(
        task_id=kwargs.get("task_id", "t"),
        target_files=kwargs.get("target_files", ["src/app.py"]),
        instructions=kwargs.get(
            "instructions",
            "Implement the login fix per the QA spec (non-trivial body).",
        ),
        constraints=kwargs.get("constraints", []),
        acceptance_criteria=kwargs.get("acceptance_criteria", []),
        summary=kwargs.get("summary", "Fix login"),
    )


class TestBlueprintSufficiency:
    def test_valid_blueprint_is_sufficient(self):
        assert _blueprint_is_sufficient(_bp()) is True

    def test_empty_target_files_is_insufficient(self):
        assert _blueprint_is_sufficient(_bp(target_files=[])) is False

    def test_placeholder_path_is_insufficient(self):
        assert _blueprint_is_sufficient(
            _bp(target_files=["path/to/file.py"])
        ) is False

    def test_angle_bracket_placeholder_is_insufficient(self):
        assert _blueprint_is_sufficient(
            _bp(target_files=["<unknown>"])
        ) is False
        assert _blueprint_is_sufficient(
            _bp(target_files=["src/<something>.py"])
        ) is False

    def test_tbd_style_placeholder_is_insufficient(self):
        assert _blueprint_is_sufficient(_bp(target_files=["TODO"])) is False
        assert _blueprint_is_sufficient(_bp(target_files=["TBD"])) is False

    def test_blank_entry_is_insufficient(self):
        assert _blueprint_is_sufficient(_bp(target_files=[""])) is False
        assert _blueprint_is_sufficient(_bp(target_files=["  "])) is False

    def test_short_instructions_is_insufficient(self):
        assert _blueprint_is_sufficient(_bp(instructions="Fix it")) is False

    def test_exact_20_char_boundary(self):
        # 19 chars — should fail
        assert _blueprint_is_sufficient(
            _bp(instructions="x" * 19)
        ) is False
        # 20 chars — should pass
        assert _blueprint_is_sufficient(
            _bp(instructions="x" * 20)
        ) is True


# ---------------------------------------------------------------------------
# architect_node two-phase behavior
# ---------------------------------------------------------------------------


def _make_response(text: str, *, tokens: int = 100) -> AIMessage:
    """Build an AIMessage shaped like langchain LLM output, with token meta."""
    msg = AIMessage(content=text)
    # Match what _extract_token_count expects — usage_metadata with total_tokens
    msg.usage_metadata = {
        "input_tokens": tokens // 2,
        "output_tokens": tokens // 2,
        "total_tokens": tokens,
    }
    return msg


def _fake_filesystem_list_tool() -> Tool:
    """A fake filesystem_list tool returning a fixed listing."""
    async def _arun(*_args, **_kwargs):
        return "src/auth.py\nsrc/login.py\ntests/test_auth.py"

    return Tool.from_function(
        name="filesystem_list",
        description="List files in a directory",
        func=lambda *_a, **_kw: "src/auth.py\nsrc/login.py",
        coroutine=_arun,
    )


def _fake_filesystem_read_tool() -> Tool:
    """A fake filesystem_read tool returning a fixed file body."""
    async def _arun(*_args, **_kwargs):
        return "def login():\n    pass  # broken"

    return Tool.from_function(
        name="filesystem_read",
        description="Read a file",
        func=lambda *_a, **_kw: "def login(): pass",
        coroutine=_arun,
    )


@pytest.mark.asyncio
class TestArchitectTwoPhase:
    @patch("src.orchestrator._fetch_memory_context", return_value=[])
    @patch("src.orchestrator._get_architect_llm")
    async def test_phase1_sufficient_skips_phase2(self, mock_llm, _mock_mem):
        """Sufficient Phase-1 blueprint → no tools called even if available."""
        good_blueprint = _bp(task_id="p1-good")
        mock_llm.return_value.ainvoke = AsyncMock(
            return_value=_make_response(good_blueprint.model_dump_json(), tokens=500)
        )
        # Even if the bind_tools branch was attempted, ensure we don't go there
        mock_llm.return_value.bind_tools = MagicMock(
            side_effect=AssertionError("bind_tools should not be called")
        )

        state = {
            "task_description": "Fix login",
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
        }
        config = {
            "configurable": {
                "tools": [_fake_filesystem_list_tool(), _fake_filesystem_read_tool()],
            }
        }
        result = await architect_node(state, config)

        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "p1-good"
        # Phase 1 path emits the classic trace and no tool_calls_log
        assert any("phase 1 blueprint created" in t for t in result["trace"])
        assert "tool_calls_log" not in result

    @patch("src.orchestrator._fetch_memory_context", return_value=[])
    @patch("src.orchestrator._get_architect_llm")
    async def test_phase1_insufficient_no_tools_falls_through(
        self, mock_llm, _mock_mem
    ):
        """Insufficient Phase-1 Blueprint but no tools → returns Phase-1 anyway.

        Keeps single-shot (test) mode and MCP-disabled deployments working —
        we don't fail the run just because we can't escalate.
        """
        weak_bp = _bp(task_id="p1-weak", target_files=["path/to/file.py"])
        mock_llm.return_value.ainvoke = AsyncMock(
            return_value=_make_response(weak_bp.model_dump_json())
        )

        state = {
            "task_description": "Fix something",
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
        }
        # No tools in config → no escalation
        config = {"configurable": {"tools": []}}
        result = await architect_node(state, config)

        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "p1-weak"
        assert not any("phase 2" in t for t in result["trace"])

    @patch("src.orchestrator._fetch_memory_context", return_value=[])
    @patch("src.orchestrator._get_architect_llm")
    async def test_phase1_insufficient_escalates_to_phase2(
        self, mock_llm, _mock_mem
    ):
        """Empty target_files + tools available → Phase 2 corrects."""
        # Phase 1: empty target_files (insufficient)
        weak_bp = _bp(task_id="p1-empty", target_files=[])
        # Phase 2: corrected blueprint
        good_bp = _bp(task_id="p2-corrected", target_files=["src/login.py"])

        llm_instance = MagicMock()
        # Phase 1 call returns weak; Phase 2 tool-loop-final response returns good.
        # The tool-loop also calls `llm_with_tools.ainvoke` — we mock that
        # via bind_tools returning a separate mock.
        llm_instance.ainvoke = AsyncMock(
            return_value=_make_response(weak_bp.model_dump_json())
        )

        tools_llm = MagicMock()
        # First tool-loop turn: return final answer directly with no tool_calls
        tools_llm.ainvoke = AsyncMock(
            return_value=_make_response(good_bp.model_dump_json(), tokens=300)
        )
        llm_instance.bind_tools = MagicMock(return_value=tools_llm)
        mock_llm.return_value = llm_instance

        state = {
            "task_description": "Please fix issue #113",
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
        }
        config = {
            "configurable": {
                "tools": [_fake_filesystem_list_tool(), _fake_filesystem_read_tool()],
            }
        }
        result = await architect_node(state, config)

        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "p2-corrected"
        assert result["blueprint"].target_files == ["src/login.py"]
        assert any("escalating to phase 2" in t for t in result["trace"])
        assert any("phase 2 blueprint parsed" in t for t in result["trace"])

    @patch("src.orchestrator._fetch_memory_context", return_value=[])
    @patch("src.orchestrator._get_architect_llm")
    async def test_phase2_tool_call_is_logged(self, mock_llm, _mock_mem):
        """Phase 2 tool invocations show up in tool_calls_log under 'architect'."""
        weak_bp = _bp(target_files=[])
        good_bp = _bp(task_id="p2-with-tool", target_files=["src/app.py"])

        llm_instance = MagicMock()
        llm_instance.ainvoke = AsyncMock(
            return_value=_make_response(weak_bp.model_dump_json())
        )

        # First tool-loop turn: emit a tool call to filesystem_list.
        # Second tool-loop turn: no tool calls → final blueprint.
        first_turn = _make_response("", tokens=50)
        first_turn.tool_calls = [
            {"name": "filesystem_list", "args": {"path": "."}, "id": "call-1"},
        ]
        second_turn = _make_response(good_bp.model_dump_json(), tokens=80)
        tools_llm = MagicMock()
        tools_llm.ainvoke = AsyncMock(side_effect=[first_turn, second_turn])
        llm_instance.bind_tools = MagicMock(return_value=tools_llm)
        mock_llm.return_value = llm_instance

        state = {
            "task_description": "Fix things",
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
            "tool_calls_log": [],
        }
        config = {
            "configurable": {
                "tools": [_fake_filesystem_list_tool(), _fake_filesystem_read_tool()],
            }
        }
        result = await architect_node(state, config)

        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "p2-with-tool"
        log = result.get("tool_calls_log", [])
        assert any(
            entry.get("agent") == "architect"
            and entry.get("tool") == "filesystem_list"
            for entry in log
        )

    @patch("src.orchestrator._fetch_memory_context", return_value=[])
    @patch("src.orchestrator._get_architect_llm")
    async def test_phase1_unparseable_phase2_rescues(self, mock_llm, _mock_mem):
        """Phase 1 returns garbage JSON; Phase 2 produces a valid Blueprint."""
        good_bp = _bp(task_id="p2-rescue", target_files=["src/app.py"])
        llm_instance = MagicMock()
        llm_instance.ainvoke = AsyncMock(
            return_value=_make_response("not json at all")
        )
        tools_llm = MagicMock()
        tools_llm.ainvoke = AsyncMock(
            return_value=_make_response(good_bp.model_dump_json())
        )
        llm_instance.bind_tools = MagicMock(return_value=tools_llm)
        mock_llm.return_value = llm_instance

        state = {
            "task_description": "Fix a thing",
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
        }
        config = {
            "configurable": {
                "tools": [_fake_filesystem_list_tool()],
            }
        }
        result = await architect_node(state, config)

        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "p2-rescue"
        assert any("phase 1 failed to parse" in t for t in result["trace"])

    @patch("src.orchestrator._fetch_memory_context", return_value=[])
    @patch("src.orchestrator._get_architect_llm")
    async def test_both_phases_fail_returns_failed_status(
        self, mock_llm, _mock_mem
    ):
        """Phase 1 and Phase 2 both return garbage → WorkflowStatus.FAILED."""
        llm_instance = MagicMock()
        llm_instance.ainvoke = AsyncMock(
            return_value=_make_response("nope not json")
        )
        tools_llm = MagicMock()
        tools_llm.ainvoke = AsyncMock(
            return_value=_make_response("still not json")
        )
        llm_instance.bind_tools = MagicMock(return_value=tools_llm)
        mock_llm.return_value = llm_instance

        state = {
            "task_description": "Something",
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
        }
        config = {
            "configurable": {
                "tools": [_fake_filesystem_list_tool()],
            }
        }
        result = await architect_node(state, config)

        assert result["status"] == WorkflowStatus.FAILED
        assert "phase 1 + phase 2 both failed" in result["error_message"]

    @patch("src.orchestrator._fetch_memory_context", return_value=[])
    @patch("src.orchestrator._get_architect_llm")
    async def test_phase2_respects_max_tool_turns(self, mock_llm, _mock_mem):
        """Phase 2 tool-loop caps at MAX_ARCHITECT_TOOL_TURNS.

        When Phase 2 hits max turns without producing a parseable Blueprint
        AND Phase 1 already gave us *some* Blueprint (even a weak one), we
        fall back to Phase 1 so the downstream retry loop gets a chance
        rather than hard-failing the run.
        """
        weak_bp = _bp(task_id="weak-fallback", target_files=[])
        llm_instance = MagicMock()
        llm_instance.ainvoke = AsyncMock(
            return_value=_make_response(weak_bp.model_dump_json())
        )

        # Every turn emits a tool call — the loop must still terminate.
        call_count = 0

        async def never_stops(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            msg = _make_response("", tokens=10)
            msg.tool_calls = [
                {"name": "filesystem_list", "args": {"path": "."}, "id": f"c-{call_count}"}
            ]
            return msg

        tools_llm = MagicMock()
        tools_llm.ainvoke = never_stops
        llm_instance.bind_tools = MagicMock(return_value=tools_llm)
        mock_llm.return_value = llm_instance

        state = {
            "task_description": "Endless",
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
        }
        config = {
            "configurable": {
                "tools": [_fake_filesystem_list_tool()],
            }
        }
        result = await architect_node(state, config)

        # The loop must call the LLM at most MAX_ARCHITECT_TOOL_TURNS times.
        assert call_count <= MAX_ARCHITECT_TOOL_TURNS
        # Fallback to Phase 1 blueprint (even though insufficient), so the
        # broader retry + QA loop gets its shot.
        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "weak-fallback"
        # Trace must note the tool-loop hit its cap
        assert any("hit max turns" in t for t in result["trace"])


# ---------------------------------------------------------------------------
# "Fix issue #113" integration-style flow (Issue #193 acceptance)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFixIssue113Integration:
    """Simulates the canonical self-dev gate flow end-to-end from the
    Architect's perspective.

    The Planner has pre-fetched issue #113 (`github://Abernaughty/agent-dev/issues/113`)
    into gathered_context. The Architect's Phase 1 produces an empty
    Blueprint because it can't tell which file to touch. Phase 2 uses
    filesystem_list/read to orient, then emits a proper Blueprint.
    """

    @patch("src.orchestrator._fetch_memory_context", return_value=[])
    @patch("src.orchestrator._get_architect_llm")
    async def test_planner_prefetched_context_flows_into_architect(
        self, mock_llm, _mock_mem
    ):
        # Planner's pre-fetch (PR 2) already put the issue body on state.
        planner_prefetched = [
            {
                "path": "github://Abernaughty/agent-dev/issues/113",
                "content": (
                    "# Issue #113: Self-dev gate test\n\n"
                    "State: open\n\n"
                    "Body: Run the end-to-end gate test; the orchestrator "
                    "should resolve its own task."
                ),
                "truncated": False,
                "source": "github_issue",
            }
        ]

        # Phase 1: architect has the issue body but no code to ground on;
        # emits empty target_files.
        phase1_bp = _bp(
            task_id="gate-p1",
            target_files=[],
            instructions=(
                "Need to locate the gate test runner before I can specify "
                "target_files. Please provide codebase access."
            ),
        )
        # Phase 2: after poking filesystem_list, architect commits.
        phase2_bp = _bp(
            task_id="gate-p2",
            target_files=["scripts/smoke-test.sh"],
            instructions=(
                "Extend smoke-test.sh stage 2 to cover the self-dev gate "
                "scenario described in the issue."
            ),
            acceptance_criteria=["Stage 2 of smoke-test.sh exercises the gate scenario"],
        )

        llm_instance = MagicMock()
        llm_instance.ainvoke = AsyncMock(
            return_value=_make_response(phase1_bp.model_dump_json())
        )

        # Tool-loop: one list → one final blueprint
        turn1 = _make_response("", tokens=40)
        turn1.tool_calls = [
            {"name": "filesystem_list", "args": {"path": "scripts"}, "id": "t1"},
        ]
        turn2 = _make_response(phase2_bp.model_dump_json(), tokens=120)
        tools_llm = MagicMock()
        tools_llm.ainvoke = AsyncMock(side_effect=[turn1, turn2])
        llm_instance.bind_tools = MagicMock(return_value=tools_llm)
        mock_llm.return_value = llm_instance

        # The state captures the full flow as it would arrive after
        # gather_context_node has folded prefetched items into gathered_context.
        state = {
            "task_description": (
                "Please address issue #113 — the self-dev gate test is blocking "
                "the roadmap."
            ),
            "trace": [],
            "tokens_used": 0,
            "retry_count": 0,
            "gathered_context": planner_prefetched,
        }
        config = {
            "configurable": {
                "tools": [_fake_filesystem_list_tool(), _fake_filesystem_read_tool()],
            }
        }
        result = await architect_node(state, config)

        assert result["status"] == WorkflowStatus.BUILDING
        assert result["blueprint"].task_id == "gate-p2"
        assert result["blueprint"].target_files == ["scripts/smoke-test.sh"]

        # Trace shows both phases ran in order
        trace_str = "\n".join(result["trace"])
        assert "escalating to phase 2" in trace_str
        assert "phase 2 blueprint parsed" in trace_str

        # Phase 1 system prompt must have embedded the prefetched GitHub body.
        # Inspect the first LLM call's messages.
        phase1_call = llm_instance.ainvoke.await_args
        messages = phase1_call.args[0]
        system_content = messages[0].content
        assert "github://Abernaughty/agent-dev/issues/113" in system_content
        assert "Self-dev gate test" in system_content

        # tool_calls_log records the Architect using filesystem_list
        log = result.get("tool_calls_log", [])
        assert log, "Phase 2 must append tool calls to the log"
        assert log[0]["agent"] == "architect"
        assert log[0]["tool"] == "filesystem_list"
