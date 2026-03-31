"""Validation command mapping for sandbox execution.

Maps file types from Blueprint target_files to:
  - The appropriate sandbox template (default Python vs fullstack)
  - The validation commands QA should run

This module is pure functions with no side effects — easy to test
and extend as new file types are supported.
"""

import os
from dataclasses import dataclass, field

from .e2b_runner import select_template_for_files


# -- File Extension Categories --

FRONTEND_EXTENSIONS = {".svelte", ".ts", ".tsx", ".js", ".jsx", ".css", ".vue"}
PYTHON_EXTENSIONS = {".py", ".pyi"}


@dataclass
class ValidationPlan:
    """What to validate and how.

    Attributes:
        template: Sandbox template name ("fullstack" or None for default).
        commands: Shell commands to run in the sandbox.
        description: Human-readable summary for logging/QA context.
    """

    template: str | None = None
    commands: list[str] = field(default_factory=list)
    description: str = ""


# -- Command Sets --

PYTHON_COMMANDS = [
    "ruff check . --select=E,F,W --no-fix",
    "python -m pytest tests/ -v --tb=short 2>&1 || true",
]

FRONTEND_COMMANDS = [
    "cd /home/user/dashboard && pnpm check 2>&1 || true",
    "cd /home/user/dashboard && pnpm exec tsc --noEmit 2>&1 || true",
]

# When both Python and frontend files are involved
FULLSTACK_COMMANDS = FRONTEND_COMMANDS + PYTHON_COMMANDS


def get_validation_plan(target_files: list[str]) -> ValidationPlan:
    """Determine the validation plan based on target file types.

    Args:
        target_files: List of file paths from the Blueprint.

    Returns:
        ValidationPlan with template, commands, and description.
    """
    if not target_files:
        return ValidationPlan(
            template=None,
            commands=[],
            description="No target files \u2014 skipping validation",
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

    # Pick commands
    if has_frontend and has_python:
        commands = list(FULLSTACK_COMMANDS)
        description = (
            f"Full-stack validation: {len(target_files)} files "
            f"(frontend + Python) using fullstack template"
        )
    elif has_frontend:
        commands = list(FRONTEND_COMMANDS)
        description = (
            f"Frontend validation: {len(target_files)} files "
            f"using fullstack template (pnpm check + tsc)"
        )
    elif has_python:
        commands = list(PYTHON_COMMANDS)
        description = (
            f"Python validation: {len(target_files)} files "
            f"using default template (ruff + pytest)"
        )
    else:
        # Non-code files (JSON, YAML, SQL, etc.) \u2014 no validation
        commands = []
        description = (
            f"No code validation needed for {len(target_files)} files "
            f"(non-code file types)"
        )

    return ValidationPlan(
        template=template,
        commands=commands,
        description=description,
    )


def format_validation_summary(plan: ValidationPlan) -> str:
    """Format a validation plan as a human-readable string for QA context.

    Used in the QA prompt to describe what sandbox validation was attempted.
    """
    if not plan.commands:
        return plan.description

    lines = [plan.description, "Commands:"]
    for cmd in plan.commands:
        lines.append(f"  $ {cmd}")
    return "\n".join(lines)
