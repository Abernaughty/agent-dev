"""Live end-to-end pipeline test (Step 7).

This test runs a REAL task through the full pipeline using
actual API keys. It is NOT run in CI — only on your local machine.

Prerequisites:
    - GOOGLE_API_KEY in .env (Gemini)
    - ANTHROPIC_API_KEY in .env (Claude)
    - Chroma data seeded (run: python -m src.memory.seed)
    - LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY in .env (optional)

Usage:
    cd dev-suite
    python -m pytest tests/test_e2e_live.py -v -s --run-live

    Or run directly:
    python tests/test_e2e_live.py
"""

import os
import sys
import time
from pathlib import Path

import pytest

# Add dev-suite to path when running directly
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrator import AgentState, WorkflowStatus, run_task

# ── Helpers ──

def _check_api_keys() -> dict[str, bool]:
    """Check which API keys are available."""
    return {
        "gemini": bool(os.getenv("GOOGLE_API_KEY", "")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY", "")),
        "langfuse": bool(
            os.getenv("LANGFUSE_PUBLIC_KEY", "")
            and os.getenv("LANGFUSE_SECRET_KEY", "")
        ),
    }


def _print_result(result: AgentState):
    """Pretty-print the pipeline result."""
    print("\n" + "=" * 60)
    print("  END-TO-END PIPELINE RESULT")
    print("=" * 60)
    print(f"  Status:       {result.status.value}")
    print(f"  Retry count:  {result.retry_count}")
    print(f"  Tokens used:  {result.tokens_used:,}")
    print()

    if result.blueprint:
        print("  BLUEPRINT:")
        print(f"    Task ID:      {result.blueprint.task_id}")
        print(f"    Target files: {', '.join(result.blueprint.target_files)}")
        print(f"    Constraints:  {len(result.blueprint.constraints)}")
        print(f"    Criteria:     {len(result.blueprint.acceptance_criteria)}")
        print()

    if result.generated_code:
        lines = result.generated_code.strip().split("\n")
        print(f"  GENERATED CODE: ({len(lines)} lines)")
        for line in lines[:20]:
            print(f"    {line}")
        if len(lines) > 20:
            print(f"    ... ({len(lines) - 20} more lines)")
        print()

    if result.failure_report:
        print("  QA REPORT:")
        print(f"    Verdict:      {result.failure_report.status}")
        print(f"    Tests passed: {result.failure_report.tests_passed}")
        print(f"    Tests failed: {result.failure_report.tests_failed}")
        if result.failure_report.errors:
            print(f"    Errors:       {', '.join(result.failure_report.errors[:3])}")
        print()

    if result.trace:
        print("  TRACE:")
        for t in result.trace:
            print(f"    -> {t}")
        print()

    if result.error_message:
        print(f"  ERROR: {result.error_message}")
        print()

    print("=" * 60)


# ── Tests ──

@pytest.mark.live
class TestLiveE2E:
    """Run a real task through the full pipeline."""

    def test_simple_task(self):
        """Simple task: create a Python email validator function."""
        keys = _check_api_keys()
        print(f"\n  API Keys: {keys}")

        if not keys["gemini"]:
            pytest.skip("GOOGLE_API_KEY not set")
        if not keys["anthropic"]:
            pytest.skip("ANTHROPIC_API_KEY not set")

        task = (
            "Create a Python function called validate_email that takes a string "
            "and returns True if it's a valid email address, False otherwise. "
            "Use the re module. Include type hints and a docstring."
        )

        print(f"\n  Task: {task}")
        print("  Running pipeline...\n")

        start = time.time()
        result = run_task(task, enable_tracing=keys.get("langfuse", False))
        elapsed = time.time() - start

        _print_result(result)
        print(f"  Elapsed: {elapsed:.1f}s")

        # Assertions
        assert result.status in (
            WorkflowStatus.PASSED,
            WorkflowStatus.FAILED,
            WorkflowStatus.ESCALATED,
        ), f"Unexpected status: {result.status}"

        assert result.blueprint is not None, "Architect should have produced a Blueprint"
        assert len(result.generated_code) > 0, "Developer should have generated code"
        assert result.failure_report is not None, "QA should have produced a report"
        assert result.tokens_used > 0, "Should have used some tokens"
        assert len(result.trace) >= 3, "Trace should have entries from all agents"

        # Log success/failure for documentation
        if result.status == WorkflowStatus.PASSED:
            print("  >>> PIPELINE PASSED on first try!")
        else:
            print(f"  >>> PIPELINE ended with status: {result.status.value}")
            print(f"  >>> Retries used: {result.retry_count}")


# ── Direct execution ──

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    keys = _check_api_keys()
    print(f"API Keys available: {keys}")

    missing = [k for k, v in keys.items() if not v and k != "langfuse"]
    if missing:
        print(f"\nMissing required keys: {missing}")
        print("Set them in dev-suite/.env and try again.")
        sys.exit(1)

    task = (
        "Create a Python function called validate_email that takes a string "
        "and returns True if it's a valid email address, False otherwise. "
        "Use the re module. Include type hints and a docstring."
    )

    print(f"\nTask: {task}")
    print("Running full pipeline...\n")

    start = time.time()
    result = run_task(task, enable_tracing=keys.get("langfuse", False))
    elapsed = time.time() - start

    _print_result(result)
    print(f"Total time: {elapsed:.1f}s")
