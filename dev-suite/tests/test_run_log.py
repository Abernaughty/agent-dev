"""Tests for CLI run-log JSON output (issue #31).

Validates that _write_run_log() produces correctly structured JSON
files that the SvelteKit dashboard can consume.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cli import _write_run_log
from src.orchestrator import AgentState, WorkflowStatus
from src.agents.architect import Blueprint


# -- Fixtures --


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace directory."""
    return str(tmp_path)


@pytest.fixture
def sample_blueprint():
    """A realistic Blueprint for testing."""
    return Blueprint(
        task_id="test-auth-module",
        target_files=["src/auth.py", "tests/test_auth.py"],
        instructions="Implement JWT-based authentication middleware.",
        constraints=["Use PyJWT library", "Token expiry: 15 minutes"],
        acceptance_criteria=["All auth tests pass", "Invalid tokens return 401"],
    )


@pytest.fixture
def passed_state(sample_blueprint):
    """An AgentState representing a successful run."""
    return AgentState(
        task_description="Build auth module",
        blueprint=sample_blueprint,
        generated_code="# generated code here",
        status=WorkflowStatus.PASSED,
        retry_count=1,
        tokens_used=3200,
        trace=["architect: planning", "developer: building", "qa: passed"],
    )


@pytest.fixture
def failed_state():
    """An AgentState representing a failed run."""
    return AgentState(
        task_description="Build broken feature",
        status=WorkflowStatus.FAILED,
        retry_count=3,
        tokens_used=48000,
        error_message="Max retries exhausted",
        trace=["architect: planning", "developer: building", "qa: failed"],
    )


# -- Tests --


class TestWriteRunLog:
    """Tests for the _write_run_log function."""

    def test_creates_runs_directory(self, workspace, passed_state):
        """runs/ directory is auto-created if missing."""
        runs_dir = Path(workspace) / "runs"
        assert not runs_dir.exists()

        _write_run_log(passed_state, 5.2, "Build auth module", workspace)

        assert runs_dir.is_dir()

    def test_writes_json_file(self, workspace, passed_state):
        """A JSON file is written with the expected naming pattern."""
        _write_run_log(passed_state, 5.2, "Build auth module", workspace)

        runs_dir = Path(workspace) / "runs"
        json_files = list(runs_dir.glob("run_*.json"))
        assert len(json_files) == 1
        assert json_files[0].name.startswith("run_")
        assert json_files[0].name.endswith(".json")

    def test_json_schema_passed_run(self, workspace, passed_state):
        """JSON contains all expected fields for a passed run."""
        _write_run_log(passed_state, 5.2, "Build auth module", workspace)

        runs_dir = Path(workspace) / "runs"
        json_file = list(runs_dir.glob("run_*.json"))[0]
        data = json.loads(json_file.read_text())

        # Required top-level fields
        assert data["task"] == "Build auth module"
        assert data["status"] == "passed"
        assert data["tokens_used"] == 3200
        assert data["retry_count"] == 1
        assert data["elapsed_seconds"] == 5.2
        assert isinstance(data["estimated_cost"], float)
        assert data["error_message"] is None
        assert isinstance(data["timestamp"], str)
        assert isinstance(data["trace"], list)
        assert len(data["trace"]) == 3

        # Blueprint present
        assert data["blueprint"] is not None
        assert data["blueprint"]["task_id"] == "test-auth-module"
        assert len(data["blueprint"]["target_files"]) == 2

        # Models present
        assert "models" in data
        assert "architect" in data["models"]
        assert "developer" in data["models"]
        assert "qa" in data["models"]

    def test_json_schema_failed_run(self, workspace, failed_state):
        """JSON correctly represents a failed run with error message."""
        _write_run_log(failed_state, 42.0, "Build broken feature", workspace)

        runs_dir = Path(workspace) / "runs"
        json_file = list(runs_dir.glob("run_*.json"))[0]
        data = json.loads(json_file.read_text())

        assert data["status"] == "failed"
        assert data["error_message"] == "Max retries exhausted"
        assert data["blueprint"] is None
        assert data["retry_count"] == 3
        assert data["tokens_used"] == 48000

    def test_multiple_runs_unique_files(self, workspace, passed_state):
        """Each run produces a uniquely named file."""
        _write_run_log(passed_state, 1.0, "Task 1", workspace)
        time.sleep(0.01)  # Ensure different timestamp
        _write_run_log(passed_state, 2.0, "Task 2", workspace)

        runs_dir = Path(workspace) / "runs"
        json_files = list(runs_dir.glob("run_*.json"))
        assert len(json_files) == 2

        names = {f.name for f in json_files}
        assert len(names) == 2  # No collisions

    def test_latest_json_written(self, workspace, passed_state):
        """latest.json is written alongside the timestamped file."""
        _write_run_log(passed_state, 5.0, "Latest task", workspace)

        latest = Path(workspace) / "runs" / "latest.json"
        assert latest.is_file()

        data = json.loads(latest.read_text())
        assert data["task"] == "Latest task"

    def test_latest_json_overwritten(self, workspace, passed_state):
        """latest.json always reflects the most recent run."""
        _write_run_log(passed_state, 1.0, "First task", workspace)
        _write_run_log(passed_state, 2.0, "Second task", workspace)

        latest = Path(workspace) / "runs" / "latest.json"
        data = json.loads(latest.read_text())
        assert data["task"] == "Second task"

    def test_json_is_valid_parseable(self, workspace, passed_state):
        """Output is valid, indented JSON (human-readable for debugging)."""
        _write_run_log(passed_state, 5.0, "Test task", workspace)

        runs_dir = Path(workspace) / "runs"
        json_file = list(runs_dir.glob("run_*.json"))[0]
        content = json_file.read_text()

        # Should be indented (pretty-printed)
        assert "\n  " in content

        # Should round-trip cleanly
        data = json.loads(content)
        assert json.loads(json.dumps(data)) == data

    @patch.dict(os.environ, {
        "ARCHITECT_MODEL": "custom-architect",
        "DEVELOPER_MODEL": "custom-developer",
        "QA_MODEL": "custom-qa",
    })
    def test_captures_model_overrides(self, workspace, passed_state):
        """Model names reflect env var overrides."""
        _write_run_log(passed_state, 1.0, "Custom models", workspace)

        runs_dir = Path(workspace) / "runs"
        json_file = list(runs_dir.glob("run_*.json"))[0]
        data = json.loads(json_file.read_text())

        assert data["models"]["architect"] == "custom-architect"
        assert data["models"]["developer"] == "custom-developer"
        assert data["models"]["qa"] == "custom-qa"

    def test_existing_runs_dir_no_error(self, workspace, passed_state):
        """No error if runs/ already exists."""
        Path(workspace, "runs").mkdir()
        _write_run_log(passed_state, 1.0, "Task", workspace)

        runs_dir = Path(workspace) / "runs"
        assert len(list(runs_dir.glob("run_*.json"))) == 1
