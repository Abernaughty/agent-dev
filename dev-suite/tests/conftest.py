"""Shared test configuration.

Loads .env file so integration tests can access API keys.
Issue #105: Sets WORKSPACE_ROOT to a temp directory for all tests
to avoid protected workspace detection (CWD may contain "agent-dev").
"""

import os
import tempfile

import pytest
from dotenv import load_dotenv

# Load .env before any tests run (provides API keys for integration tests)
load_dotenv()

# Issue #105: Create a global temp workspace directory for tests.
# This prevents WorkspaceManager from detecting the test CWD as a
# protected workspace (since it may contain "agent-dev" in the path).
# IMPORTANT: Use direct assignment, NOT setdefault(). load_dotenv() above
# may have already set WORKSPACE_ROOT from the developer's .env file,
# making setdefault a no-op and causing tests to run against the real
# workspace path instead of the isolated temp directory.
_TEST_WORKSPACE = tempfile.mkdtemp(prefix="dev-suite-test-workspace-")
os.environ["WORKSPACE_ROOT"] = _TEST_WORKSPACE

# Export for use in test assertions
TEST_WORKSPACE_ROOT = _TEST_WORKSPACE


def pytest_addoption(parser):
    """Add --run-live flag to pytest."""
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run live E2E tests with real API keys",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: mark test as requiring live API keys")
    config.addinivalue_line("markers", "integration: tests requiring external services (E2B, etc.)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-live"):
        skip = pytest.mark.skip(reason="Need --run-live to run live E2E tests")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)
