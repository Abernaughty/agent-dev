"""Validation command mapping for sandbox execution.

Maps file types from Blueprint target_files to:
  - The appropriate sandbox template (default Python vs fullstack)
  - The validation strategy (TEST_SUITE, SCRIPT_EXEC, LINT_ONLY, SKIP)
  - The validation commands QA should run

This module is pure functions with no side effects -- easy to test
and extend as new file types are supported.

Issue #95: Added ValidationStrategy enum to differentiate between
"run tests", "just execute the script", and "lint only" paths.
Previously, all Python tasks got ruff + pytest even when no test
suite existed, causing misleading "passed" results.
"""

import os
from dataclasses import dataclass, field
from enum import Enum

from .e2b_runner import select_template_for_files


# -- File Extension Categories --

FRONTEND_EXTENSIONS = {".svelte", ".ts", ".tsx", ".js", ".jsx", ".css", ".vue"}
PYTHON_EXTENSIONS = {".py", ".pyi"}

# CR fix: .pyi stubs are type annotations only -- not executable.
# Use this for _get_primary_script() selection so stubs are never
# chosen as the script to run via SCRIPT_EXEC.
EXECUTABLE_PYTHON_EXTENSIONS = {".py"}


class ValidationStrategy(str, Enum):
    """How to validate the generated code in the sandbox.

    TEST_SUITE:  Full validation -- lint + test runner. Used when test
                 files are present in target_files.
    SCRIPT_EXEC: Run the primary script and check exit code + stdout.
                 Used for single-file scripts with no test suite.
    LINT_ONLY:   Syntax/lint check only, no execution. Used for
                 non-executable code (e.g., .pyi stubs, config generators).
    SKIP:        No validation needed (non-code files).
    """
    TEST_SUITE = "test_suite"
    SCRIPT_EXEC = "script_exec"
    LINT_ONLY = "lint_only"
    SKIP = "skip"


@dataclass
class ValidationPlan:
    """What to validate and how.

    Attributes:
        strategy: The validation approach to use.
        template: Sandbox template name ("fullstack" or None for default).
        commands: Shell commands to run in the sandbox (for TEST_SUITE/LINT_ONLY).
        script_file: Primary file to execute (for SCRIPT_EXEC).
        description: Human-readable summary for logging/QA context.
    """

    strategy: ValidationStrategy = ValidationStrategy.SKIP
    template: str | None = None
    commands: list[str] = field(default_factory=list)
    script_file: str | None = None
    description: str = ""


# -- Command Sets --

# CR fix: if/then/else preserves ruff exit code; A && B || C masks failures
PYTHON_LINT_COMMANDS = [
    "if command -v ruff >/dev/null 2>&1; then ruff check . --select=E,F,W --no-fix; else echo '[WARN] ruff not available -- lint skipped'; fi",
]

# CR fix: drop hardcoded tests/ -- let pytest auto-discover
PYTHON_TEST_COMMANDS = [
    "python -m pytest -v --tb=short",
]

PYTHON_COMMANDS = PYTHON_LINT_COMMANDS + PYTHON_TEST_COMMANDS

FRONTEND_COMMANDS = [
    "cd /home/user/dashboard && pnpm check 2>&1 || true",
    "cd /home/user/dashboard && pnpm exec tsc --noEmit 2>&1 || true",
]

# When both Python and frontend files are involved
FULLSTACK_COMMANDS = FRONTEND_COMMANDS + PYTHON_COMMANDS


# CR fix: boundary-based test detection instead of substring matching
def _is_test_file(filepath: str) -> bool:
    """Check if a single file looks like a test file using boundary matching.

    Matches on basename boundaries to avoid false positives like
    'latest_utils.py' or 'contest_helper.py'.
    """
    lower = filepath.lower().replace("\\", "/")
    parts = lower.split("/")
    basename = parts[-1] if parts else lower

    # Directory component is "tests" (exact match)
    if "tests" in parts[:-1]:
        return True

    # Basename starts with "test_"
    if basename.startswith("test_"):
        return True

    # Basename ends with "_test.py" or "_test.pyi"
    name_no_ext = os.path.splitext(basename)[0]
    if name_no_ext.endswith("_test"):
        return True

    # Exact matches
    if basename in ("tests.py", "conftest.py"):
        return True

    # JS/TS test patterns: .test.js, .test.ts, .spec.js, .spec.ts
    if ".test." in basename or ".spec." in basename:
        return True

    return False


def _has_test_files(target_files: list[str]) -> bool:
    """Check if any target files look like test files or test directories."""
    return any(_is_test_file(f) for f in target_files)


def _get_primary_script(target_files: list[str]) -> str | None:
    """Find the primary executable script from target files.

    Returns the first .py file (not .pyi stubs) that doesn't look like
    a test file, or the first .py file if all look like tests, or None.

    CR fix: .pyi stubs are excluded -- they are type annotations only
    and cannot be meaningfully executed.
    """
    py_files = [
        f for f in target_files
        if os.path.splitext(f.lower())[1] in EXECUTABLE_PYTHON_EXTENSIONS
    ]
    if not py_files:
        return None

    # Prefer non-test files
    for f in py_files:
        if not _is_test_file(f):
            return f

    # Fallback to first .py file
    return py_files[0]


def get_validation_plan(target_files: list[str]) -> ValidationPlan:
    """Determine the validation plan based on target file types.

    Strategy selection:
      - No files -> SKIP
      - Non-code files only -> SKIP
      - Frontend files -> TEST_SUITE (pnpm check + tsc)
      - Python files with test files -> TEST_SUITE (ruff + pytest)
      - Python files without test files:
          - Has executable .py files -> SCRIPT_EXEC (run the script)
          - Only .pyi stubs -> LINT_ONLY (ruff check only)
      - Mixed frontend + Python -> TEST_SUITE (fullstack)

    Args:
        target_files: List of file paths from the Blueprint.

    Returns:
        ValidationPlan with strategy, template, commands, and description.
    """
    if not target_files:
        return ValidationPlan(
            strategy=ValidationStrategy.SKIP,
            template=None,
            commands=[],
            description="No target files -- skipping validation",
        )

    has_frontend = False
    has_python = False

    for filepath in target_files:
        _, ext = os.path.splitext(filepath.lower())
        if ext in FRONTEND_EXTENSIONS:
            has_frontend = True
        elif ext in PYTHON_EXTENSIONS:
            has_python = True

    # Pick template
    template = select_template_for_files(target_files)

    # Pick strategy and commands
    if has_frontend and has_python:
        # Full-stack: always TEST_SUITE
        return ValidationPlan(
            strategy=ValidationStrategy.TEST_SUITE,
            template=template,
            commands=list(FULLSTACK_COMMANDS),
            description=(
                f"Full-stack validation: {len(target_files)} files "
                f"(frontend + Python) using fullstack template"
            ),
        )

    if has_frontend:
        return ValidationPlan(
            strategy=ValidationStrategy.TEST_SUITE,
            template=template,
            commands=list(FRONTEND_COMMANDS),
            description=(
                f"Frontend validation: {len(target_files)} files "
                f"using fullstack template (pnpm check + tsc)"
            ),
        )

    if has_python:
        has_tests = _has_test_files(target_files)
        if has_tests:
            return ValidationPlan(
                strategy=ValidationStrategy.TEST_SUITE,
                template=template,
                commands=list(PYTHON_COMMANDS),
                description=(
                    f"Python validation: {len(target_files)} files "
                    f"using default template (ruff + pytest)"
                ),
            )
        else:
            primary = _get_primary_script(target_files)
            # CR fix: if no executable .py files found (e.g., .pyi stubs
            # only), fall back to lint-only instead of trying to execute
            if primary is None:
                return ValidationPlan(
                    strategy=ValidationStrategy.LINT_ONLY,
                    template=template,
                    commands=list(PYTHON_LINT_COMMANDS),
                    description=(
                        f"Lint-only validation: {len(target_files)} files "
                        f"using default template (no executable .py files)"
                    ),
                )
            return ValidationPlan(
                strategy=ValidationStrategy.SCRIPT_EXEC,
                template=template,
                commands=[],
                script_file=primary,
                description=(
                    f"Script execution: {len(target_files)} files "
                    f"using default template (run + check exit code)"
                ),
            )

    # Non-code files (JSON, YAML, SQL, etc.)
    return ValidationPlan(
        strategy=ValidationStrategy.SKIP,
        template=None,
        commands=[],
        description=(
            f"No code validation needed for {len(target_files)} files "
            f"(non-code file types)"
        ),
    )


def format_validation_summary(plan: ValidationPlan) -> str:
    """Format a validation plan as a human-readable string for QA context.

    Used in the QA prompt to describe what sandbox validation was attempted.
    """
    if plan.strategy == ValidationStrategy.SKIP:
        return plan.description

    if plan.strategy == ValidationStrategy.SCRIPT_EXEC:
        lines = [plan.description]
        if plan.script_file:
            lines.append(f"  Executing: python {plan.script_file}")
        return "\n".join(lines)

    if not plan.commands:
        return plan.description

    lines = [plan.description, "Commands:"]
    for cmd in plan.commands:
        lines.append(f"  $ {cmd}")
    return "\n".join(lines)
