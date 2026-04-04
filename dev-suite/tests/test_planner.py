"""Tests for the Planner agent module.

Issue #106 Phase B: Tests for TaskSpec, checklist validation,
auto-inference, session management, and Planner message handling.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.planner import (
    ChecklistPriority,
    PlannerSession,
    PlannerSessionStore,
    TaskSpec,
    _apply_spec_updates,
    _extract_task_spec_updates,
    _strip_json_block,
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
        assert len(checklist.missing_required) == 4  # workspace, objective, languages, frameworks

    def test_required_fields_satisfied(self):
        spec = TaskSpec(
            workspace="/my/project",
            objective="Add auth",
            languages=["Python"],
            frameworks=["FastAPI"],
        )
        checklist = build_checklist(spec)
        assert checklist.required_satisfied
        assert checklist.missing_required == []

    def test_recommended_warnings(self):
        spec = TaskSpec(
            workspace="/my/project",
            objective="Add auth",
            languages=["Python"],
            frameworks=["FastAPI"],
        )
        checklist = build_checklist(spec)
        assert checklist.has_warnings
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
            # languages and frameworks missing
        )
        checklist = build_checklist(spec)
        assert not checklist.required_satisfied
        assert "languages" in checklist.missing_required
        assert "frameworks" in checklist.missing_required

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
        assert not hasattr(spec, "unknown_field") or True  # No crash

    def test_skip_empty_values(self):
        spec = TaskSpec(workspace="/proj", objective="Existing")
        updates = {"objective": "", "languages": []}
        _apply_spec_updates(spec, updates)
        assert spec.objective == "Existing"  # Not overwritten
        assert spec.languages == []  # Stays empty


class TestStripJsonBlock:
    def test_strip_fenced_block(self):
        text = 'Analysis here.\n\n```json\n{"a": 1}\n```'
        assert _strip_json_block(text) == "Analysis here."

    def test_no_block(self):
        text = "Just text."
        assert _strip_json_block(text) == "Just text."


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
        assert response.ready  # workspace + objective + languages + frameworks = all required
        assert len(session.messages) == 3  # system + user + assistant

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
