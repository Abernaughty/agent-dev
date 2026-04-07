"""Tests for publish_code_node — Issue #89 / #153.

Tests the guard chain, branch creation, file push, PR opening,
and error handling. All GitHub API calls are mocked via
unittest.mock.patch on the GitHubPRProvider methods.

Issue #153: Renamed publish_pr → create_pr throughout.
Added tests for workspace_type/github_repo GraphState fields
and per-task PR targeting.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.architect import Blueprint
from src.orchestrator import GraphState, WorkflowStatus, publish_code_node


def _make_blueprint(**overrides) -> Blueprint:
    """Create a test Blueprint with sensible defaults."""
    defaults = {
        "task_id": "test-auth-rls",
        "target_files": ["src/auth.py", "tests/test_auth.py"],
        "instructions": "Implement authentication middleware",
        "constraints": ["Use cookie-based sessions"],
        "acceptance_criteria": ["All tests pass", "302 redirect for unauthed"],
    }
    defaults.update(overrides)
    return Blueprint(**defaults)


def _make_state(**overrides) -> GraphState:
    """Create a minimal GraphState for publish_code_node testing."""
    defaults: GraphState = {
        "task_description": "test task",
        "blueprint": _make_blueprint(),
        "generated_code": "# test code",
        "failure_report": None,
        "status": WorkflowStatus.PASSED,
        "retry_count": 0,
        "tokens_used": 1000,
        "error_message": "",
        "memory_context": [],
        "memory_writes": [],
        "trace": [],
        "sandbox_result": None,
        "parsed_files": [
            {"path": "src/auth.py", "content": "# auth code\ndef authenticate():\n    pass\n"},
            {"path": "tests/test_auth.py", "content": "# test code\ndef test_auth():\n    assert True\n"},
        ],
        "tool_calls_log": [],
        "workspace_root": "/tmp/test-workspace",
        "workspace_type": "local",
        "create_pr": True,
    }
    defaults.update(overrides)
    return defaults


def _make_pr_summary(number=142):
    """Create a mock PRSummary-like object."""
    mock = MagicMock()
    mock.number = number
    mock.id = f"#{number}"
    return mock


class TestPublishCodeGuards:
    """Test the guard chain that skips publishing gracefully."""

    @patch("src.api.github_prs.github_pr_provider")
    def test_skips_when_no_github_token(self, mock_provider):
        """Guard 1: No GITHUB_TOKEN → skip."""
        mock_provider.configured = False
        state = _make_state()
        result = publish_code_node(state)
        assert "skipped -- no GITHUB_TOKEN" in result["trace"][-1]
        assert "pr_url" not in result

    @patch("src.api.github_prs.github_pr_provider")
    def test_skips_when_create_pr_false(self, mock_provider):
        """Guard 2: create_pr=False → skip."""
        mock_provider.configured = True
        state = _make_state(create_pr=False)
        result = publish_code_node(state)
        assert "create_pr=False" in result["trace"][-1]

    @patch("src.api.github_prs.github_pr_provider")
    def test_skips_when_no_parsed_files(self, mock_provider):
        """Guard 3: No parsed_files → skip."""
        mock_provider.configured = True
        state = _make_state(parsed_files=[])
        result = publish_code_node(state)
        assert "no parsed_files" in result["trace"][-1]

    @patch("src.api.github_prs.github_pr_provider")
    def test_skips_when_no_blueprint(self, mock_provider):
        """Guard 4: No blueprint → skip."""
        mock_provider.configured = True
        state = _make_state(blueprint=None)
        result = publish_code_node(state)
        assert "no blueprint" in result["trace"][-1]


class TestPublishCodeHappyPath:
    """Test successful branch creation, file push, and PR opening."""

    @patch("src.api.github_prs.github_pr_provider")
    def test_creates_branch_pushes_files_opens_pr(self, mock_provider):
        """Full happy path: branch → files → PR."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_summary(142))

        state = _make_state()
        result = publish_code_node(state)

        assert result["working_branch"] == "agent/test-auth-rls"
        assert result["pr_number"] == 142
        assert "github.com" in result["pr_url"]
        assert "PR #142 opened" in result["trace"][-1]

        # Verify API calls
        mock_provider.create_branch.assert_called_once_with("agent/test-auth-rls")
        mock_provider.push_files_batch.assert_called_once()
        mock_provider.create_pr.assert_called_once()

        # Verify PR body content
        pr_call = mock_provider.create_pr.call_args
        assert pr_call.kwargs["head"] == "agent/test-auth-rls"
        assert pr_call.kwargs["base"] == "main"
        assert "test-auth-rls" in pr_call.kwargs["title"]

    @patch("src.api.github_prs.github_pr_provider")
    def test_branch_name_sanitized(self, mock_provider):
        """Branch name should have special chars replaced."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_summary())

        bp = _make_blueprint(task_id="fix auth & session handling!")
        state = _make_state(blueprint=bp)
        result = publish_code_node(state)

        branch = result["working_branch"]
        assert " " not in branch
        assert "&" not in branch
        assert "!" not in branch


class TestPublishCodeGitHubWorkspace:
    """Test PR targeting for remote GitHub workspaces (Issue #153)."""

    @patch("src.api.github_prs.github_pr_provider")
    def test_github_workspace_uses_task_repo(self, mock_provider):
        """When workspace_type=github, PR URL uses github_repo."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"  # global default
        mock_provider.repo = "agent-dev"  # global default
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_summary(99))

        state = _make_state(
            workspace_type="github",
            github_repo="Abernaughty/other-project",
            github_branch="develop",
        )
        result = publish_code_node(state)

        assert result["pr_number"] == 99
        # PR URL should reference the task's repo, not the global default
        assert "other-project" in result["pr_url"]
        # PR base branch should be the task's github_branch
        pr_call = mock_provider.create_pr.call_args
        assert pr_call.kwargs["base"] == "develop"

    @patch("src.api.github_prs.github_pr_provider")
    def test_github_workspace_uses_feature_branch(self, mock_provider):
        """When github_feature_branch is set, use it instead of auto-generated."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_summary(50))

        state = _make_state(
            workspace_type="github",
            github_repo="Abernaughty/agent-dev",
            github_branch="main",
            github_feature_branch="feature/my-custom-branch",
        )
        result = publish_code_node(state)

        assert result["working_branch"] == "feature/my-custom-branch"
        mock_provider.create_branch.assert_called_once_with("feature/my-custom-branch")

    @patch("src.api.github_prs.github_pr_provider")
    def test_github_workspace_no_repo_subdir_prefix(self, mock_provider):
        """GitHub workspace files should NOT get a repo_subdir prefix."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_summary(77))

        state = _make_state(
            workspace_type="github",
            github_repo="Abernaughty/agent-dev",
            github_branch="main",
            workspace_root="/tmp/dev-suite/task-abc",
        )
        result = publish_code_node(state)

        # Verify files were pushed with original paths (no subdir prefix)
        push_call = mock_provider.push_files_batch.call_args
        pushed_paths = [f["path"] for f in push_call.kwargs["files"]]
        assert pushed_paths == ["src/auth.py", "tests/test_auth.py"]

    @patch("src.api.github_prs.github_pr_provider")
    def test_local_workspace_falls_back_to_global_env(self, mock_provider):
        """When workspace_type=local, use global GITHUB_OWNER/GITHUB_REPO."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_summary(200))

        state = _make_state(workspace_type="local")
        result = publish_code_node(state)

        assert result["pr_number"] == 200
        assert "agent-dev" in result["pr_url"]


class TestPublishCodeErrorHandling:
    """Test error handling for various failure scenarios."""

    @patch("src.api.github_prs.github_pr_provider")
    def test_branch_creation_fails(self, mock_provider):
        """Branch creation failure → no files pushed, no PR."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=False)

        state = _make_state()
        result = publish_code_node(state)

        assert "FAILED to create branch" in result["trace"][-1]
        assert "pr_url" not in result
        assert "working_branch" not in result

    @patch("src.api.github_prs.github_pr_provider")
    def test_file_push_fails(self, mock_provider):
        """File push failure → branch exists but no PR."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=False)

        state = _make_state()
        result = publish_code_node(state)

        assert "FAILED to push files" in result["trace"][-1]
        assert result["working_branch"] == "agent/test-auth-rls"
        assert "pr_url" not in result

    @patch("src.api.github_prs.github_pr_provider")
    def test_pr_creation_fails(self, mock_provider):
        """PR creation failure → branch + files exist, no PR URL."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=None)

        state = _make_state()
        result = publish_code_node(state)

        assert "FAILED to open PR" in result["trace"][-1]
        assert result["working_branch"] == "agent/test-auth-rls"
        assert "pr_url" not in result

    @patch("src.api.github_prs.github_pr_provider")
    def test_unexpected_exception_caught(self, mock_provider):
        """Unexpected exceptions are caught and traced."""
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(side_effect=RuntimeError("network down"))

        state = _make_state()
        result = publish_code_node(state)

        assert "error -- RuntimeError" in result["trace"][-1]


class TestPublishCodeGraphIntegration:
    """Test that publish_code_node integrates correctly with the graph."""

    def test_graph_has_publish_code_node(self):
        """build_graph() includes publish_code node."""
        from src.orchestrator import build_graph

        graph = build_graph()
        assert "publish_code" in graph.nodes

    def test_route_after_qa_passes_to_publish_code(self):
        """route_after_qa returns 'publish_code' on PASSED status."""
        from src.orchestrator import route_after_qa

        state: GraphState = {
            "status": WorkflowStatus.PASSED,
            "retry_count": 0,
            "tokens_used": 100,
        }
        assert route_after_qa(state) == "publish_code"

    def test_route_after_qa_budget_exhausted_to_flush(self):
        """Budget exhaustion routes to flush_memory (no PR)."""
        from src.orchestrator import MAX_RETRIES, route_after_qa

        state: GraphState = {
            "status": WorkflowStatus.REVIEWING,
            "retry_count": MAX_RETRIES,
            "tokens_used": 100,
        }
        assert route_after_qa(state) == "flush_memory"

    def test_graphstate_has_create_pr_field(self):
        """GraphState TypedDict includes the renamed create_pr field."""
        from src.orchestrator import GraphState

        hints = GraphState.__annotations__
        assert "create_pr" in hints
        assert "publish_pr" not in hints  # Verify old name is gone
        assert "working_branch" in hints
        assert "pr_url" in hints
        assert "pr_number" in hints

    def test_graphstate_has_workspace_type_fields(self):
        """GraphState includes Issue #153 workspace fields."""
        from src.orchestrator import GraphState

        hints = GraphState.__annotations__
        assert "workspace_type" in hints
        assert "github_repo" in hints
        assert "github_branch" in hints
        assert "github_feature_branch" in hints
