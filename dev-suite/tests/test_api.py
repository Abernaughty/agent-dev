"""Tests for the FastAPI API layer.

Issue #34: FastAPI Bootstrap -- API Layer for Orchestrator
Issue #35: Updated for async StateManager mutations and version 0.2.0
Issue #50: PR tests updated for live GitHub integration
Issue #51: Replaced seeded mock data with fixture-based state

Uses httpx TestClient (sync) to test all endpoints. State is
explicitly created via fixtures -- no pre-seeded mock data.

Covers:
- Health check
- Agent listing
- Task CRUD (list, detail, create, cancel, retry)
- Memory listing with filters, approve/reject
- PR listing (mocked provider for isolation)
- Auth enforcement
- Error cases (404, 400, 409)
"""

import os
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.models import (
    BlueprintResponse,
    MemoryEntryResponse,
    MemoryStatus,
    MemoryTierEnum,
    PRFileChange,
    PRStatus,
    PRSummary,
    PRTestResults,
    TaskBudget,
    TaskDetail,
    TaskStatus,
    TimelineEvent,
)
from src.api.state import StateManager


# ── Helpers ──


def _seed_task(sm: StateManager) -> str:
    """Seed a completed task with timeline and blueprint into the StateManager."""
    from datetime import datetime, timezone

    task_id = "test-task-001"
    sm._tasks[task_id] = TaskDetail(
        id=task_id,
        description="Build authentication middleware",
        status=TaskStatus.PASSED,
        created_at=datetime(2026, 3, 25, 14, 32, tzinfo=timezone.utc),
        completed_at=datetime(2026, 3, 25, 14, 37, tzinfo=timezone.utc),
        budget=TaskBudget(
            tokens_used=12000, token_budget=50000,
            retries_used=1, max_retries=3,
            cost_used=0.18, cost_budget=1.00,
        ),
        timeline=[
            TimelineEvent(time="14:32", agent="arch", action="Blueprint created", type="plan"),
            TimelineEvent(time="14:33", agent="dev", action="Writing auth.js", type="code"),
            TimelineEvent(time="14:37", agent="dev", action="All tests passing", type="success"),
        ],
        blueprint=BlueprintResponse(
            task_id=task_id,
            target_files=["src/auth.py", "tests/test_auth.py"],
            instructions="Implement auth middleware",
            constraints=["Use JWT", "No plaintext passwords"],
            acceptance_criteria=["Auth tests pass", "401 on invalid token"],
        ),
    )
    return task_id


def _seed_memory(sm: StateManager) -> None:
    """Seed memory entries into the StateManager."""
    now = time.time()
    sm._memory = {
        "mem-1": MemoryEntryResponse(
            id="mem-1", content="Auth requires RLS on public tables",
            tier=MemoryTierEnum.L0_DISCOVERED, module="auth",
            source_agent="Architect", verified=False,
            status=MemoryStatus.PENDING, created_at=now - 120,
            expires_at=now + 47 * 3600, hours_remaining=47.0,
        ),
        "mem-2": MemoryEntryResponse(
            id="mem-2", content="auth.js needs supabase-ssr v0.5+",
            tier=MemoryTierEnum.L1, module="auth",
            source_agent="Lead Dev", verified=False,
            status=MemoryStatus.PENDING, created_at=now - 480,
        ),
        "mem-3": MemoryEntryResponse(
            id="mem-3", content="Rate limiter must wrap /api/* routes",
            tier=MemoryTierEnum.L0_DISCOVERED, module="middleware",
            source_agent="QA Agent", verified=False,
            status=MemoryStatus.PENDING, created_at=now - 840,
            expires_at=now + 47 * 3600, hours_remaining=47.0,
        ),
    }


# ── Fixtures ──


@pytest.fixture()
def client():
    """Fresh TestClient with empty StateManager."""
    from src.api import state as state_mod, main as main_mod

    fresh_manager = StateManager()
    state_mod.state_manager = fresh_manager
    main_mod.state_manager = fresh_manager

    return TestClient(app)


@pytest.fixture()
def seeded_client():
    """TestClient with pre-seeded task and memory data."""
    from src.api import state as state_mod, main as main_mod

    fresh_manager = StateManager()
    _seed_task(fresh_manager)
    _seed_memory(fresh_manager)
    state_mod.state_manager = fresh_manager
    main_mod.state_manager = fresh_manager

    return TestClient(app)


@pytest.fixture()
def auth_client():
    """TestClient with API_SECRET set -- auth is enforced."""
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
        assert data["version"] == "0.2.0"
        assert data["agents"] == 3
        assert isinstance(data["uptime_seconds"], float)
        assert "sse_subscribers" in data

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
        assert data["meta"]["version"] == "0.2.0"
        assert data["errors"] == []


# ── Tasks ──


class TestTasks:
    def test_list_tasks_empty(self, client):
        """Empty StateManager returns no tasks."""
        r = client.get("/tasks")
        assert r.status_code == 200
        tasks = r.json()["data"]
        assert len(tasks) == 0

    def test_list_tasks_seeded(self, seeded_client):
        """Seeded client returns the pre-created task."""
        r = seeded_client.get("/tasks")
        assert r.status_code == 200
        tasks = r.json()["data"]
        assert len(tasks) == 1
        assert tasks[0]["id"] == "test-task-001"

    def test_task_has_timeline(self, seeded_client):
        r = seeded_client.get("/tasks")
        task = r.json()["data"][0]
        assert "timeline" in task
        assert len(task["timeline"]) == 3
        event = task["timeline"][0]
        assert "time" in event
        assert "agent" in event
        assert "action" in event
        assert "type" in event

    def test_task_has_budget(self, seeded_client):
        r = seeded_client.get("/tasks")
        task = r.json()["data"][0]
        assert "budget" in task
        budget = task["budget"]
        assert budget["tokens_used"] == 12000
        assert budget["token_budget"] == 50000
        assert budget["retries_used"] == 1

    def test_get_task_detail(self, seeded_client):
        r = seeded_client.get("/tasks/test-task-001")
        assert r.status_code == 200
        task = r.json()["data"]
        assert task["id"] == "test-task-001"
        assert task["blueprint"] is not None
        assert task["blueprint"]["task_id"] == "test-task-001"
        assert len(task["blueprint"]["target_files"]) == 2
        assert len(task["blueprint"]["constraints"]) == 2
        assert len(task["blueprint"]["acceptance_criteria"]) == 2

    def test_get_task_not_found(self, client):
        r = client.get("/tasks/nonexistent")
        assert r.status_code == 404

    def test_create_task(self, client):
        r = client.post("/tasks", json={"description": "Build a login page"})
        assert r.status_code == 201
        data = r.json()["data"]
        assert "task_id" in data
        assert data["status"] == "queued"

        r2 = client.get("/tasks")
        task_ids = [t["id"] for t in r2.json()["data"]]
        assert data["task_id"] in task_ids

    def test_create_task_empty_description(self, client):
        r = client.post("/tasks", json={"description": ""})
        assert r.status_code == 422

    def test_cancel_task(self, client):
        r = client.post("/tasks", json={"description": "Test task"})
        task_id = r.json()["data"]["task_id"]

        r2 = client.post(f"/tasks/{task_id}/cancel")
        assert r2.status_code == 200
        assert r2.json()["data"]["status"] == "cancelled"

    def test_cancel_nonexistent_task(self, client):
        r = client.post("/tasks/nonexistent/cancel")
        assert r.status_code == 404

    def test_cancel_already_passed_task(self, seeded_client):
        """Cannot cancel a task that already passed."""
        r = seeded_client.post("/tasks/test-task-001/cancel")
        assert r.status_code == 409

    def test_retry_failed_task(self, seeded_client):
        """Can retry a task in FAILED status."""
        from src.api import state as state_mod
        state_mod.state_manager._tasks["test-task-001"].status = TaskStatus.FAILED

        r = seeded_client.post("/tasks/test-task-001/retry")
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "queued"

    def test_retry_non_retryable_task(self, seeded_client):
        """Cannot retry a task that passed."""
        r = seeded_client.post("/tasks/test-task-001/retry")
        assert r.status_code == 400


# ── Memory ──


class TestMemory:
    def test_list_memory_empty(self, client):
        """Empty StateManager returns no memory entries."""
        r = client.get("/memory")
        assert r.status_code == 200
        entries = r.json()["data"]
        assert len(entries) == 0

    def test_list_memory_seeded(self, seeded_client):
        """Seeded client returns 3 memory entries."""
        r = seeded_client.get("/memory")
        assert r.status_code == 200
        entries = r.json()["data"]
        assert len(entries) == 3

    def test_memory_entry_fields(self, seeded_client):
        r = seeded_client.get("/memory")
        entry = r.json()["data"][0]
        assert "id" in entry
        assert "content" in entry
        assert "tier" in entry
        assert "module" in entry
        assert "source_agent" in entry
        assert "status" in entry

    def test_filter_by_tier(self, seeded_client):
        r = seeded_client.get("/memory?tier=l0-discovered")
        assert r.status_code == 200
        entries = r.json()["data"]
        assert len(entries) == 2
        assert all(e["tier"] == "l0-discovered" for e in entries)

    def test_filter_by_status(self, seeded_client):
        r = seeded_client.get("/memory?status=pending")
        assert r.status_code == 200
        entries = r.json()["data"]
        assert len(entries) == 3

    def test_approve_memory(self, seeded_client):
        r = seeded_client.patch("/memory/mem-1", json={"action": "approve"})
        assert r.status_code == 200
        entry = r.json()["data"]
        assert entry["status"] == "approved"
        assert entry["verified"] is True

        r2 = seeded_client.get("/memory?status=approved")
        assert len(r2.json()["data"]) == 1

    def test_reject_memory(self, seeded_client):
        r = seeded_client.patch("/memory/mem-3", json={"action": "reject"})
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "rejected"

    def test_memory_action_not_found(self, seeded_client):
        r = seeded_client.patch("/memory/nonexistent", json={"action": "approve"})
        assert r.status_code == 404

    def test_memory_invalid_action(self, seeded_client):
        r = seeded_client.patch("/memory/mem-1", json={"action": "invalid"})
        assert r.status_code == 422


# ── Pull Requests ──


FIXTURE_PRS = [
    PRSummary(
        id="#52", number=52, title="feat: orchestrator bridge",
        author="Abernaughty", status=PRStatus.MERGED, branch="feat/orchestrator-bridge",
        additions=500, deletions=20, file_count=3,
    ),
    PRSummary(
        id="#53", number=53, title="feat: GitHub PR integration",
        author="Abernaughty", status=PRStatus.MERGED, branch="feat/github-prs",
        additions=800, deletions=30, file_count=5,
        files=[PRFileChange(name="src/api/github_prs.py", additions=400, deletions=0, status="added")],
        tests=PRTestResults(passed=45, failed=0, total=45),
    ),
]


class TestPRs:
    def test_list_prs(self, client):
        """GET /prs returns PR list from the provider."""
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = True
            mock_provider.list_prs = AsyncMock(return_value=FIXTURE_PRS)
            r = client.get("/prs")
        assert r.status_code == 200
        prs = r.json()["data"]
        assert len(prs) == 2

    def test_pr_has_expected_fields(self, client):
        """PR response includes all required fields."""
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = True
            mock_provider.list_prs = AsyncMock(return_value=FIXTURE_PRS)
            r = client.get("/prs")
        pr = r.json()["data"][0]
        for field in ("id", "number", "title", "author", "status", "branch", "additions", "deletions", "file_count"):
            assert field in pr

    def test_pr_has_files(self, client):
        """PR with files populated should include them."""
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = True
            mock_provider.list_prs = AsyncMock(return_value=FIXTURE_PRS)
            r = client.get("/prs")
        pr = next(p for p in r.json()["data"] if p["id"] == "#53")
        assert pr["file_count"] == 5
        assert len(pr["files"]) == 1
        assert pr["files"][0]["name"] == "src/api/github_prs.py"

    def test_pr_has_tests(self, client):
        """PR with test results should include them."""
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = True
            mock_provider.list_prs = AsyncMock(return_value=FIXTURE_PRS)
            r = client.get("/prs")
        pr = next(p for p in r.json()["data"] if p["id"] == "#53")
        assert pr["tests"]["passed"] == 45
        assert pr["tests"]["failed"] == 0
        assert pr["tests"]["total"] == 45

    def test_prs_state_filter(self, client):
        """GET /prs?state=open should pass the filter to the provider."""
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = True
            mock_provider.list_prs = AsyncMock(return_value=[])
            r = client.get("/prs?state=open")
            mock_provider.list_prs.assert_called_once_with(state="open")
        assert r.status_code == 200

    def test_prs_no_provider_returns_empty(self, client):
        """Without GITHUB_TOKEN, /prs returns empty list."""
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = False
            r = client.get("/prs")
        assert r.status_code == 200
        assert r.json()["data"] == []


# ── Auth ──


class TestAuth:
    def test_no_auth_in_dev_mode(self, client):
        r = client.get("/agents")
        assert r.status_code == 200

    def test_auth_required_when_secret_set(self, auth_client):
        r = auth_client.get("/agents")
        assert r.status_code == 401

    def test_auth_with_valid_token(self, auth_client):
        r = auth_client.get("/agents", headers={"Authorization": "Bearer test-secret-123"})
        assert r.status_code == 200

    def test_auth_with_invalid_token(self, auth_client):
        r = auth_client.get("/agents", headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code == 403

    def test_health_bypasses_auth(self, auth_client):
        r = auth_client.get("/health")
        assert r.status_code == 200
