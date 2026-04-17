"""Tests for the Planner agent module.

Issue #106 Phase B: Tests for TaskSpec, checklist validation,
auto-inference, session management, and Planner message handling.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.planner import (
    PlannerSessionStore,
    TaskSpec,
    _apply_spec_updates,
    _extract_task_spec_updates,
    _strip_code_blocks,
    build_checklist,
    create_planner_session,
    infer_workspace_stack,
    send_planner_message,
)

# =========================================================================
# TaskSpec tests
# =========================================================================


class TestTaskSpec:
    def test_empty_spec(self):
        spec = TaskSpec()
        assert spec.workspace == ""
        assert spec.objective == ""
        assert spec.languages == []
        assert spec.frameworks == []

    def test_to_description_minimal(self):
        spec = TaskSpec(objective="Add auth middleware")
        desc = spec.to_description()
        assert desc == "Add auth middleware"

    def test_to_description_full(self):
        spec = TaskSpec(
            objective="Add auth middleware",
            languages=["TypeScript"],
            frameworks=["SvelteKit"],
            output_type="Files",
            acceptance_criteria=["Tests pass", "No regressions"],
            constraints=["Don't modify existing auth"],
            related_files=["src/hooks.server.ts"],
        )
        desc = spec.to_description()
        assert "Add auth middleware" in desc
        assert "TypeScript" in desc
        assert "SvelteKit" in desc
        assert "Files" in desc
        assert "Tests pass" in desc
        assert "Don't modify existing auth" in desc
        assert "src/hooks.server.ts" in desc


# =========================================================================
# Checklist tests
# =========================================================================


class TestChecklist:
    def test_empty_spec_all_unsatisfied(self):
        spec = TaskSpec()
        checklist = build_checklist(spec)
        assert not checklist.required_satisfied
        assert len(checklist.missing_required) == 3  # workspace, objective, languages

    def test_required_fields_satisfied(self):
        spec = TaskSpec(
            workspace="/my/project",
            objective="Add auth",
            languages=["Python"],
        )
        checklist = build_checklist(spec)
        assert checklist.required_satisfied
        assert checklist.missing_required == []

    def test_recommended_warnings(self):
        spec = TaskSpec(
            workspace="/my/project",
            objective="Add auth",
            languages=["Python"],
        )
        checklist = build_checklist(spec)
        assert checklist.has_warnings
        assert "frameworks" in checklist.missing_recommended
        assert "output_type" in checklist.missing_recommended
        assert "acceptance_criteria" in checklist.missing_recommended

    def test_no_warnings_when_recommended_present(self):
        spec = TaskSpec(
            workspace="/my/project",
            objective="Add auth",
            languages=["Python"],
            frameworks=["FastAPI"],
            output_type="Files",
            acceptance_criteria=["Tests pass"],
        )
        checklist = build_checklist(spec)
        assert not checklist.has_warnings

    def test_partial_required(self):
        spec = TaskSpec(
            workspace="/my/project",
            objective="Add auth",
            # languages missing
        )
        checklist = build_checklist(spec)
        assert not checklist.required_satisfied
        assert "languages" in checklist.missing_required

    def test_frameworks_is_recommended_not_required(self):
        """frameworks should not block submission when empty."""
        spec = TaskSpec(
            workspace="/my/project",
            objective="Print triforce",
            languages=["Python"],
            # No frameworks — this is fine for a standalone script
        )
        checklist = build_checklist(spec)
        assert checklist.required_satisfied  # Should be ready!
        assert "frameworks" not in checklist.missing_required
        assert "frameworks" in checklist.missing_recommended

    def test_auto_inferred_flag(self):
        spec = TaskSpec(
            workspace="/my/project",
            languages=["Python"],
            frameworks=["FastAPI"],
        )
        checklist = build_checklist(spec)
        lang_item = next(i for i in checklist.items if i.field == "languages")
        assert lang_item.auto_inferred


# =========================================================================
# Auto-inference tests
# =========================================================================


class TestInferWorkspaceStack:
    def test_empty_directory(self, tmp_path):
        result = infer_workspace_stack(tmp_path)
        assert result["languages"] == []
        assert result["frameworks"] == []

    def test_package_json_javascript(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "dependencies": {"express": "^4.18.0"},
        }))
        result = infer_workspace_stack(tmp_path)
        assert "JavaScript" in result["languages"]
        assert "Express" in result["frameworks"]

    def test_package_json_typescript(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "devDependencies": {"typescript": "^5.0.0"},
            "dependencies": {"react": "^18.0.0"},
        }))
        result = infer_workspace_stack(tmp_path)
        assert "TypeScript" in result["languages"]
        assert "JavaScript" in result["languages"]
        assert "React" in result["frameworks"]

    def test_package_json_sveltekit(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "devDependencies": {
                "typescript": "^5.0.0",
                "@sveltejs/kit": "^2.0.0",
                "svelte": "^5.0.0",
                "@tailwindcss/vite": "^4.0.0",
            },
        }))
        result = infer_workspace_stack(tmp_path)
        assert "TypeScript" in result["languages"]
        assert "SvelteKit" in result["frameworks"]
        assert "Svelte" in result["frameworks"]
        assert "TailwindCSS" in result["frameworks"]

    def test_tsconfig_detects_typescript(self, tmp_path):
        """TypeScript detected even without typescript in deps if tsconfig exists."""
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({"dependencies": {}}))
        tsconfig = tmp_path / "tsconfig.json"
        tsconfig.write_text("{}")
        result = infer_workspace_stack(tmp_path)
        assert "TypeScript" in result["languages"]

    def test_pyproject_toml(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\n'
            'name = "my-api"\n'
            'dependencies = ["fastapi>=0.100", "pydantic>=2.0"]\n'
        )
        result = infer_workspace_stack(tmp_path)
        assert "Python" in result["languages"]
        assert "FastAPI" in result["frameworks"]
        assert "Pydantic" in result["frameworks"]

    def test_pyproject_langgraph(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[dependency-groups]\n'
            'dev = ["langgraph", "langchain", "pytest"]\n'
        )
        result = infer_workspace_stack(tmp_path)
        assert "Python" in result["languages"]
        assert "LangGraph" in result["frameworks"]
        assert "LangChain" in result["frameworks"]
        assert "pytest" in result["frameworks"]

    def test_requirements_txt_fallback(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask>=3.0\n")
        result = infer_workspace_stack(tmp_path)
        assert "Python" in result["languages"]
        # Note: requirements.txt doesn't do framework detection
        assert result["frameworks"] == []

    def test_cargo_toml(self, tmp_path):
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text('[package]\nname = "my-project"\n')
        result = infer_workspace_stack(tmp_path)
        assert "Rust" in result["languages"]

    def test_go_mod(self, tmp_path):
        go_mod = tmp_path / "go.mod"
        go_mod.write_text('module example.com/my-project\n')
        result = infer_workspace_stack(tmp_path)
        assert "Go" in result["languages"]

    def test_multi_language_project(self, tmp_path):
        """Project with both package.json and pyproject.toml."""
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "devDependencies": {"typescript": "^5.0.0", "@sveltejs/kit": "^2.0.0"},
        }))
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\n'
            'dependencies = ["fastapi", "langgraph"]\n'
        )
        result = infer_workspace_stack(tmp_path)
        assert "TypeScript" in result["languages"]
        assert "JavaScript" in result["languages"]
        assert "Python" in result["languages"]
        assert "SvelteKit" in result["frameworks"]
        assert "FastAPI" in result["frameworks"]
        assert "LangGraph" in result["frameworks"]

    def test_invalid_package_json(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text("not valid json")
        result = infer_workspace_stack(tmp_path)
        # Should not crash, just skip
        assert isinstance(result["languages"], list)

    def test_preact_not_detected_as_react(self, tmp_path):
        """Exact matching: preact should NOT trigger React detection."""
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "dependencies": {"preact": "^10.0.0"},
        }))
        result = infer_workspace_stack(tmp_path)
        assert "React" not in result["frameworks"]

    def test_build_gradle_only_detects_java_not_kotlin(self, tmp_path):
        """build.gradle alone should only detect Java, not Kotlin."""
        gradle = tmp_path / "build.gradle"
        gradle.write_text('apply plugin: "java"\n')
        result = infer_workspace_stack(tmp_path)
        assert "Java" in result["languages"]
        assert "Kotlin" not in result["languages"]

    def test_build_gradle_kts_detects_kotlin(self, tmp_path):
        """build.gradle.kts indicates Kotlin."""
        gradle = tmp_path / "build.gradle.kts"
        gradle.write_text('plugins { kotlin("jvm") }\n')
        result = infer_workspace_stack(tmp_path)
        assert "Kotlin" in result["languages"]
        assert "Java" in result["languages"]


# =========================================================================
# JSON extraction tests
# =========================================================================


class TestExtractTaskSpecUpdates:
    def test_fenced_json_block(self):
        text = 'Here is my analysis.\n\n```json\n{"objective": "Add auth", "languages": ["Python"]}\n```'
        updates = _extract_task_spec_updates(text)
        assert updates["objective"] == "Add auth"
        assert updates["languages"] == ["Python"]

    def test_no_json_block(self):
        text = "Just a regular response without JSON."
        updates = _extract_task_spec_updates(text)
        assert updates == {}

    def test_malformed_json(self):
        text = '```json\n{invalid json}\n```'
        updates = _extract_task_spec_updates(text)
        assert updates == {}

    def test_bare_json_object(self):
        text = 'Here is the result.\n{"objective": "Fix bug"}'
        updates = _extract_task_spec_updates(text)
        assert updates["objective"] == "Fix bug"


class TestApplySpecUpdates:
    def test_apply_valid_updates(self):
        spec = TaskSpec(workspace="/proj", objective="Initial")
        updates = {"objective": "Updated", "languages": ["Python"]}
        _apply_spec_updates(spec, updates)
        assert spec.objective == "Updated"
        assert spec.languages == ["Python"]

    def test_never_override_workspace(self):
        spec = TaskSpec(workspace="/safe/path")
        updates = {"workspace": "/evil/path"}
        _apply_spec_updates(spec, updates)
        assert spec.workspace == "/safe/path"

    def test_ignore_unknown_fields(self):
        spec = TaskSpec(workspace="/proj")
        updates = {"unknown_field": "value", "objective": "Real"}
        _apply_spec_updates(spec, updates)
        assert spec.objective == "Real"
        assert not hasattr(spec, "unknown_field")

    def test_skip_empty_values(self):
        spec = TaskSpec(workspace="/proj", objective="Existing")
        updates = {"objective": "", "languages": []}
        _apply_spec_updates(spec, updates)
        assert spec.objective == "Existing"  # Not overwritten
        assert spec.languages == []  # Stays empty

    def test_string_coerced_to_list(self):
        """LLM returns string instead of list — should be coerced."""
        spec = TaskSpec(workspace="/proj")
        updates = {"languages": "Python"}
        _apply_spec_updates(spec, updates)
        assert spec.languages == ["Python"]

    def test_invalid_type_skipped(self):
        """Non-string, non-list value for list field — should be skipped."""
        spec = TaskSpec(workspace="/proj", languages=["Python"])
        updates = {"languages": 42}
        _apply_spec_updates(spec, updates)
        assert spec.languages == ["Python"]  # Unchanged


class TestStripCodeBlocks:
    def test_strip_fenced_block(self):
        text = 'Analysis here.\n\n```json\n{"a": 1}\n```'
        assert _strip_code_blocks(text) == "Analysis here."

    def test_no_block(self):
        text = "Just text."
        assert _strip_code_blocks(text) == "Just text."

    def test_strip_non_json_fenced_block(self):
        """Strips python, bash, or unlabelled fenced blocks too."""
        text = 'Here is code:\n\n```python\nprint("hello")\n```'
        assert _strip_code_blocks(text) == "Here is code:"

    def test_strip_multiple_blocks(self):
        """Strips all fenced blocks, not just the last one."""
        text = 'First:\n\n```json\n{"a": 1}\n```\n\nSecond:\n\n```python\nprint(1)\n```'
        result = _strip_code_blocks(text)
        assert "```" not in result
        assert "First:" in result
        assert "Second:" in result


# =========================================================================
# Session management tests
# =========================================================================


class TestPlannerSession:
    def test_create_session(self):
        session = create_planner_session(
            workspace="/my/project",
            languages=["Python"],
            frameworks=["FastAPI"],
        )
        assert session.task_spec.workspace == "/my/project"
        assert session.task_spec.languages == ["Python"]
        assert session.task_spec.frameworks == ["FastAPI"]
        assert len(session.messages) == 1  # System message
        assert session.messages[0].role == "system"
        assert not session.submitted

    def test_session_expiry(self):
        session = create_planner_session(workspace="/proj")
        session.last_activity = time.time() - 3600  # 1 hour ago
        assert session.is_expired

    def test_session_touch(self):
        session = create_planner_session(workspace="/proj")
        old_time = session.last_activity
        time.sleep(0.01)
        session.touch()
        assert session.last_activity > old_time


class TestPlannerSessionStore:
    def test_create_and_get(self):
        store = PlannerSessionStore()
        session = create_planner_session(workspace="/proj")
        store.create(session)
        retrieved = store.get(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent(self):
        store = PlannerSessionStore()
        assert store.get("nonexistent") is None

    def test_expired_session_removed(self):
        store = PlannerSessionStore()
        session = create_planner_session(workspace="/proj")
        session.last_activity = time.time() - 3600
        store.create(session)
        assert store.get(session.session_id) is None

    def test_remove(self):
        store = PlannerSessionStore()
        session = create_planner_session(workspace="/proj")
        store.create(session)
        assert store.remove(session.session_id)
        assert store.get(session.session_id) is None

    def test_count(self):
        store = PlannerSessionStore()
        s1 = create_planner_session(workspace="/proj1")
        s2 = create_planner_session(workspace="/proj2")
        store.create(s1)
        store.create(s2)
        assert store.count == 2


# =========================================================================
# Planner LLM interaction tests (mocked)
# =========================================================================


class TestSendPlannerMessage:
    @pytest.mark.asyncio
    async def test_message_with_json_response(self):
        """Planner extracts TaskSpec fields from LLM response."""
        session = create_planner_session(
            workspace="/my/project",
            languages=["Python"],
            frameworks=["FastAPI"],
        )

        mock_response = (
            "I see this is a Python/FastAPI project. "
            "Let me set up the task spec.\n\n"
            '```json\n'
            '{"objective": "Add auth middleware", '
            '"acceptance_criteria": ["Tests pass", "No regressions"]}\n'
            '```'
        )

        with patch(
            "src.agents.planner._call_planner_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await send_planner_message(session, "Add auth middleware")

        assert response.task_spec.objective == "Add auth middleware"
        assert "Tests pass" in response.task_spec.acceptance_criteria
        assert response.ready  # workspace + objective + languages = all required
        assert len(session.messages) == 3  # system + user + assistant

    @pytest.mark.asyncio
    async def test_message_with_json_response_no_frameworks(self):
        """Task without frameworks should still be submittable."""
        session = create_planner_session(
            workspace="/my/project",
            languages=["Python"],
            # No frameworks
        )

        mock_response = (
            "Got it, a standalone Python script.\n\n"
            '```json\n'
            '{"objective": "Print the triforce"}\n'
            '```'
        )

        with patch(
            "src.agents.planner._call_planner_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await send_planner_message(session, "Print the triforce")

        assert response.task_spec.objective == "Print the triforce"
        assert response.ready  # frameworks not required

    @pytest.mark.asyncio
    async def test_message_without_json(self):
        """Planner response without JSON block doesn't crash."""
        session = create_planner_session(workspace="/proj")

        with patch(
            "src.agents.planner._call_planner_llm",
            new_callable=AsyncMock,
            return_value="What kind of changes are you looking for?",
        ):
            response = await send_planner_message(session, "Help me")

        assert response.message == "What kind of changes are you looking for?"
        assert not response.ready  # Still missing objective

    @pytest.mark.asyncio
    async def test_minimal_input_warning(self):
        """Minimal objective triggers a warning."""
        session = create_planner_session(
            workspace="/proj",
            languages=["Python"],
            frameworks=["FastAPI"],
        )

        mock_response = '```json\n{"objective": "Fix bug"}\n```'

        with patch(
            "src.agents.planner._call_planner_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await send_planner_message(session, "Fix bug")

        assert any("minimal" in w.lower() for w in response.warnings)

    @pytest.mark.asyncio
    async def test_workspace_never_overridden(self):
        """LLM response cannot override workspace."""
        session = create_planner_session(workspace="/safe/path")

        mock_response = '```json\n{"workspace": "/evil", "objective": "Hack"}\n```'

        with patch(
            "src.agents.planner._call_planner_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            response = await send_planner_message(session, "Override workspace")

        assert response.task_spec.workspace == "/safe/path"


# =========================================================================
# GitHub pre-fetch tests (Issue #193 PR 2)
# =========================================================================


class TestPlannerGitHubPrefetch:
    @pytest.mark.asyncio
    async def test_prefetch_populates_github_context(self, monkeypatch):
        """User message with issue ref → github_context populated before LLM call."""
        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        monkeypatch.setenv("GITHUB_OWNER", "acme")
        monkeypatch.setenv("GITHUB_REPO", "widgets")

        session = create_planner_session(
            workspace="/proj", languages=["Python"],
        )

        fetched = {
            "path": "github://acme/widgets/issues/42",
            "content": "Issue #42: Broken login\n\nState: open",
            "truncated": False,
            "source": "github_issue",
        }
        captured_messages: list[list[dict]] = []

        async def fake_fetch(*args, **kwargs):
            return [fetched]

        async def fake_llm(model_name, messages, **kwargs):
            # Capture the messages the LLM would have received
            captured_messages.append(messages)
            return '```json\n{"objective": "Fix login"}\n```'

        with patch(
            "src.agents.planner.fetch_refs_as_context_items",
            side_effect=fake_fetch,
        ), patch(
            "src.agents.planner._call_planner_llm",
            side_effect=fake_llm,
        ):
            response = await send_planner_message(
                session, "Please fix issue #42 — login is broken"
            )

        # github_context captured from the fetch
        assert len(response.task_spec.github_context) == 1
        assert response.task_spec.github_context[0]["path"] == \
            "github://acme/widgets/issues/42"

        # The pre-fetch block reached the LLM as a system message
        assert captured_messages, "LLM must have been called"
        system_messages = [
            m["content"] for m in captured_messages[0] if m["role"] == "system"
        ]
        assert any(
            "github://acme/widgets/issues/42" in s for s in system_messages
        )

    @pytest.mark.asyncio
    async def test_prefetch_skipped_without_token(self, monkeypatch):
        """No GITHUB_TOKEN → no pre-fetch attempted."""
        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        session = create_planner_session(workspace="/proj", languages=["Python"])

        fetch_called = False

        async def fake_fetch(*args, **kwargs):
            nonlocal fetch_called
            fetch_called = True
            return []

        async def fake_llm(model_name, messages, **kwargs):
            return '```json\n{"objective": "Fix login"}\n```'

        with patch(
            "src.agents.planner.fetch_refs_as_context_items",
            side_effect=fake_fetch,
        ), patch(
            "src.agents.planner._call_planner_llm",
            side_effect=fake_llm,
        ):
            await send_planner_message(session, "Fix issue #42")

        assert not fetch_called
        assert session.task_spec.github_context == []

    @pytest.mark.asyncio
    async def test_prefetch_dedupes_across_turns(self, monkeypatch):
        """Mentioning the same ref twice in the session only stores it once."""
        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        monkeypatch.setenv("GITHUB_OWNER", "acme")
        monkeypatch.setenv("GITHUB_REPO", "widgets")

        session = create_planner_session(workspace="/proj", languages=["Python"])

        fetched = {
            "path": "github://acme/widgets/issues/42",
            "content": "Issue #42: Broken login",
            "truncated": False,
            "source": "github_issue",
        }

        async def fake_fetch(*args, **kwargs):
            return [fetched]

        async def fake_llm(model_name, messages, **kwargs):
            return '```json\n{}\n```'

        with patch(
            "src.agents.planner.fetch_refs_as_context_items",
            side_effect=fake_fetch,
        ), patch(
            "src.agents.planner._call_planner_llm",
            side_effect=fake_llm,
        ):
            await send_planner_message(session, "Fix issue #42")
            await send_planner_message(session, "Also address issue #42 again")

        assert len(session.task_spec.github_context) == 1

    @pytest.mark.asyncio
    async def test_llm_cannot_override_github_context(self, monkeypatch):
        """Extracted JSON cannot set or clobber github_context field."""
        from src.agents.planner import (
            TaskSpec,
            _apply_spec_updates,
        )

        ts = TaskSpec(
            workspace="/p",
            github_context=[{"path": "github://a/b/issues/1", "content": "real"}],
        )
        _apply_spec_updates(
            ts,
            {"github_context": [{"path": "github://evil/x/issues/9", "content": "fake"}]},
        )
        assert ts.github_context == [
            {"path": "github://a/b/issues/1", "content": "real"}
        ]

    @pytest.mark.asyncio
    async def test_prefetch_failure_does_not_break_message(self, monkeypatch):
        """Network errors during pre-fetch are swallowed; user turn still succeeds."""
        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        session = create_planner_session(workspace="/proj", languages=["Python"])

        async def boom(*args, **kwargs):
            raise RuntimeError("network down")

        async def fake_llm(model_name, messages, **kwargs):
            return '```json\n{"objective": "Fix"}\n```'

        with patch(
            "src.agents.planner.fetch_refs_as_context_items",
            side_effect=boom,
        ), patch(
            "src.agents.planner._call_planner_llm",
            side_effect=fake_llm,
        ):
            response = await send_planner_message(session, "Fix #42")

        assert response.task_spec.objective == "Fix"
        assert session.task_spec.github_context == []


# =========================================================================
# System-prompt reconciliation with pre-fetched GitHub context (Issue #193)
# =========================================================================


class TestPlannerSystemPromptReconciliation:
    """Regression tests for the bug where the Planner's system prompt
    told the LLM it had "ZERO tool access" even when the orchestrator
    had pre-fetched GitHub issue/PR bodies. See issue #193 AC #3.
    """

    @pytest.mark.asyncio
    async def test_prompt_does_not_claim_zero_tool_access_when_context_present(
        self, monkeypatch,
    ):
        """System messages sent to the LLM must not tell it it has ZERO
        tool access once github_context is populated, and must include
        the PRE-FETCHED GITHUB CONTEXT marker so the LLM knows to use
        the injected data instead of apologizing about missing tools.
        """
        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        monkeypatch.setenv("GITHUB_OWNER", "Abernaughty")
        monkeypatch.setenv("GITHUB_REPO", "agent-dev")

        session = create_planner_session(workspace="/proj", languages=["Python"])

        fetched = {
            "path": "github://Abernaughty/agent-dev/issues/113",
            "content": (
                "Issue #113: Fix the planner\n\nState: open\n\n"
                "Body: the planner needs real GitHub context"
            ),
            "truncated": False,
            "source": "github_issue",
        }
        captured: list[list[dict]] = []

        async def fake_fetch(*args, **kwargs):
            return [fetched]

        async def fake_llm(model_name, messages, **kwargs):
            captured.append(messages)
            return '```json\n{"objective": "Fix the planner"}\n```'

        with patch(
            "src.agents.planner.fetch_refs_as_context_items",
            side_effect=fake_fetch,
        ), patch(
            "src.agents.planner._call_planner_llm",
            side_effect=fake_llm,
        ):
            await send_planner_message(
                session,
                "Review and implement the fix for GitHub Issue #113.",
            )

        assert captured, "LLM must have been called"
        system_content = "\n".join(
            m["content"] for m in captured[0] if m["role"] == "system"
        )

        # The self-denial phrasing is gone.
        assert "ZERO tool access" not in system_content

        # The LLM is explicitly told not to claim no GitHub access when
        # context is present, and the pre-fetched block is clearly marked.
        assert "PRE-FETCHED GITHUB CONTEXT" in system_content
        assert "github://Abernaughty/agent-dev/issues/113" in system_content

    def test_context_block_has_start_and_end_markers(self):
        """The pre-fetched block uses explicit start/end markers that
        match the phrasing the system prompt tells the LLM to look for.
        """
        from src.agents.planner import _format_github_context_block

        block = _format_github_context_block([
            {
                "path": "github://acme/widgets/issues/42",
                "content": "Body text",
                "source": "github_issue",
            }
        ])

        assert "=== PRE-FETCHED GITHUB CONTEXT ===" in block
        assert "=== END PRE-FETCHED GITHUB CONTEXT ===" in block
        assert "github://acme/widgets/issues/42" in block
        assert "Body text" in block

    def test_context_block_empty_when_no_items_have_content(self):
        """Empty or malformed context items produce no block at all — we
        don't want to emit markers with no body and confuse the LLM.
        """
        from src.agents.planner import _format_github_context_block

        assert _format_github_context_block([]) == ""
        assert _format_github_context_block([{"path": "x", "content": ""}]) == ""
        assert _format_github_context_block(["not-a-dict"]) == ""  # type: ignore[list-item]

    @pytest.mark.asyncio
    async def test_warns_when_refs_detected_but_no_token(
        self, monkeypatch, caplog,
    ):
        """Missing GITHUB_TOKEN should log a warning when the user's
        message actually contained refs, so operators can diagnose why
        pre-fetch silently returned nothing.
        """
        import logging as _logging

        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GITHUB_OWNER", "Abernaughty")
        monkeypatch.setenv("GITHUB_REPO", "agent-dev")

        session = create_planner_session(workspace="/proj", languages=["Python"])

        async def fake_llm(model_name, messages, **kwargs):
            return '```json\n{}\n```'

        with caplog.at_level(_logging.WARNING, logger="src.agents.planner"):
            with patch(
                "src.agents.planner._call_planner_llm",
                side_effect=fake_llm,
            ):
                await send_planner_message(session, "Fix issue #113")

        assert any(
            "GITHUB_TOKEN is not set" in record.message
            for record in caplog.records
        )


# =========================================================================
# Session-level github_repo plumbing + git-remote auto-detect (Issue #193)
# =========================================================================


class TestPlannerGithubRepoPlumbing:
    """Issue #193 AC #3a-#3c: the Planner must resolve same-repo refs
    like "Issue #113" using either an explicitly-passed github_repo
    (from the dashboard repo picker) or one auto-detected by parsing
    the workspace's `.git/config` — not just env vars.
    """

    def test_explicit_github_repo_stored_on_session(self, tmp_path):
        """Passing github_repo to create_planner_session sticks it on
        the session for downstream pre-fetch resolution.
        """
        from src.agents.planner import create_planner_session

        session = create_planner_session(
            workspace=str(tmp_path),
            github_repo="Abernaughty/agent-dev",
        )
        assert session.github_repo == "Abernaughty/agent-dev"

    def test_explicit_repo_wins_over_auto_detect(self, tmp_path):
        """Explicit github_repo must win over the auto-detect so users
        can override what git says (fork workflows, monorepos, etc.).
        """
        from src.agents.planner import create_planner_session

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            '\turl = https://github.com/wrong/repo.git\n',
            encoding="utf-8",
        )
        session = create_planner_session(
            workspace=str(tmp_path),
            github_repo="right/repo",
        )
        assert session.github_repo == "right/repo"

    def test_auto_detect_https_remote(self, tmp_path):
        """HTTPS `remote.origin.url` with .git suffix parses cleanly."""
        from src.agents.planner import create_planner_session

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[core]\n'
            '\trepositoryformatversion = 0\n'
            '[remote "origin"]\n'
            '\turl = https://github.com/Abernaughty/agent-dev.git\n'
            '\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
            encoding="utf-8",
        )
        session = create_planner_session(workspace=str(tmp_path))
        assert session.github_repo == "Abernaughty/agent-dev"

    def test_auto_detect_ssh_remote(self, tmp_path):
        """SSH `git@github.com:owner/repo.git` parses cleanly too."""
        from src.agents.planner import create_planner_session

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            '\turl = git@github.com:Abernaughty/agent-dev.git\n',
            encoding="utf-8",
        )
        session = create_planner_session(workspace=str(tmp_path))
        assert session.github_repo == "Abernaughty/agent-dev"

    def test_auto_detect_handles_no_dot_git_suffix(self, tmp_path):
        """Some clones omit the `.git` suffix in the remote URL."""
        from src.agents.planner import create_planner_session

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            '\turl = https://github.com/Abernaughty/agent-dev\n',
            encoding="utf-8",
        )
        session = create_planner_session(workspace=str(tmp_path))
        assert session.github_repo == "Abernaughty/agent-dev"

    def test_auto_detect_ignores_non_github_remotes(self, tmp_path):
        """Self-hosted Gitea/GitLab remotes don't trigger false positives."""
        from src.agents.planner import create_planner_session

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            '\turl = https://gitlab.example.com/team/proj.git\n',
            encoding="utf-8",
        )
        session = create_planner_session(workspace=str(tmp_path))
        assert session.github_repo is None

    def test_auto_detect_skips_non_origin_remote(self, tmp_path):
        """Only the `origin` remote matters; other remotes are ignored."""
        from src.agents.planner import create_planner_session

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "upstream"]\n'
            '\turl = https://github.com/Abernaughty/agent-dev.git\n'
            '[remote "origin"]\n'
            '\turl = https://github.com/forkuser/agent-dev.git\n',
            encoding="utf-8",
        )
        session = create_planner_session(workspace=str(tmp_path))
        assert session.github_repo == "forkuser/agent-dev"

    def test_auto_detect_returns_none_when_no_git_dir(self, tmp_path):
        """Plain directories without a .git folder just yield None."""
        from src.agents.planner import create_planner_session

        session = create_planner_session(workspace=str(tmp_path))
        assert session.github_repo is None

    @pytest.mark.asyncio
    async def test_session_github_repo_used_for_same_repo_refs(
        self, tmp_path, monkeypatch,
    ):
        """The session's github_repo resolves "Issue #113" even when
        GITHUB_OWNER/GITHUB_REPO env vars are unset.
        """
        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        monkeypatch.delenv("GITHUB_OWNER", raising=False)
        monkeypatch.delenv("GITHUB_REPO", raising=False)

        session = create_planner_session(
            workspace=str(tmp_path),
            github_repo="Abernaughty/agent-dev",
        )

        captured_kwargs: dict = {}

        async def fake_fetch(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return [{
                "path": "github://Abernaughty/agent-dev/issues/113",
                "content": "Issue #113 body",
                "truncated": False,
                "source": "github_issue",
            }]

        async def fake_llm(model_name, messages, **kwargs):
            return '```json\n{}\n```'

        with patch(
            "src.agents.planner.fetch_refs_as_context_items",
            side_effect=fake_fetch,
        ), patch(
            "src.agents.planner._call_planner_llm",
            side_effect=fake_llm,
        ):
            await send_planner_message(
                session, "Review and implement the fix for Issue #113."
            )

        assert captured_kwargs.get("default_owner") == "Abernaughty"
        assert captured_kwargs.get("default_repo") == "agent-dev"
        assert any(
            item["path"] == "github://Abernaughty/agent-dev/issues/113"
            for item in session.task_spec.github_context
        )

    @pytest.mark.asyncio
    async def test_env_vars_are_fallback_when_session_repo_missing(
        self, tmp_path, monkeypatch,
    ):
        """Env vars still work as a fallback for CLI / headless use."""
        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        monkeypatch.setenv("GITHUB_OWNER", "envowner")
        monkeypatch.setenv("GITHUB_REPO", "envrepo")

        session = create_planner_session(workspace=str(tmp_path))
        assert session.github_repo is None  # no git dir, no explicit

        captured_kwargs: dict = {}

        async def fake_fetch(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return []

        async def fake_llm(model_name, messages, **kwargs):
            return '```json\n{}\n```'

        with patch(
            "src.agents.planner.fetch_refs_as_context_items",
            side_effect=fake_fetch,
        ), patch(
            "src.agents.planner._call_planner_llm",
            side_effect=fake_llm,
        ):
            await send_planner_message(session, "Fix issue #7")

        assert captured_kwargs.get("default_owner") == "envowner"
        assert captured_kwargs.get("default_repo") == "envrepo"

    @pytest.mark.asyncio
    async def test_warns_when_hash_ref_present_but_no_repo_configured(
        self, tmp_path, monkeypatch, caplog,
    ):
        """The loose `#N` heuristic catches the silent-drop case so
        operators see a breadcrumb pointing at the missing config.
        """
        import logging as _logging

        from src.agents.planner import create_planner_session, send_planner_message

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        monkeypatch.delenv("GITHUB_OWNER", raising=False)
        monkeypatch.delenv("GITHUB_REPO", raising=False)

        session = create_planner_session(workspace=str(tmp_path))
        assert session.github_repo is None

        async def fake_llm(model_name, messages, **kwargs):
            return '```json\n{}\n```'

        with caplog.at_level(_logging.WARNING, logger="src.agents.planner"):
            with patch(
                "src.agents.planner._call_planner_llm",
                side_effect=fake_llm,
            ):
                # "Issue #113" — extract_github_refs returns [] because
                # no default_owner/repo, but the loose heuristic fires.
                await send_planner_message(session, "Please look at Issue #113")

        assert any(
            "no GitHub repo is configured" in record.message
            for record in caplog.records
        )


# =========================================================================
# Anti-hallucination system prompt (Issue #193 AC #3d)
# =========================================================================


class TestPlannerAntiHallucination:
    def test_system_prompt_forbids_inventing_issue_contents(self):
        """The prompt must explicitly tell the LLM not to invent issue
        contents when the pre-fetched block is absent — this was the
        failure mode from the first manual smoke where the LLM made up
        a fake #113 body.
        """
        from src.agents.planner import _PLANNER_SYSTEM_PROMPT

        prompt = _PLANNER_SYSTEM_PROMPT.lower()
        # Mentions the marker so the LLM can check for it
        assert "pre-fetched github context" in prompt
        # Explicitly forbids invention
        assert "must not invent" in prompt or "must not" in prompt
        assert "invent" in prompt or "guess" in prompt


# =========================================================================
# Planner read-only filesystem tool access
# =========================================================================


class TestPlannerReadOnlyTools:
    """The Planner gets the same READONLY_TOOLS bound to its LLM as the
    Architect's Phase 2 — so it can look up file paths / read configs
    itself instead of asking the user, matching the Architect's reach.
    """

    def test_system_prompt_advertises_filesystem_tools(self):
        """The system prompt must tell the LLM it can use tools so it
        actually uses them instead of asking the user for file paths.
        """
        from src.agents.planner import _PLANNER_SYSTEM_PROMPT

        prompt = _PLANNER_SYSTEM_PROMPT.lower()
        assert "filesystem_list" in prompt
        assert "filesystem_read" in prompt
        # Pushes the LLM toward using the tools rather than asking
        assert "do not ask the user" in prompt or "avoid asking" in prompt

    def test_get_planner_readonly_tools_returns_empty_on_no_workspace(self):
        """No workspace → no tools (nothing to scope a provider to)."""
        from src.agents.planner import _get_planner_readonly_tools

        assert _get_planner_readonly_tools("") == []
        assert _get_planner_readonly_tools(None) == []  # type: ignore[arg-type]

    def test_get_planner_readonly_tools_returns_empty_on_missing_config(
        self, tmp_path, monkeypatch,
    ):
        """No mcp-config.json → graceful fallback to zero tools."""
        from src.agents.planner import _get_planner_readonly_tools

        # Point MCP config at a nonexistent file
        monkeypatch.setenv("MCP_CONFIG_PATH", str(tmp_path / "nope.json"))
        assert _get_planner_readonly_tools(str(tmp_path)) == []

    def test_get_planner_readonly_tools_passes_readonly_filter(
        self, tmp_path, monkeypatch,
    ):
        """The helper must call get_tools with tool_filter=READONLY_TOOLS
        so write tools never reach the Planner LLM. Mocks the provider
        stack so we assert the contract, not the provider internals.
        """
        from unittest.mock import MagicMock

        from src.agents.planner import _get_planner_readonly_tools
        from src.tools.mcp_bridge import READONLY_TOOLS

        cfg = tmp_path / "mcp-config.json"
        cfg.write_text('{"servers": {}}', encoding="utf-8")
        monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))

        fake_tools = [MagicMock(name="filesystem_read")]
        get_tools_mock = MagicMock(return_value=fake_tools)

        with patch("src.tools.mcp_bridge.get_tools", get_tools_mock), \
             patch("src.tools.create_provider", MagicMock()), \
             patch("src.tools.load_mcp_config", MagicMock()):
            tools = _get_planner_readonly_tools(str(tmp_path))

        assert tools == fake_tools
        # The critical assertion: filter was passed so writes cannot leak
        assert get_tools_mock.called
        _, kwargs = get_tools_mock.call_args
        assert kwargs.get("tool_filter") is READONLY_TOOLS

    @pytest.mark.asyncio
    async def test_send_planner_message_passes_tools_to_llm(
        self, tmp_path, monkeypatch,
    ):
        """send_planner_message fetches tools and forwards them to the
        LLM call path — even if there are none, the kwarg is passed.
        """
        from src.agents.planner import create_planner_session, send_planner_message

        captured_kwargs: dict = {}

        async def fake_llm(model_name, messages, **kwargs):
            captured_kwargs.update(kwargs)
            return '```json\n{}\n```'

        session = create_planner_session(workspace=str(tmp_path))

        with patch(
            "src.agents.planner._call_planner_llm",
            side_effect=fake_llm,
        ):
            await send_planner_message(session, "Add auth middleware")

        # tools kwarg was forwarded (value is [] when no provider —
        # proves the wire is in place without needing a real provider).
        assert "tools" in captured_kwargs
        assert isinstance(captured_kwargs["tools"], list)

    @pytest.mark.asyncio
    async def test_invoke_with_optional_tools_bypasses_loop_when_no_tools(
        self,
    ):
        """When tools=[] or None, skip bind_tools + loop and call the
        LLM once directly. Zero added latency for no-tool scenarios.
        """
        from src.agents.planner import _invoke_with_optional_tools

        llm = AsyncMock()
        llm.bind_tools = AsyncMock()  # should NOT be called
        mock_response = AsyncMock()
        mock_response.content = "plain response"
        llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await _invoke_with_optional_tools(llm, [], tools=None)
        assert result == "plain response"
        llm.bind_tools.assert_not_called()
        llm.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invoke_with_optional_tools_runs_loop_when_tools_present(
        self, monkeypatch,
    ):
        """When tools are provided, bind_tools + _run_tool_loop runs.

        The Planner opts into return_messages=True so the loop returns a
        4-tuple (response, tokens, log, messages). When the final
        response already has text, we just return it — no wrap-up.
        """
        from src.agents.planner import _invoke_with_optional_tools

        async def fake_loop(llm_with_tools, messages, tools, **kwargs):
            assert kwargs.get("return_messages") is True, (
                "Planner must opt into return_messages so the wrap-up "
                "can thread real tool results on exhaustion."
            )
            mock_resp = AsyncMock()
            mock_resp.content = "loop response"
            return mock_resp, 0, [], list(messages)

        monkeypatch.setattr(
            "src.orchestrator._run_tool_loop", fake_loop,
        )

        llm = AsyncMock()
        bound_llm = AsyncMock()
        llm.bind_tools = lambda _tools: bound_llm  # sync method

        fake_tools = [AsyncMock(name="fake_tool_obj")]
        result = await _invoke_with_optional_tools(llm, [], tools=fake_tools)
        assert result == "loop response"

    @pytest.mark.asyncio
    async def test_invoke_with_optional_tools_falls_back_on_loop_error(
        self, monkeypatch,
    ):
        """If the tool loop raises, fall back to a single no-tool call
        so the Planner turn still completes (graceful degradation).
        """
        from src.agents.planner import _invoke_with_optional_tools

        async def exploding_loop(*args, **kwargs):
            raise RuntimeError("provider crashed")

        monkeypatch.setattr(
            "src.orchestrator._run_tool_loop", exploding_loop,
        )

        llm = AsyncMock()
        llm.bind_tools = lambda _tools: llm
        fallback_response = AsyncMock()
        fallback_response.content = "fallback response"
        llm.ainvoke = AsyncMock(return_value=fallback_response)

        fake_tools = [AsyncMock(name="fake_tool_obj")]
        result = await _invoke_with_optional_tools(llm, [], tools=fake_tools)
        assert result == "fallback response"
        llm.ainvoke.assert_awaited()

    @pytest.mark.asyncio
    async def test_wrap_up_threads_real_loop_messages_not_originals(
        self, monkeypatch,
    ):
        """Hallucination regression from the PR #201 smoke: wrap-up used
        to call the unbound LLM with only the ORIGINAL system+user
        messages, so the LLM had zero real filesystem info and
        fabricated paths (`BottomPanel.jsx`) + `<tool_call>` XML.

        Fix: pass the loop's accumulated messages (real tool results +
        assistant responses) to the wrap-up so the LLM has grounded
        context.
        """
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        from src.agents.planner import _invoke_with_optional_tools

        original_messages = [
            SystemMessage(content="You are the Planner..."),
            HumanMessage(content="Fix issue #113"),
        ]

        # Simulate what _run_tool_loop accumulated: original + 1 tool
        # call round-trip + final unresolved tool_use.
        fake_tool_call_msg = AIMessage(
            content="",
            tool_calls=[{
                "id": "call_1",
                "name": "filesystem_list",
                "args": {"path": "dashboard/src/lib"},
            }],
        )
        fake_tool_result_msg = ToolMessage(
            content="components/\nrouter/\nstores/",
            tool_call_id="call_1",
        )
        # The real loop returns a LangChain AIMessage — using a raw
        # AsyncMock here would sneak past the isinstance check in
        # `_is_unresolved_tail` and never get dropped, masking the bug.
        exhausted_response = AIMessage(content=[
            {
                "id": "toolu_xyz",
                "input": {"__arg1": "dashboard/src/lib/components"},
                "name": "filesystem_list",
                "type": "tool_use",
            },
        ])
        loop_final_messages = [
            *original_messages,
            fake_tool_call_msg,
            fake_tool_result_msg,
            exhausted_response,  # unresolved tail — must be dropped
        ]

        async def fake_loop(llm_with_tools, messages, tools, **kwargs):
            return exhausted_response, 0, [], loop_final_messages

        monkeypatch.setattr("src.orchestrator._run_tool_loop", fake_loop)

        wrap_up_response = AsyncMock()
        wrap_up_response.content = (
            "Here's the spec — found components/ under dashboard/src/lib"
        )
        captured_wrap_up_messages: list = []

        async def capturing_ainvoke(messages):
            captured_wrap_up_messages.extend(messages)
            return wrap_up_response

        llm = AsyncMock()
        llm.bind_tools = lambda _tools: llm
        llm.ainvoke = capturing_ainvoke

        fake_tools = [AsyncMock(name="filesystem_list")]
        result = await _invoke_with_optional_tools(
            llm, original_messages, tools=fake_tools,
        )
        assert "components/" in result

        # Real tool result must have reached the wrap-up call.
        content_strs = [
            str(getattr(m, "content", "")) for m in captured_wrap_up_messages
        ]
        assert any("components/\nrouter/" in c for c in content_strs), (
            "Wrap-up LLM must see the real tool results, not just the "
            "original messages — otherwise it fabricates."
        )

        # Unresolved tool_use tail must NOT be in the wrap-up messages
        # (Anthropic would reject unpaired tool_use without tool_result).
        assert exhausted_response not in captured_wrap_up_messages

        # Explicit no-tools instruction must be appended as the final
        # user message so the conversation ends on a user turn AND the
        # LLM won't fabricate fake tool calls.
        last_msg = captured_wrap_up_messages[-1]
        assert isinstance(last_msg, HumanMessage)
        nudge_text = str(last_msg.content).lower()
        assert "do not call" in nudge_text or "not call" in nudge_text
        assert "tool_call" in nudge_text  # forbids the XML tag the user saw
        assert "invent" in nudge_text or "fabricate" in nudge_text or \
               "do not invent" in nudge_text


class TestIsUnresolvedTail:
    """Unit coverage for the helper that detects a trailing assistant
    message whose tool_use blocks weren't paired with tool_result
    messages — needed before a wrap-up call so the sequence is valid
    for Anthropic's paired-block requirement.
    """

    def test_empty_sequence(self):
        from src.agents.planner import _is_unresolved_tail

        assert _is_unresolved_tail([]) is False

    def test_plain_assistant_text_is_resolved(self):
        from langchain_core.messages import AIMessage

        from src.agents.planner import _is_unresolved_tail

        assert _is_unresolved_tail([AIMessage(content="hello")]) is False

    def test_assistant_with_tool_calls_is_unresolved(self):
        from langchain_core.messages import AIMessage

        from src.agents.planner import _is_unresolved_tail

        msg = AIMessage(
            content="",
            tool_calls=[{"id": "x", "name": "fs_list", "args": {}}],
        )
        assert _is_unresolved_tail([msg]) is True

    def test_anthropic_tool_use_block_is_unresolved(self):
        from langchain_core.messages import AIMessage

        from src.agents.planner import _is_unresolved_tail

        msg = AIMessage(content=[
            {"id": "x", "type": "tool_use", "name": "fs", "input": {}},
        ])
        assert _is_unresolved_tail([msg]) is True

    def test_mixed_text_and_tool_use_is_unresolved(self):
        """If the assistant emitted text + tool_use, the tool_use still
        needs a paired tool_result; the whole message is unresolved.
        """
        from langchain_core.messages import AIMessage

        from src.agents.planner import _is_unresolved_tail

        msg = AIMessage(content=[
            {"type": "text", "text": "Let me check..."},
            {"id": "x", "type": "tool_use", "name": "fs", "input": {}},
        ])
        assert _is_unresolved_tail([msg]) is True

    def test_trailing_tool_message_is_unresolved(self):
        from langchain_core.messages import ToolMessage

        from src.agents.planner import _is_unresolved_tail

        msg = ToolMessage(content="result", tool_call_id="x")
        assert _is_unresolved_tail([msg]) is True


class TestExtractTextFromContent:
    """Regression coverage for the raw-dict-dump bug: when Anthropic
    returns a list of content blocks that are all tool_use (no text),
    we must render empty string, not `str(content)`.
    """

    def test_string_content_returned_as_is(self):
        from src.agents.planner import _extract_text_from_content

        assert _extract_text_from_content("hello") == "hello"

    def test_list_with_text_blocks_joins_them(self):
        from src.agents.planner import _extract_text_from_content

        content = [
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ]
        assert _extract_text_from_content(content) == "part one\npart two"

    def test_list_with_only_tool_use_returns_empty(self):
        """This was the bug the user hit — max-turns exhaustion left
        Anthropic's response as a pure tool_use block, which used to
        render as the raw dict string in the chat UI.
        """
        from src.agents.planner import _extract_text_from_content

        content = [
            {
                "id": "toolu_xyz",
                "input": {"__arg1": "dashboard/src/lib"},
                "name": "filesystem_list",
                "type": "tool_use",
            },
        ]
        assert _extract_text_from_content(content) == ""

    def test_mixed_text_and_tool_use_returns_only_text(self):
        from src.agents.planner import _extract_text_from_content

        content = [
            {"type": "text", "text": "Let me check the filesystem."},
            {"type": "tool_use", "name": "filesystem_list", "input": {}},
        ]
        assert _extract_text_from_content(content) == (
            "Let me check the filesystem."
        )

    def test_empty_list_returns_empty_string(self):
        from src.agents.planner import _extract_text_from_content

        assert _extract_text_from_content([]) == ""
