"""Shared test configuration.

Loads .env file so integration tests can access API keys.
"""

import pytest
from dotenv import load_dotenv

# Load .env before any tests run
load_dotenv()


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
