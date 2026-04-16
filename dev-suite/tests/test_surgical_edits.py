"""Layer A integration tests for the six #179 surgical-edit fixes.

Each fix gets its own test class exercising the orchestrator graph
with mocked LLMs. The scenario mirrors the #113 gate test that
originally butchered `BottomPanel.svelte` — a sibling-directory
Svelte file with terminal/SSE/tabs/command-input functionality that
must be preserved while a 1-line `Math.min(400, ...)` clamp is fixed.

Offline, deterministic, runs in seconds. Catches regressions before
spending LLM tokens on the live Layer B rerun.

Fixes exercised:
- Fix 1 (RC1): cross-directory context gathering
- Fix 2 (RC2): `filesystem_patch` tool
- Fix 3 (RC3): Developer system prompt
- Fix 4 (RC4): Architect preservation constraints
- Fix 5 (RC5): QA scope-creep detection
- Fix 6 (RC6): `Blueprint.summary` -> clean PR title
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.architect import Blueprint
from src.orchestrator import (
    GraphState,
    WorkflowStatus,
    architect_node,
    developer_node,
    gather_context_node,
    publish_code_node,
    qa_node,
)
from src.tools.provider import BlockedPathError, LocalToolProvider

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "BottomPanel.svelte.original"
TASK_113_DESCRIPTION = (
    "Fix the terminal panel drag-to-resize bug: in "
    "dashboard/src/lib/components/BottomPanel.svelte, change "
    "Math.min(400, ...) to Math.min(window.innerHeight * 0.8, ...) "
    "so the panel can be resized beyond 400px."
)


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a repo layout with .git/, dev-suite/, and dashboard/BottomPanel.svelte.

    Returns (repo_root, workspace_root) where workspace_root is dev-suite/.
    """
    (tmp_path / ".git").mkdir()
    workspace_root = tmp_path / "dev-suite"
    workspace_root.mkdir()
    target_dir = tmp_path / "dashboard" / "src" / "lib" / "components"
    target_dir.mkdir(parents=True)
    target_file = target_dir / "BottomPanel.svelte"
    target_file.write_text(
        FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path, workspace_root


def _make_llm_response(content: str, tokens: int = 100) -> MagicMock:
    """Build a MagicMock that looks like an LLM ChatMessage response."""
    resp = MagicMock()
    resp.content = content
    resp.usage_metadata = {
        "input_tokens": tokens,
        "output_tokens": tokens,
        "total_tokens": tokens * 2,
    }
    resp.tool_calls = []
    return resp


# -- Fix 1: cross-directory context gathering --

class TestContextGatheringFindsSiblingFile:
    """Fix 1 (RC1): gather_context_node resolves a sibling-directory file
    via repo root when workspace_root is a monorepo subfolder."""

    async def test_gather_context_finds_bottompanel_from_dev_suite_workspace(
        self, tmp_path
    ):
        _, workspace_root = _make_repo(tmp_path)
        state: GraphState = {
            "task_description": TASK_113_DESCRIPTION,
            "workspace_root": str(workspace_root),
            "trace": [],
            "status": WorkflowStatus.PLANNING,
        }
        result = await gather_context_node(state)
        gathered = result.get("gathered_context") or []
        paths = [c["path"] for c in gathered]
        matching = [c for c in gathered if c["path"].endswith("BottomPanel.svelte")]
        assert matching, (
            f"Expected BottomPanel.svelte via repo-root resolution, got: {paths}"
        )
        content = matching[0]["content"]
        # Prove the real terminal/SSE/command-input code is present.
        assert "onMount" in content
        assert "tasksStore" in content
        assert "handleMouseDown" in content
        assert "Math.min(400" in content  # the bug we want to patch


# -- Fix 2: filesystem_patch tool --

class TestFilesystemPatchSurgicalEdit:
    """Fix 2 (RC2): filesystem_patch performs a single-match search/replace
    that preserves every other byte of the file."""

    async def test_single_match_patches_and_preserves_remaining_lines(
        self, tmp_path
    ):
        _, workspace_root = _make_repo(tmp_path)
        panel = tmp_path / "dashboard" / "src" / "lib" / "components" / "BottomPanel.svelte"
        original = panel.read_text(encoding="utf-8")
        original_line_count = len(original.splitlines())

        # Workspace for the tool provider is the repo root, which is where
        # the orchestrator opens up access after Fix 1's `allowed_root`
        # parameter. This matches the live pipeline's behavior.
        provider = LocalToolProvider(workspace_root=tmp_path)
        result = await provider.call_tool(
            "filesystem_patch",
            {
                "path": "dashboard/src/lib/components/BottomPanel.svelte",
                "search": "Math.min(400, startH + (startY - e.clientY))",
                "replace": (
                    "Math.min(window.innerHeight * 0.8, "
                    "startH + (startY - e.clientY))"
                ),
            },
        )
        assert "Successfully patched" in result
        patched = panel.read_text(encoding="utf-8")

        # Surgical — line count unchanged, only the one line differs.
        assert len(patched.splitlines()) == original_line_count
        assert "Math.min(400" not in patched
        assert "Math.min(window.innerHeight * 0.8" in patched
        # Every other preserved marker survives.
        for marker in ("onMount", "tasksStore", "handleMouseDown",
                       "handleCmd", "svelte:window", "activeTab"):
            assert marker in patched, f"Lost preservation marker: {marker}"

    async def test_zero_match_raises_and_leaves_file_untouched(self, tmp_path):
        _, workspace_root = _make_repo(tmp_path)
        panel = tmp_path / "dashboard" / "src" / "lib" / "components" / "BottomPanel.svelte"
        before = panel.read_bytes()

        provider = LocalToolProvider(workspace_root=tmp_path)
        with pytest.raises(ValueError, match="search string not found"):
            await provider.call_tool(
                "filesystem_patch",
                {
                    "path": "dashboard/src/lib/components/BottomPanel.svelte",
                    "search": "this-literal-never-appears-xyz",
                    "replace": "whatever",
                },
            )
        assert panel.read_bytes() == before, "File must be unchanged on zero-match"

    async def test_multi_match_raises_and_leaves_file_untouched(self, tmp_path):
        """When the search string matches multiple times, reject with a
        disambiguation hint. A literal like 'lines = [...lines' appears
        twice in BottomPanel.svelte (inside handleCmd)."""
        _, workspace_root = _make_repo(tmp_path)
        panel = tmp_path / "dashboard" / "src" / "lib" / "components" / "BottomPanel.svelte"
        body = panel.read_text(encoding="utf-8")

        # Find a substring that genuinely appears >1 times so the test
        # isn't sensitive to fixture phrasing.
        candidates = ["lines = [...lines", "text: '", "'info'"]
        multi = next(
            (s for s in candidates if body.count(s) >= 2), None
        )
        assert multi is not None, (
            "Fixture changed; no multi-match substring found for this test"
        )

        before = panel.read_bytes()
        provider = LocalToolProvider(workspace_root=tmp_path)
        with pytest.raises(ValueError, match="matched .* times"):
            await provider.call_tool(
                "filesystem_patch",
                {
                    "path": "dashboard/src/lib/components/BottomPanel.svelte",
                    "search": multi,
                    "replace": "REPLACEMENT",
                },
            )
        assert panel.read_bytes() == before

    async def test_blocked_path_rejected(self, tmp_path):
        """Security: filesystem_patch must not allow editing .env."""
        (tmp_path / ".env").write_text("SECRET=hunter2", encoding="utf-8")
        provider = LocalToolProvider(workspace_root=tmp_path)
        with pytest.raises(BlockedPathError):
            await provider.call_tool(
                "filesystem_patch",
                {
                    "path": ".env",
                    "search": "hunter2",
                    "replace": "leaked",
                },
            )


# -- Fix 3: Developer system prompt surgical-edit language --

class TestDeveloperPromptSurgicalEditRules:
    """Fix 3 (RC3): Developer system prompts (first-run and retry) must
    require filesystem_patch + preservation on existing files."""

    def _make_blueprint(self) -> Blueprint:
        return Blueprint(
            task_id="fix-bottom-panel-resize-113",
            target_files=["dashboard/src/lib/components/BottomPanel.svelte"],
            instructions=(
                "Change Math.min(400, ...) to Math.min(window.innerHeight * 0.8, ...)."
            ),
            constraints=["Preserve all existing terminal/SSE functionality"],
            acceptance_criteria=["Panel resizes to ~80% viewport height"],
            summary="Clamp bottom panel height to 80% viewport",
        )

    @patch("src.orchestrator._run_tool_loop")
    @patch("src.orchestrator._get_developer_llm")
    async def test_first_run_prompt_requires_filesystem_patch(
        self, mock_get_llm, mock_tool_loop
    ):
        # Make the LLM + tool loop both safe no-ops.
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_get_llm.return_value = mock_llm

        # Return a response shape + a trace-friendly payload.
        mock_tool_loop.return_value = (
            _make_llm_response("patched"),
            200,
            [{"tool": "filesystem_patch", "success": True, "agent": "developer"}],
        )

        state: GraphState = {
            "trace": [], "memory_writes": [], "tool_calls_log": [],
            "retry_count": 0, "tokens_used": 0,
            "status": WorkflowStatus.BUILDING,
            "blueprint": self._make_blueprint(),
            "generated_code": "", "failure_report": None,
        }
        # Developer runs agentic mode when config carries tools.
        fake_tool = MagicMock()
        fake_tool.name = "filesystem_patch"
        config = {"configurable": {"tools": [fake_tool]}}

        await developer_node(state, config=config)

        # _run_tool_loop(llm_with_tools, messages, tools, ...) -- grab messages.
        call_args = mock_tool_loop.call_args
        messages = call_args.args[1] if call_args.args else call_args.kwargs["messages"]
        system_prompt = messages[0].content

        assert "NEVER rewrite an entire existing file" in system_prompt
        assert "filesystem_patch" in system_prompt
        assert "PRESERVE all existing functionality" in system_prompt
        # Regression guard: the old "include the complete code" instruction
        # was the root cause of #178's rewrite. It must stay out.
        assert "include the complete code in your final response" not in system_prompt

    @patch("src.orchestrator._run_tool_loop")
    @patch("src.orchestrator._get_developer_llm")
    async def test_retry_prompt_also_requires_filesystem_patch(
        self, mock_get_llm, mock_tool_loop
    ):
        """On retry, the stricter 'EDITING RULES (STRICT)' block must also
        require filesystem_patch."""
        from src.agents.qa import FailureReport

        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_get_llm.return_value = mock_llm
        mock_tool_loop.return_value = (
            _make_llm_response("fixed"), 200, [],
        )

        state: GraphState = {
            "trace": [], "memory_writes": [], "tool_calls_log": [],
            "retry_count": 1, "tokens_used": 100,
            "status": WorkflowStatus.BUILDING,
            "blueprint": self._make_blueprint(),
            "generated_code": "",
            "failure_report": FailureReport(
                task_id="fix-bottom-panel-resize-113",
                status="fail",
                tests_passed=5,
                tests_failed=1,
                errors=["Off-by-one in clamp"],
                failed_files=["dashboard/src/lib/components/BottomPanel.svelte"],
                is_architectural=False,
                failure_type="code",
                fix_complexity="trivial",
                exact_fix_hint="Change 400 to window.innerHeight * 0.8",
                recommendation="Use filesystem_patch to adjust the Math.min call",
            ),
        }
        fake_tool = MagicMock()
        fake_tool.name = "filesystem_patch"
        config = {"configurable": {"tools": [fake_tool]}}

        await developer_node(state, config=config)

        call_args = mock_tool_loop.call_args
        messages = call_args.args[1] if call_args.args else call_args.kwargs["messages"]
        system_prompt = messages[0].content

        assert "NEVER rewrite an entire existing file" in system_prompt
        assert "filesystem_patch" in system_prompt
        assert "PRESERVE all existing functionality" in system_prompt


# -- Fix 4: Architect preservation constraints --

class TestArchitectPreservationRules:
    """Fix 4 (RC4): when source files are gathered, the architect prompt
    must include explicit Preservation Rules."""

    @patch("src.orchestrator._get_architect_llm")
    async def test_architect_prompt_includes_preservation_rules(self, mock_get_llm):
        # Architect returns a Blueprint-shaped JSON.
        blueprint_json = json.dumps({
            "task_id": "fix-bottom-panel-resize-113",
            "target_files": ["dashboard/src/lib/components/BottomPanel.svelte"],
            "instructions": (
                "Replace `Math.min(400, startH + (startY - e.clientY))` with "
                "`Math.min(window.innerHeight * 0.8, startH + (startY - e.clientY))`."
            ),
            "constraints": [
                "Preserve the SSE log streaming subscription in onMount",
                "Preserve handleCmd task-creation flow",
                "Preserve tabs and command input",
            ],
            "acceptance_criteria": ["Panel resizes to ~80% viewport height"],
            "summary": "Clamp bottom panel height to 80% viewport",
        })
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response(blueprint_json))
        mock_get_llm.return_value = mock_llm

        source_content = FIXTURE_PATH.read_text(encoding="utf-8")
        state: GraphState = {
            "task_description": TASK_113_DESCRIPTION,
            "gathered_context": [{
                "path": "dashboard/src/lib/components/BottomPanel.svelte",
                "content": source_content,
                "truncated": False,
            }],
            "trace": [], "memory_writes": [],
            "retry_count": 0, "tokens_used": 0,
            "status": WorkflowStatus.PLANNING,
        }
        await architect_node(state)

        # Inspect the messages passed to the LLM.
        ainvoke_args = mock_llm.ainvoke.call_args.args[0]
        system_prompt = ainvoke_args[0].content

        assert "## Preservation Rules" in system_prompt
        assert "filesystem_patch" in system_prompt
        assert "NEVER instruct the Developer to rewrite an entire existing file" in system_prompt
        # The source file itself must be included too.
        assert "# --- FILE: dashboard/src/lib/components/BottomPanel.svelte" in system_prompt
        assert "Math.min(400" in system_prompt


# -- Fix 5: QA scope-creep detection --

class TestQAScopeCreepDetection:
    """Fix 5 (RC5): when gathered_context is available, qa_node must
    inject the original source files and enable scope-creep checks."""

    @patch("src.orchestrator._get_qa_llm")
    async def test_qa_prompt_has_scope_creep_rules_and_original_source(
        self, mock_get_llm
    ):
        # Valid FailureReport JSON so qa_node returns cleanly.
        report_json = json.dumps({
            "task_id": "fix-bottom-panel-resize-113",
            "status": "pass",
            "tests_passed": 4,
            "tests_failed": 0,
            "errors": [],
            "failed_files": [],
            "is_architectural": False,
            "failure_type": None,
            "fix_complexity": None,
            "exact_fix_hint": None,
            "recommendation": "All preserved",
        })
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response(report_json))
        mock_get_llm.return_value = mock_llm

        blueprint = Blueprint(
            task_id="fix-bottom-panel-resize-113",
            target_files=["dashboard/src/lib/components/BottomPanel.svelte"],
            instructions="Change Math.min(400, ...) to Math.min(window.innerHeight * 0.8, ...).",
            constraints=["Preserve all existing terminal/SSE functionality"],
            acceptance_criteria=["Panel resizes to ~80% viewport height"],
            summary="Clamp bottom panel height to 80% viewport",
        )
        source_content = FIXTURE_PATH.read_text(encoding="utf-8")
        state: GraphState = {
            "trace": [], "memory_writes": [], "tool_calls_log": [],
            "retry_count": 0, "tokens_used": 0,
            "status": WorkflowStatus.REVIEWING,
            "blueprint": blueprint,
            "generated_code": (
                "# --- FILE: dashboard/src/lib/components/BottomPanel.svelte ---\n"
                + source_content.replace(
                    "Math.min(400",
                    "Math.min(window.innerHeight * 0.8",
                )
            ),
            "failure_report": None,
            "gathered_context": [{
                "path": "dashboard/src/lib/components/BottomPanel.svelte",
                "content": source_content,
                "truncated": False,
            }],
            "sandbox_result": None,
        }

        await qa_node(state)

        call_args = mock_llm.ainvoke.call_args.args[0]
        system_prompt = call_args[0].content
        user_msg = call_args[1].content

        assert "SCOPE-CREEP DETECTION" in system_prompt
        assert ">20% shorter" in system_prompt
        assert "filesystem_patch" in system_prompt

        assert "# --- ORIGINAL: dashboard/src/lib/components/BottomPanel.svelte ---" in user_msg
        assert "Original Source Files" in user_msg
        # The original must actually be present so the LLM can compare.
        assert "Math.min(400" in user_msg

    @patch("src.orchestrator._get_qa_llm")
    async def test_qa_prompt_omits_scope_creep_when_no_gathered_context(
        self, mock_get_llm
    ):
        """Negative check: creating a brand-new file has no gathered_context
        so the scope-creep block should stay out to avoid false positives."""
        report_json = json.dumps({
            "task_id": "new-file-task",
            "status": "pass",
            "tests_passed": 1,
            "tests_failed": 0,
            "errors": [],
            "failed_files": [],
            "is_architectural": False,
            "failure_type": None,
            "fix_complexity": None,
            "exact_fix_hint": None,
            "recommendation": "ok",
        })
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_llm_response(report_json))
        mock_get_llm.return_value = mock_llm

        state: GraphState = {
            "trace": [], "memory_writes": [], "tool_calls_log": [],
            "retry_count": 0, "tokens_used": 0,
            "status": WorkflowStatus.REVIEWING,
            "blueprint": Blueprint(
                task_id="new-file-task",
                target_files=["greet.py"],
                instructions="Create greet() function",
                constraints=[],
                acceptance_criteria=["Function exists"],
                summary="Add greet helper",
            ),
            "generated_code": "def greet(name): return f'hi {name}'",
            "failure_report": None,
            "gathered_context": [],
            "sandbox_result": None,
        }
        await qa_node(state)
        system_prompt = mock_llm.ainvoke.call_args.args[0][0].content
        assert "SCOPE-CREEP DETECTION" not in system_prompt


# -- Fix 6: Blueprint.summary drives clean PR title --

class TestPRTitleFromSummary:
    """Fix 6 (RC6): publish_code_node uses Blueprint.summary (one-line
    imperative) not blueprint.instructions (multi-line steps)."""

    def _make_state(self, **overrides) -> GraphState:
        blueprint = Blueprint(
            task_id="fix-bottom-panel-resize-113",
            target_files=["dashboard/src/lib/components/BottomPanel.svelte"],
            instructions=(
                "1. Open the file.\n"
                "2. Locate Math.min(400, ...).\n"
                "3. Replace with window.innerHeight * 0.8.\n"
                "4. Verify behavior."
            ),
            constraints=["Preserve terminal"],
            acceptance_criteria=["Panel resizes to ~80%"],
            summary="Clamp bottom panel height to 80% viewport",
        )
        defaults: GraphState = {
            "task_description": TASK_113_DESCRIPTION,
            "blueprint": blueprint,
            "generated_code": "# patched",
            "failure_report": None,
            "status": WorkflowStatus.PASSED,
            "retry_count": 0, "tokens_used": 500,
            "error_message": "",
            "memory_context": [], "memory_writes": [], "trace": [],
            "sandbox_result": None,
            "parsed_files": [{
                "path": "dashboard/src/lib/components/BottomPanel.svelte",
                "content": "// patched content",
            }],
            "tool_calls_log": [],
            "workspace_root": "/tmp/does-not-matter",
            "create_pr": True,
        }
        defaults.update(overrides)
        return defaults

    @patch("src.api.github_prs.github_pr_provider")
    async def test_pr_title_uses_blueprint_summary(self, mock_provider):
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        pr = MagicMock()
        pr.number = 999
        pr.id = "#999"
        mock_provider.create_pr = AsyncMock(return_value=pr)

        state = self._make_state()
        await publish_code_node(state)

        pr_title = mock_provider.create_pr.call_args.kwargs["title"]
        assert pr_title == (
            "feat(fix-bottom-panel-resize-113): "
            "Clamp bottom panel height to 80% viewport"
        )
        # Regression guard: no multi-line instructions or numbered steps.
        assert "\n" not in pr_title
        assert not pr_title.endswith(".")
        assert "1. Open the file" not in pr_title

    @patch("src.api.github_prs.github_pr_provider")
    async def test_pr_title_falls_back_to_task_description_first_line(
        self, mock_provider
    ):
        """When summary is empty, use the first non-marker line of
        task_description rather than the multi-line instructions dump."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        pr = MagicMock()
        pr.number = 1000
        mock_provider.create_pr = AsyncMock(return_value=pr)

        bp = Blueprint(
            task_id="fix-bottom-panel-resize-113",
            target_files=["dashboard/src/lib/components/BottomPanel.svelte"],
            instructions="1. Open the file.\n2. Do things.",
            constraints=[],
            acceptance_criteria=["pass"],
            summary="",  # empty on purpose
        )
        state = self._make_state(
            blueprint=bp,
            task_description=(
                "RELATED_FILES: dashboard/src/lib/components/BottomPanel.svelte\n"
                "Fix the bottom panel resize bug\n"
                "More details below."
            ),
        )
        await publish_code_node(state)

        pr_title = mock_provider.create_pr.call_args.kwargs["title"]
        assert "Fix the bottom panel resize bug" in pr_title
        assert "RELATED_FILES" not in pr_title
        assert "1. Open" not in pr_title
