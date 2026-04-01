"""Async task runner bridging the FastAPI API to the LangGraph orchestrator.

Issue #48: StateManager <-> Orchestrator bridge
Issue #80: Tool binding -- tools_config initialization, TOOL_CALL SSE events

Uses LangGraph's astream() to iterate node completions and emit SSE events
in real time. Runs entirely on the async event loop -- no threading needed.

Usage:
    from src.api.runner import task_runner

    # In POST /tasks handler:
    task_id = await state_manager.create_task(description)
    task_runner.submit(task_id, description)

    # On shutdown:
    await task_runner.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from ..agents.architect import Blueprint
from ..orchestrator import (
    GraphState,
    WorkflowStatus,
    build_graph,
    init_tools_config,
    MAX_RETRIES,
    TOKEN_BUDGET,
)
from .events import EventType, SSEEvent, event_bus
from .models import (
    AgentStatus,
    BlueprintResponse,
    TaskBudget,
    TaskStatus,
    TimelineEvent,
)

logger = logging.getLogger(__name__)

# Map orchestrator node names to agent IDs and API statuses
NODE_TO_AGENT = {
    "architect": ("arch", AgentStatus.PLANNING),
    "developer": ("dev", AgentStatus.CODING),
    "qa": ("qa", AgentStatus.REVIEWING),
}

# Infrastructure nodes that get SSE events but aren't mapped to agents
INFRA_NODES = {"apply_code", "sandbox_validate", "flush_memory"}

# Map orchestrator WorkflowStatus to API TaskStatus
WORKFLOW_TO_TASK_STATUS = {
    WorkflowStatus.PLANNING: TaskStatus.PLANNING,
    WorkflowStatus.BUILDING: TaskStatus.BUILDING,
    WorkflowStatus.REVIEWING: TaskStatus.REVIEWING,
    WorkflowStatus.PASSED: TaskStatus.PASSED,
    WorkflowStatus.FAILED: TaskStatus.FAILED,
    WorkflowStatus.ESCALATED: TaskStatus.ESCALATED,
}

# Rough cost estimate per token (blended rate across models)
COST_PER_TOKEN = 0.000012


def _now_str() -> str:
    """Current time as HH:MM string for timeline events."""
    return datetime.now(timezone.utc).strftime("%H:%M")


def _blueprint_to_response(bp: Blueprint) -> BlueprintResponse:
    """Convert orchestrator Blueprint to API BlueprintResponse."""
    return BlueprintResponse(
        task_id=bp.task_id,
        target_files=bp.target_files,
        instructions=bp.instructions,
        constraints=bp.constraints,
        acceptance_criteria=bp.acceptance_criteria,
    )


class TaskRunner:
    """Manages background orchestrator runs, emitting SSE events per node.

    Uses LangGraph's astream() to iterate state diffs after each node.
    Each diff tells us which node just completed and what it produced,
    allowing us to emit granular SSE events to the dashboard.
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(self, task_id: str, description: str) -> None:
        """Submit a task for background execution."""
        if task_id in self._tasks:
            logger.warning("Task %s already running, ignoring duplicate submit", task_id)
            return

        coro = self._run_task(task_id, description)
        async_task = asyncio.create_task(coro, name=f"orchestrator-{task_id}")
        self._tasks[task_id] = async_task
        async_task.add_done_callback(lambda t: self._tasks.pop(task_id, None))
        logger.info("Task %s submitted for background execution", task_id)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        async_task = self._tasks.get(task_id)
        if not async_task:
            return False
        async_task.cancel()
        logger.info("Task %s cancellation requested", task_id)
        return True

    async def shutdown(self) -> None:
        """Cancel all running tasks. Called on API shutdown."""
        for task_id, async_task in list(self._tasks.items()):
            async_task.cancel()
            logger.info("Shutting down task %s", task_id)
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info("TaskRunner shutdown complete")

    @property
    def running_count(self) -> int:
        return len(self._tasks)

    async def _run_task(self, task_id: str, description: str) -> None:
        """Run the orchestrator via astream() and emit SSE events."""
        from .state import state_manager

        start_time = time.time()

        try:
            await self._emit_progress(task_id, "task_started", None, f"Task started: {description[:100]}")
            await self._emit_log(f"[orchestrator] Task accepted: {task_id}")
            await self._emit_log("[orchestrator] Spinning up agent team...")

            graph = build_graph()
            workflow = graph.compile()

            # Initialize tools config (issue #80)
            tools_config = init_tools_config()
            n_tools = len(tools_config.get("configurable", {}).get("tools", []))
            if n_tools > 0:
                await self._emit_log(f"[orchestrator] {n_tools} tools loaded for agents")
            else:
                await self._emit_log("[orchestrator] No tools configured (single-shot mode)")

            initial_state: GraphState = {
                "task_description": description,
                "blueprint": None,
                "generated_code": "",
                "failure_report": None,
                "status": WorkflowStatus.PLANNING,
                "retry_count": 0,
                "tokens_used": 0,
                "error_message": "",
                "memory_context": [],
                "trace": [],
                "parsed_files": [],
                "tool_calls_log": [],
            }

            stream_config = {
                "recursion_limit": 25,
                **tools_config,
            }

            prev_node = None
            prev_tool_count = 0
            async for event in workflow.astream(initial_state, config=stream_config):
                for node_name, node_output in event.items():
                    if node_name.startswith("__"):
                        continue

                    # Emit tool_call SSE events for any new tool calls (issue #80)
                    tool_calls_log = node_output.get("tool_calls_log", [])
                    if len(tool_calls_log) > prev_tool_count:
                        new_calls = tool_calls_log[prev_tool_count:]
                        for tc in new_calls:
                            await self._emit_tool_call(
                                task_id,
                                tc.get("agent", "unknown"),
                                tc.get("tool", "unknown"),
                                tc.get("success", True),
                                tc.get("result_preview", ""),
                            )
                        prev_tool_count = len(tool_calls_log)

                    await self._handle_node_completion(
                        task_id, node_name, node_output, state_manager, prev_node,
                    )
                    prev_node = node_name

            task = state_manager.get_task(task_id)
            if task:
                elapsed = time.time() - start_time
                final_status = task.status
                for agent_id in ("arch", "dev", "qa"):
                    await state_manager.update_agent_status(agent_id, AgentStatus.IDLE)
                await self._emit_complete(task_id, final_status.value, f"Task completed in {elapsed:.1f}s -- {final_status.value}")
                await self._emit_log(f"[orchestrator] Task {task_id} finished: {final_status.value} ({elapsed:.1f}s, {task.budget.tokens_used} tokens, ${task.budget.cost_used:.2f})")

        except asyncio.CancelledError:
            logger.info("Task %s was cancelled", task_id)
            task = state_manager.get_task(task_id)
            if task:
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc)
            await self._emit_complete(task_id, "cancelled", "Task was cancelled")

        except Exception as e:
            logger.error("Task %s failed with exception: %s", task_id, e, exc_info=True)
            task = state_manager.get_task(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.error_message = str(e)
                task.completed_at = datetime.now(timezone.utc)
            for agent_id in ("arch", "dev", "qa"):
                await state_manager.update_agent_status(agent_id, AgentStatus.IDLE)
            await self._emit_complete(task_id, "failed", f"Task failed: {e}")
            await self._emit_log(f"[orchestrator] ERROR: {e}")

    async def _handle_node_completion(self, task_id, node_name, node_output, state_manager, prev_node):
        """Process a completed node and emit appropriate SSE events."""
        if prev_node and prev_node in NODE_TO_AGENT:
            prev_agent_id, _ = NODE_TO_AGENT[prev_node]
            await state_manager.update_agent_status(prev_agent_id, AgentStatus.IDLE)

        # Handle infrastructure nodes (apply_code, sandbox_validate, flush_memory)
        if node_name in INFRA_NODES:
            await self._handle_infra_node(task_id, node_name, node_output, state_manager)
            return

        if node_name not in NODE_TO_AGENT:
            return

        agent_id, agent_status = NODE_TO_AGENT[node_name]
        task = state_manager.get_task(task_id)
        if not task:
            return

        workflow_status = node_output.get("status")
        if workflow_status and isinstance(workflow_status, WorkflowStatus):
            new_task_status = WORKFLOW_TO_TASK_STATUS.get(workflow_status)
            if new_task_status:
                task.status = new_task_status
                if new_task_status in (TaskStatus.PASSED, TaskStatus.FAILED, TaskStatus.ESCALATED):
                    task.completed_at = datetime.now(timezone.utc)

        tokens_used = node_output.get("tokens_used", task.budget.tokens_used)
        retry_count = node_output.get("retry_count", task.budget.retries_used)
        cost_used = round(tokens_used * COST_PER_TOKEN, 4)
        task.budget.tokens_used = tokens_used
        task.budget.retries_used = retry_count
        task.budget.cost_used = cost_used

        if node_name == "architect":
            await self._handle_architect(task_id, node_output, task, state_manager)
        elif node_name == "developer":
            await self._handle_developer(task_id, node_output, task, state_manager)
        elif node_name == "qa":
            await self._handle_qa(task_id, node_output, task, state_manager)

    async def _handle_infra_node(self, task_id, node_name, node_output, state_manager):
        """Handle infrastructure nodes that aren't tied to specific agents."""
        task = state_manager.get_task(task_id)
        if not task:
            return

        if node_name == "apply_code":
            parsed_files = node_output.get("parsed_files", [])
            if parsed_files:
                n_files = len(parsed_files)
                total_chars = sum(len(f.get("content", "")) for f in parsed_files)
                action = f"Applied {n_files} file{'s' if n_files != 1 else ''} to workspace ({total_chars:,} chars)"
                task.timeline.append(TimelineEvent(
                    time=_now_str(), agent="dev", action=action, type="exec",
                ))
                await self._emit_progress(task_id, "code_applied", "dev", action)
                await self._emit_log(f"[apply_code] Writing {n_files} files to workspace...")
                for pf in parsed_files:
                    await self._emit_log(f"[apply_code] + {pf.get('path', '?')}")
            else:
                await self._emit_log("[apply_code] No files to apply (skipped)")

        elif node_name == "sandbox_validate":
            sandbox_result = node_output.get("sandbox_result")
            if sandbox_result is not None:
                passed = sandbox_result.tests_passed
                failed = sandbox_result.tests_failed
                exit_code = sandbox_result.exit_code
                if exit_code == 0 and (failed is None or failed == 0):
                    action = f"Sandbox validation passed (exit code {exit_code})"
                    if passed is not None:
                        action = f"Sandbox: {passed} tests passed"
                    await self._emit_log(f"[sandbox:locked] Validation passed (exit={exit_code})")
                else:
                    action = f"Sandbox: {failed or '?'} test(s) failed"
                    await self._emit_log(f"[sandbox:locked] Validation: {passed or '?'} passed, {failed or '?'} failed")
                task.timeline.append(TimelineEvent(
                    time=_now_str(), agent="qa", action=action, type="exec",
                ))
                await self._emit_progress(task_id, "sandbox_validated", "qa", action)
            else:
                await self._emit_log("[sandbox] Validation skipped (no E2B key or no code files)")

        # flush_memory doesn't need SSE events -- it's internal bookkeeping

    async def _handle_architect(self, task_id, output, task, state_manager):
        blueprint = output.get("blueprint")
        if blueprint and isinstance(blueprint, Blueprint):
            task.blueprint = _blueprint_to_response(blueprint)
            n_files = len(blueprint.target_files)
            action = f"Blueprint created for {n_files} file{'s' if n_files != 1 else ''}"
            event_type = "plan"
            await state_manager.update_agent_status("arch", AgentStatus.IDLE, task_id)
            await self._emit_log(f"[orchestrator] Architect -> blueprint for {n_files} files")
        else:
            error = output.get("error_message", "Blueprint generation failed")
            action = f"Planning failed: {error[:80]}"
            event_type = "fail"
            await self._emit_log(f"[orchestrator] Architect FAILED: {error[:120]}")
        task.timeline.append(TimelineEvent(time=_now_str(), agent="arch", action=action, type=event_type))
        await self._emit_progress(task_id, "blueprint_created", "arch", action)

    async def _handle_developer(self, task_id, output, task, state_manager):
        code = output.get("generated_code", "")
        task.generated_code = code

        # Log tool usage summary (issue #80)
        tool_calls_log = output.get("tool_calls_log", [])
        dev_tool_calls = [tc for tc in tool_calls_log if tc.get("agent") == "developer"]

        if code:
            action = f"Code generated ({len(code):,} chars)"
            if dev_tool_calls:
                action += f" using {len(dev_tool_calls)} tool call(s)"
            event_type = "code"
            retry = task.budget.retries_used
            if retry > 0:
                action = f"Retry {retry}/{task.budget.max_retries} -- code regenerated ({len(code):,} chars)"
                if dev_tool_calls:
                    action += f" using {len(dev_tool_calls)} tool call(s)"
                event_type = "retry"
            await self._emit_log("[sandbox:locked] E2B micro-VM started (dev-sandbox)")
        else:
            action = "Code generation failed"
            event_type = "fail"
        task.timeline.append(TimelineEvent(time=_now_str(), agent="dev", action=action, type=event_type))
        await self._emit_progress(task_id, "code_generated", "dev", action)
        await state_manager.update_agent_status("dev", AgentStatus.IDLE, task_id)

    async def _handle_qa(self, task_id, output, task, state_manager):
        failure_report = output.get("failure_report")
        if failure_report:
            passed = failure_report.tests_passed
            failed = failure_report.tests_failed
            verdict = failure_report.status
            if verdict == "pass":
                action = f"All {passed} tests passing"
                event_type = "success"
                await self._emit_log(f"[qa] {passed}/{passed} tests passing")
            elif failure_report.is_architectural:
                action = "Architectural issue detected -- escalating to Architect"
                event_type = "fail"
                await self._emit_log(f"[qa] ESCALATION: {', '.join(failure_report.errors[:2])}")
            else:
                errors_preview = ", ".join(failure_report.errors[:2])
                action = f"{failed} test{'s' if failed != 1 else ''} failed: {errors_preview}"
                event_type = "fail"
                await self._emit_log(f"[qa] {failed}/{passed + failed} tests failed: {errors_preview}")
        else:
            action = "QA review completed"
            event_type = "exec"
        task.timeline.append(TimelineEvent(time=_now_str(), agent="qa", action=action, type=event_type))
        await self._emit_progress(task_id, "qa_complete", "qa", action)
        await state_manager.update_agent_status("qa", AgentStatus.IDLE, task_id)

    async def _emit_progress(self, task_id, event, agent, detail):
        try:
            await event_bus.publish(SSEEvent(type=EventType.TASK_PROGRESS, data={"task_id": task_id, "event": event, "agent": agent, "detail": detail}))
        except Exception:
            logger.debug("Failed to emit task_progress", exc_info=True)

    async def _emit_complete(self, task_id, status, detail):
        try:
            await event_bus.publish(SSEEvent(type=EventType.TASK_COMPLETE, data={"task_id": task_id, "status": status, "detail": detail}))
        except Exception:
            logger.debug("Failed to emit task_complete", exc_info=True)

    async def _emit_log(self, message):
        try:
            await event_bus.publish(SSEEvent(type=EventType.LOG_LINE, data={"message": message, "level": "info"}))
        except Exception:
            logger.debug("Failed to emit log_line", exc_info=True)

    async def _emit_tool_call(self, task_id, agent, tool_name, success, result_preview):
        """Emit a TOOL_CALL SSE event for dashboard tool usage tracking (issue #80)."""
        try:
            await event_bus.publish(SSEEvent(
                type=EventType.TOOL_CALL,
                data={
                    "task_id": task_id,
                    "agent": agent,
                    "tool": tool_name,
                    "success": success,
                    "result_preview": result_preview[:100] if result_preview else "",
                },
            ))
        except Exception:
            logger.debug("Failed to emit tool_call", exc_info=True)


# -- Singleton --

task_runner = TaskRunner()
