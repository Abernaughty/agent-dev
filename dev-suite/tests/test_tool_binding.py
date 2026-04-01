"""Tests for issue #80: Agent tool binding.

Tests cover:
- Tool extraction from RunnableConfig
- Tool filtering by agent role
- Tool execution (success and failure)
- Tool loop iteration and termination
- Developer node with tools (agentic mode)
- Developer node without tools (single-shot fallback)
- QA node with tools (read-only subset)
- QA node without tools (single-shot fallback)
- init_tools_config() factory
- TOOL_CALL event type in events.py
- Runner TOOL_CALL SSE emission
- GraphState tool_calls_log field
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeTool:
    def __init__(self, name, result="ok", should_raise=False):
        self.name = name
        self._result = result
        self._should_raise = should_raise
        self.coroutine = self._async_call
        self.invoke_count = 0

    async def _async_call(self, args):
        self.invoke_count += 1
        if self._should_raise:
            raise RuntimeError(f"Tool {self.name} failed")
        return self._result

    def invoke(self, args):
        self.invoke_count += 1
        if self._should_raise:
            raise RuntimeError(f"Tool {self.name} failed")
        return self._result


class FakeResponse:
    def __init__(self, content="test response", tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage_metadata = usage or {"input_tokens": 100, "output_tokens": 50}


def make_config(tools=None):
    return {"configurable": {"tools": tools or []}}


class TestGetAgentTools:
    def test_returns_empty_when_no_config(self):
        from src.orchestrator import _get_agent_tools
        assert _get_agent_tools(None) == []

    def test_returns_empty_when_no_configurable(self):
        from src.orchestrator import _get_agent_tools
        assert _get_agent_tools({}) == []

    def test_returns_empty_when_no_tools_key(self):
        from src.orchestrator import _get_agent_tools
        assert _get_agent_tools({"configurable": {}}) == []

    def test_returns_all_tools_when_no_filter(self):
        from src.orchestrator import _get_agent_tools
        tools = [FakeTool("a"), FakeTool("b"), FakeTool("c")]
        result = _get_agent_tools(make_config(tools))
        assert len(result) == 3

    def test_filters_by_allowed_names(self):
        from src.orchestrator import _get_agent_tools
        tools = [FakeTool("filesystem_read"), FakeTool("filesystem_write"), FakeTool("github_create_pr")]
        result = _get_agent_tools(make_config(tools), {"filesystem_read", "github_create_pr"})
        assert len(result) == 2
        assert {t.name for t in result} == {"filesystem_read", "github_create_pr"}

    def test_filters_returns_empty_when_no_match(self):
        from src.orchestrator import _get_agent_tools
        tools = [FakeTool("filesystem_read")]
        assert _get_agent_tools(make_config(tools), {"nonexistent_tool"}) == []

    def test_dev_tool_names_subset(self):
        from src.orchestrator import DEV_TOOL_NAMES, QA_TOOL_NAMES
        assert QA_TOOL_NAMES.issubset(DEV_TOOL_NAMES)

    def test_qa_has_no_write_tools(self):
        from src.orchestrator import QA_TOOL_NAMES
        for name in QA_TOOL_NAMES:
            assert "write" not in name.lower()
            assert "create" not in name.lower()


class TestExecuteToolCall:
    @pytest.mark.asyncio
    async def test_executes_tool_successfully(self):
        from src.orchestrator import _execute_tool_call
        tool = FakeTool("filesystem_read", result="file contents here")
        tool_call = {"name": "filesystem_read", "args": {"path": "test.py"}, "id": "call_1"}
        result = await _execute_tool_call(tool_call, [tool])
        assert result.content == "file contents here"
        assert result.tool_call_id == "call_1"
        assert tool.invoke_count == 1

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_tool(self):
        from src.orchestrator import _execute_tool_call
        tool_call = {"name": "nonexistent", "args": {}, "id": "call_2"}
        result = await _execute_tool_call(tool_call, [FakeTool("other")])
        assert "Error" in result.content
        assert "nonexistent" in result.content

    @pytest.mark.asyncio
    async def test_handles_tool_exception(self):
        from src.orchestrator import _execute_tool_call
        tool = FakeTool("failing_tool", should_raise=True)
        tool_call = {"name": "failing_tool", "args": {}, "id": "call_3"}
        result = await _execute_tool_call(tool_call, [tool])
        assert "Error executing failing_tool" in result.content

    @pytest.mark.asyncio
    async def test_prefers_async_coroutine(self):
        from src.orchestrator import _execute_tool_call
        tool = FakeTool("async_tool", result="async result")
        tool_call = {"name": "async_tool", "args": {}, "id": "call_4"}
        result = await _execute_tool_call(tool_call, [tool])
        assert result.content == "async result"

    @pytest.mark.asyncio
    async def test_falls_back_to_sync_invoke(self):
        from src.orchestrator import _execute_tool_call
        tool = FakeTool("sync_tool", result="sync result")
        tool.coroutine = None
        tool_call = {"name": "sync_tool", "args": {}, "id": "call_5"}
        result = await _execute_tool_call(tool_call, [tool])
        assert result.content == "sync result"


class TestRunToolLoop:
    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_immediately(self):
        from src.orchestrator import _run_tool_loop
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = FakeResponse(content="final answer")
        response, tokens, log = await _run_tool_loop(mock_llm, [{"role": "user", "content": "test"}], [])
        assert response.content == "final answer"
        assert mock_llm.ainvoke.call_count == 1
        assert len(log) == 0

    @pytest.mark.asyncio
    async def test_single_tool_turn(self):
        from src.orchestrator import _run_tool_loop
        tool = FakeTool("filesystem_read", result="contents")
        first_response = FakeResponse(content="")
        first_response.tool_calls = [{"name": "filesystem_read", "args": {"path": "f.py"}, "id": "tc1"}]
        second_response = FakeResponse(content="done with tools")
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [first_response, second_response]
        response, tokens, log = await _run_tool_loop(mock_llm, [], [tool])
        assert response.content == "done with tools"
        assert mock_llm.ainvoke.call_count == 2
        assert len(log) == 1
        assert log[0]["tool"] == "filesystem_read"
        assert log[0]["success"] is True

    @pytest.mark.asyncio
    async def test_max_turns_limit(self):
        from src.orchestrator import _run_tool_loop
        tool = FakeTool("filesystem_read", result="contents")
        response_with_tools = FakeResponse(content="")
        response_with_tools.tool_calls = [{"name": "filesystem_read", "args": {}, "id": "tc"}]
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = response_with_tools
        response, tokens, log = await _run_tool_loop(mock_llm, [], [tool], max_turns=3)
        assert mock_llm.ainvoke.call_count == 3
        assert len(log) == 3

    @pytest.mark.asyncio
    async def test_accumulates_tokens(self):
        from src.orchestrator import _run_tool_loop
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = FakeResponse(content="answer", usage={"input_tokens": 200, "output_tokens": 100})
        response, tokens, log = await _run_tool_loop(mock_llm, [], [], tokens_used=500)
        assert tokens == 800

    @pytest.mark.asyncio
    async def test_tool_failure_logged(self):
        from src.orchestrator import _run_tool_loop
        tool = FakeTool("bad_tool", should_raise=True)
        first_response = FakeResponse(content="")
        first_response.tool_calls = [{"name": "bad_tool", "args": {}, "id": "tc1"}]
        second_response = FakeResponse(content="recovered")
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [first_response, second_response]
        response, tokens, log = await _run_tool_loop(mock_llm, [], [tool])
        assert len(log) == 1
        assert log[0]["success"] is False


class TestDeveloperNodeTools:
    @pytest.fixture
    def blueprint(self):
        from src.agents.architect import Blueprint
        return Blueprint(task_id="test-task", target_files=["src/main.py"], instructions="Write a main function", constraints=["Use type hints"], acceptance_criteria=["Function exists"])

    @pytest.fixture
    def base_state(self, blueprint):
        return {"task_description": "Test task", "blueprint": blueprint, "generated_code": "", "failure_report": None, "status": "building", "retry_count": 0, "tokens_used": 0, "error_message": "", "memory_context": [], "memory_writes": [], "trace": [], "sandbox_result": None, "parsed_files": [], "tool_calls_log": []}

    @pytest.mark.asyncio
    async def test_dev_with_tools_uses_bind_tools(self, base_state):
        from src.orchestrator import developer_node
        tools = [FakeTool("filesystem_read"), FakeTool("filesystem_write")]
        config = make_config(tools)
        mock_llm = MagicMock()
        mock_llm_with_tools = AsyncMock()
        mock_llm.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.ainvoke.return_value = FakeResponse(content="# --- FILE: src/main.py ---\ndef main(): pass")
        with patch("src.orchestrator._get_developer_llm", return_value=mock_llm):
            result = await developer_node(base_state, config)
        mock_llm.bind_tools.assert_called_once()
        assert result["generated_code"] != ""
        assert result["status"].value == "reviewing"

    @pytest.mark.asyncio
    async def test_dev_without_tools_single_shot(self, base_state):
        from src.orchestrator import developer_node
        config = make_config([])
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = FakeResponse(content="# --- FILE: src/main.py ---\ndef main(): pass")
        with patch("src.orchestrator._get_developer_llm", return_value=mock_llm):
            result = await developer_node(base_state, config)
        mock_llm.invoke.assert_called_once()
        mock_llm.bind_tools.assert_not_called()
        assert result["generated_code"] != ""

    @pytest.mark.asyncio
    async def test_dev_no_config_single_shot(self, base_state):
        from src.orchestrator import developer_node
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = FakeResponse(content="code here")
        with patch("src.orchestrator._get_developer_llm", return_value=mock_llm):
            result = await developer_node(base_state, None)
        mock_llm.invoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_dev_tool_calls_logged_in_state(self, base_state):
        from src.orchestrator import developer_node
        tool = FakeTool("filesystem_read", result="existing code")
        config = make_config([tool])
        first_resp = FakeResponse(content="")
        first_resp.tool_calls = [{"name": "filesystem_read", "args": {"path": "x"}, "id": "tc1"}]
        second_resp = FakeResponse(content="# --- FILE: src/main.py ---\ncode")
        mock_llm = MagicMock()
        mock_llm_bound = AsyncMock()
        mock_llm.bind_tools.return_value = mock_llm_bound
        mock_llm_bound.ainvoke.side_effect = [first_resp, second_resp]
        with patch("src.orchestrator._get_developer_llm", return_value=mock_llm):
            result = await developer_node(base_state, config)
        assert len(result["tool_calls_log"]) == 1
        assert result["tool_calls_log"][0]["tool"] == "filesystem_read"
        assert result["tool_calls_log"][0]["agent"] == "developer"

    @pytest.mark.asyncio
    async def test_dev_tools_filtered_to_dev_set(self, base_state):
        from src.orchestrator import DEV_TOOL_NAMES, developer_node
        all_tools = [FakeTool(name) for name in ["filesystem_read", "filesystem_write", "filesystem_list", "github_read_diff", "github_create_pr", "unexpected_tool"]]
        config = make_config(all_tools)
        mock_llm = MagicMock()
        mock_llm_bound = AsyncMock()
        mock_llm.bind_tools.return_value = mock_llm_bound
        mock_llm_bound.ainvoke.return_value = FakeResponse(content="code")
        with patch("src.orchestrator._get_developer_llm", return_value=mock_llm):
            result = await developer_node(base_state, config)
        bound_tools = mock_llm.bind_tools.call_args[0][0]
        bound_names = {t.name for t in bound_tools}
        assert bound_names == DEV_TOOL_NAMES


class TestQANodeTools:
    @pytest.fixture
    def qa_state(self):
        from src.agents.architect import Blueprint
        bp = Blueprint(task_id="test-qa", target_files=["src/main.py"], instructions="Implement feature", constraints=[], acceptance_criteria=["Tests pass"])
        return {"task_description": "Test task", "blueprint": bp, "generated_code": "# --- FILE: src/main.py ---\ndef main(): pass", "failure_report": None, "status": "reviewing", "retry_count": 0, "tokens_used": 0, "error_message": "", "memory_context": [], "memory_writes": [], "trace": [], "sandbox_result": None, "parsed_files": [], "tool_calls_log": []}

    @pytest.mark.asyncio
    async def test_qa_with_tools_gets_read_only(self, qa_state):
        from src.orchestrator import QA_TOOL_NAMES, qa_node
        all_tools = [FakeTool(name) for name in ["filesystem_read", "filesystem_write", "filesystem_list", "github_read_diff", "github_create_pr"]]
        config = make_config(all_tools)
        mock_llm = MagicMock()
        mock_llm_bound = AsyncMock()
        mock_llm.bind_tools.return_value = mock_llm_bound
        qa_json = json.dumps({"task_id": "test-qa", "status": "pass", "tests_passed": 1, "tests_failed": 0, "errors": [], "failed_files": [], "is_architectural": False, "failure_type": None, "recommendation": "All good"})
        mock_llm_bound.ainvoke.return_value = FakeResponse(content=qa_json)
        with patch("src.orchestrator._get_qa_llm", return_value=mock_llm):
            result = await qa_node(qa_state, config)
        bound_tools = mock_llm.bind_tools.call_args[0][0]
        bound_names = {t.name for t in bound_tools}
        assert bound_names == QA_TOOL_NAMES
        assert "filesystem_write" not in bound_names

    @pytest.mark.asyncio
    async def test_qa_without_tools(self, qa_state):
        from src.orchestrator import qa_node
        config = make_config([])
        mock_llm = MagicMock()
        qa_json = json.dumps({"task_id": "test-qa", "status": "pass", "tests_passed": 1, "tests_failed": 0, "errors": [], "failed_files": [], "is_architectural": False, "failure_type": None, "recommendation": "All good"})
        mock_llm.invoke.return_value = FakeResponse(content=qa_json)
        with patch("src.orchestrator._get_qa_llm", return_value=mock_llm):
            result = await qa_node(qa_state, config)
        mock_llm.invoke.assert_called_once()
        assert result["failure_report"].status == "pass"


class TestInitToolsConfig:
    def test_returns_empty_when_no_config_file(self, tmp_path):
        from src.orchestrator import init_tools_config
        result = init_tools_config(workspace_root=tmp_path)
        assert result == {"configurable": {"tools": []}}

    def test_returns_empty_on_exception(self, tmp_path):
        from src.orchestrator import init_tools_config
        config = tmp_path / "mcp-config.json"
        config.write_text("not valid json")
        result = init_tools_config(workspace_root=tmp_path)
        assert result == {"configurable": {"tools": []}}

    def test_loads_tools_from_valid_config(self, tmp_path):
        from src.orchestrator import init_tools_config
        config = tmp_path / "mcp-config.json"
        config.write_text(json.dumps({"servers": {"filesystem": {"version": "1.0"}}, "last_reviewed": "2026-03-01"}))
        mock_tools = [FakeTool("filesystem_read")]
        with patch("src.orchestrator.load_mcp_config") as mock_load, patch("src.orchestrator.create_provider") as mock_create, patch("src.orchestrator.get_tools", return_value=mock_tools):
            result = init_tools_config(workspace_root=tmp_path)
        assert len(result["configurable"]["tools"]) == 1


class TestToolCallEvent:
    def test_tool_call_event_type_exists(self):
        from src.api.events import EventType
        assert hasattr(EventType, "TOOL_CALL")
        assert EventType.TOOL_CALL.value == "tool_call"

    def test_can_create_tool_call_sse_event(self):
        from src.api.events import EventType, SSEEvent
        event = SSEEvent(type=EventType.TOOL_CALL, data={"task_id": "test", "agent": "developer", "tool": "filesystem_read", "success": True, "result_preview": "file contents"})
        assert event.type == EventType.TOOL_CALL
        assert event.data["tool"] == "filesystem_read"

    @pytest.mark.asyncio
    async def test_tool_call_event_published(self):
        from src.api.events import EventBus, EventType, SSEEvent
        bus = EventBus()
        queue = await bus.subscribe()
        event = SSEEvent(type=EventType.TOOL_CALL, data={"agent": "dev", "tool": "filesystem_read"})
        delivered = await bus.publish(event)
        assert delivered == 1
        received = queue.get_nowait()
        assert received.type == EventType.TOOL_CALL


class TestGraphStateToolCallsLog:
    def test_graph_state_has_tool_calls_log(self):
        from src.orchestrator import GraphState
        assert "tool_calls_log" in GraphState.__annotations__

    def test_agent_state_has_tool_calls_log(self):
        from src.orchestrator import AgentState
        state = AgentState()
        assert state.tool_calls_log == []

    def test_agent_state_with_tool_calls(self):
        from src.orchestrator import AgentState
        state = AgentState(tool_calls_log=[{"agent": "dev", "tool": "filesystem_read", "success": True}])
        assert len(state.tool_calls_log) == 1


class TestMaxToolTurns:
    def test_max_tool_turns_default(self):
        from src.orchestrator import MAX_TOOL_TURNS
        assert MAX_TOOL_TURNS == 10

    def test_max_tool_turns_from_env(self, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_TURNS", "5")
        from src.orchestrator import _safe_int
        assert _safe_int("MAX_TOOL_TURNS", 10) == 5
