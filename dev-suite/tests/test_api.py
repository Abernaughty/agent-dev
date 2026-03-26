"""Tests for the FastAPI API layer.

Issue #34: FastAPI Bootstrap — API Layer for Orchestrator

Uses httpx TestClient (async) to test all endpoints against
the seeded mock data. Tests cover:
- Health check
- Agent listing
- Task CRUD (list, detail, create, cancel, retry)
- Memory listing with filters, approve/reject
- PR listing
- Auth enforcement
- Error cases (404, 400, 409)
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.state import StateManager


@pytest.fixture()
def client():
    """Fresh TestClient with a clean StateManager for each test."""
    # Reset the state manager singleton for test isolation
    from src.api import state as state_mod

    fresh_manager = StateManager()
    state_mod.state_manager = fresh_manager

    # Also patch the reference in main module
    from src.api import main as main_mod

    main_mod.state_manager = fresh_manager

    return TestClient(app)


@pytest.fixture()
def auth_client():
    """TestClient with API_SECRET set — auth is enforced."""
    from src.api import state as state_mod, main as main_mod

    fresh_manager = StateManager()
    state_mod.state_manager = fresh_manager
    main_mod.state_manager = fresh_manager

    with patch.dict(os.environ, {"API_SECRET": "test-secret-123"}):
        yield TestClient(app)


# ── Health ──


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert data["agents"] == 3
        assert isinstance(data["uptime_seconds"], float)

    def test_health_no_auth_required(self, auth_client):
        """Health endpoint works even without auth header."""
        r = auth_client.get("/health")
        assert r.status_code == 200


# ── Agents ──


class TestAgents:
    def test_list_agents(self, client):
        r = client.get("/agents")
        assert r.status_code == 200
        data = r.json()
        agents = data["data"]
        assert len(agents) == 3
        names = {a["name"] for a in agents}
        assert names == {"Architect", "Lead Dev", "QA Agent"}

    def test_agent_has_expected_fields(self, client):
        r = client.get("/agents")
        agent = r.json()["data"][0]
        assert "id" in agent
        assert "name" in agent
        assert "model" in agent
        assert "status" in agent
        assert "color" in agent

    def test_response_envelope(self, client):
        r = client.get("/agents")
        data = r.json()
        assert "data" in data
        assert "meta" in data
        assert "errors" in data
        assert data["meta"]["version"] == "0.1.0"
        assert data["errors"] == []


# ── Tasks ──


class TestTasks:
    def test_list_tasks(self, client):
        r = client.get("/tasks")
        assert r.status_code == 200
        tasks = r.json()["data"]
        assert len(tasks) >= 1
        assert tasks[0]["id"] == "supabase-auth-rls"

    def test_task_has_timeline(self, client):
        r = client.get("/tasks")
        task = r.json()["data"][0]
        assert "timeline" in task
        assert len(task["timeline"]) > 0
        event = task["timeline"][0]
        assert "time" in event
        assert "agent" in event
        assert "action" in event
        assert "type" in event

    def test_task_has_budget(self, client):
        r = client.get("/tasks")
        task = r.json()["data"][0]
        assert "budget" in task
        budget = task["budget"]
        assert budget["tokens_used"] == 38200
        assert budget["token_budget"] == 50000
        assert budget["retries_used"] == 1

    def test_get_task_detail(self, client):
        r = client.get("/tasks/supabase-auth-rls")
        assert r.status_code == 200
        task = r.json()["data"]
        assert task["id"] == "supabase-auth-rls"
        assert task["blueprint"] is not None
        assert task["blueprint"]["task_id"] == "supabase-auth-rls"
        assert len(task["blueprint"]["target_files"]) == 4
        assert len(task["blueprint"]["constraints"]) == 4
        assert len(task["blueprint"]["acceptance_criteria"]) == 4

    def test_get_task_not_found(self, client):
        r = client.get("/tasks/nonexistent")
        assert r.status_code == 404

    def test_create_task(self, client):
        r = client.post("/tasks", json={"description": "Build a login page"})
        assert r.status_code == 201
        data = r.json()["data"]
        assert "task_id" in data
        assert data["status"] == "queued"

        # Verify it appears in the list
        r2 = client.get("/tasks")
        task_ids = [t["id"] for t in r2.json()["data"]]
        assert data["task_id"] in task_ids

    def test_create_task_empty_description(self, client):
        r = client.post("/tasks", json={"description": ""})
        assert r.status_code == 422  # Pydantic validation

    def test_cancel_task(self, client):
        # Create a task first
        r = client.post("/tasks", json={"description": "Test task"})
        task_id = r.json()["data"]["task_id"]

        r2 = client.post(f"/tasks/{task_id}/cancel")
        assert r2.status_code == 200
        assert r2.json()["data"]["status"] == "cancelled"

    def test_cancel_nonexistent_task(self, client):
        r = client.post("/tasks/nonexistent/cancel")
        assert r.status_code == 404

    def test_cancel_already_passed_task(self, client):
        r = client.post("/tasks/supabase-auth-rls/cancel")
        assert r.status_code == 409

    def test_retry_failed_task(self, client):
        # Create and cancel a task, then manually set to failed
        from src.api.state import state_manager
        from src.api.models import TaskStatus

        r = client.post("/tasks", json={"description": "Fail task"})
        task_id = r.json()["data"]["task_id"]
        state_manager._tasks[task_id].status = TaskStatus.FAILED

        r2 = client.post(f"/tasks/{task_id}/retry")
        assert r2.status_code == 200
        assert r2.json()["data"]["status"] == "queued"

    def test_retry_non_retryable_task(self, client):
        # The mock task is PASSED, which is not retryable
        r = client.post("/tasks/supabase-auth-rls/retry")
        assert r.status_code == 400


# ── Memory ──


class TestMemory:
    def test_list_memory(self, client):
        r = client.get("/memory")
        assert r.status_code == 200
        entries = r.json()["data"]
        assert len(entries) == 3

    def test_memory_entry_fields(self, client):
        r = client.get("/memory")
        entry = r.json()["data"][0]
        assert "id" in entry
        assert "content" in entry
        assert "tier" in entry
        assert "module" in entry
        assert "source_agent" in entry
        assert "status" in entry

    def test_filter_by_tier(self, client):
        r = client.get("/memory?tier=l0-discovered")
        assert r.status_code == 200
        entries = r.json()["data"]
        assert len(entries) == 2
        assert all(e["tier"] == "l0-discovered" for e in entries)

    def test_filter_by_status(self, client):
        r = client.get("/memory?status=pending")
        assert r.status_code == 200
        entries = r.json()["data"]
        assert len(entries) == 3  # All are pending in mock data

    def test_approve_memory(self, client):
        r = client.patch("/memory/mem-1", json={"action": "approve"})
        assert r.status_code == 200
        entry = r.json()["data"]
        assert entry["status"] == "approved"
        assert entry["verified"] is True

        # Confirm it persisted
        r2 = client.get("/memory?status=approved")
        assert len(r2.json()["data"]) == 1

    def test_reject_memory(self, client):
        r = client.patch("/memory/mem-3", json={"action": "reject"})
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "rejected"

    def test_memory_action_not_found(self, client):
        r = client.patch("/memory/nonexistent", json={"action": "approve"})
        assert r.status_code == 404

    def test_memory_invalid_action(self, client):
        r = client.patch("/memory/mem-1", json={"action": "invalid"})
        assert r.status_code == 422  # Pydantic validation


# ── Pull Requests ──


class TestPRs:
    def test_list_prs(self, client):
        r = client.get("/prs")
        assert r.status_code == 200
        prs = r.json()["data"]
        assert len(prs) == 2

    def test_pr_has_files(self, client):
        r = client.get("/prs")
        pr = next(p for p in r.json()["data"] if p["id"] == "#142")
        assert pr["file_count"] == 4
        assert len(pr["files"]) == 4
        assert pr["additions"] == 187

    def test_pr_has_tests(self, client):
        r = client.get("/prs")
        pr = next(p for p in r.json()["data"] if p["id"] == "#142")
        assert pr["tests"]["passed"] == 14
        assert pr["tests"]["failed"] == 0
        assert pr["tests"]["total"] == 14


# ── Auth ──


class TestAuth:
    def test_no_auth_in_dev_mode(self, client):
        """Without API_SECRET set, all endpoints work without auth."""
        r = client.get("/agents")
        assert r.status_code == 200

    def test_auth_required_when_secret_set(self, auth_client):
        """With API_SECRET set, endpoints require Bearer token."""
        r = auth_client.get("/agents")
        assert r.status_code == 401

    def test_auth_with_valid_token(self, auth_client):
        r = auth_client.get(
            "/agents",
            headers={"Authorization": "Bearer test-secret-123"},
        )
        assert r.status_code == 200

    def test_auth_with_invalid_token(self, auth_client):
        r = auth_client.get(
            "/agents",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 403

    def test_health_bypasses_auth(self, auth_client):
        """Health endpoint never requires auth."""
        r = auth_client.get("/health")
        assert r.status_code == 200
