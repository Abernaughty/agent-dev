"""Project-aware sandbox validation runner (issue #159).

Uploads the full project into an E2B sandbox, applies agent changes
on top, then runs the workspace's test/lint/type commands. Returns
structured results so QA can catch regressions against the real
codebase, not just isolated script execution.

Config is loaded from .dev-suite.json at the workspace root:

    {
      "validation": {
        "enabled": true,
        "install_commands": ["cd dev-suite && pip install -e ."],
        "test_commands": ["cd dev-suite && python -m pytest tests/ -x --tb=short -q"],
        "lint_commands": ["cd dev-suite && ruff check src/ --select=E,F,W --no-fix"],
        "type_commands": [],
        "timeout": 180
      }
    }
"""

import json
import logging
import os
import time
from pathlib import Path

from pydantic import BaseModel

from .e2b_runner import (
    CommandResult,
    ProjectValidationResult,
    SandboxResult,
    _create_sandbox,
    _extract_errors,
    _parse_test_output,
    _scan_for_secrets,
    _write_project_files,
)

logger = logging.getLogger(__name__)


# -- Configuration --

class ProjectValidationConfig(BaseModel):
    """Per-workspace config for project-aware validation."""
    enabled: bool = False
    install_commands: list[str] = []
    test_commands: list[str] = []
    lint_commands: list[str] = []
    type_commands: list[str] = []
    timeout: int = 180


# -- Exclusions for project file upload --

EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", "dist", "build", ".egg-info",
    ".tox", ".pytest_cache", ".next", ".svelte-kit",
}

BINARY_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe",
    ".whl", ".egg", ".tar", ".gz", ".zip", ".jar",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".db", ".sqlite", ".sqlite3",
}

MAX_FILE_SIZE = 512 * 1024       # 512 KB per file
MAX_PROJECT_SIZE = 50 * 1024 * 1024  # 50 MB total


# -- Config Loader --

def load_project_validation_config(
    workspace_root: Path,
) -> ProjectValidationConfig | None:
    """Load .dev-suite.json from workspace root.

    Returns None if file doesn't exist, is malformed, or has enabled=False.
    """
    config_path = workspace_root / ".dev-suite.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", config_path, exc)
        return None
    validation_data = data.get("validation")
    if not isinstance(validation_data, dict):
        return None
    try:
        config = ProjectValidationConfig(**validation_data)
    except Exception as exc:
        logger.warning("Invalid validation config in %s: %s", config_path, exc)
        return None
    return config if config.enabled else None


# -- Project File Reader --

def _read_project_files(workspace_root: Path) -> dict[str, str]:
    """Read project files into a dict for sandbox upload.

    Skips binary files, excluded dirs, and respects size caps.
    Returns {relative_path: content}.
    """
    files: dict[str, str] = {}
    total_size = 0

    for root, dirs, filenames in os.walk(workspace_root):
        # Prune excluded directories in-place
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for fname in filenames:
            _, ext = os.path.splitext(fname.lower())
            if ext in BINARY_EXTENSIONS:
                continue

            fpath = Path(root) / fname
            try:
                size = fpath.stat().st_size
            except OSError:
                continue

            if size > MAX_FILE_SIZE:
                continue
            if total_size + size > MAX_PROJECT_SIZE:
                logger.warning(
                    "Project size cap reached (%d bytes), skipping remaining files",
                    MAX_PROJECT_SIZE,
                )
                return files

            try:
                content = fpath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            rel_path = str(fpath.relative_to(workspace_root))
            files[rel_path] = content
            total_size += size

    return files


# -- Runner --

class ProjectValidationRunner:
    """Execute validation against a full project in E2B."""

    def __init__(self, api_key: str | None = None):
        if api_key:
            os.environ["E2B_API_KEY"] = api_key

    def run_project_validation(
        self,
        config: ProjectValidationConfig,
        workspace_root: Path,
        changed_files: dict[str, str],
        template: str | None = None,
    ) -> SandboxResult:
        """Upload project, apply changes, run validation commands.

        Returns a SandboxResult with the project_validation field populated.
        """
        start = time.monotonic()

        # 1. Read project files and apply agent changes on top
        project_files = _read_project_files(workspace_root)
        project_files.update(changed_files)
        logger.info(
            "[PROJECT_VALIDATION] Uploading %d files (%d changed)",
            len(project_files), len(changed_files),
        )

        # 2. Create sandbox and upload
        sbx = _create_sandbox(template=template)
        try:
            _write_project_files(sbx, project_files)

            command_results: list[CommandResult] = []
            all_errors: list[str] = []
            install_ok = True
            aggregate_tests_passed = 0
            aggregate_tests_failed = 0
            aggregate_lint_errors = 0
            aggregate_type_errors = 0

            # 3. Install dependencies
            for cmd in config.install_commands:
                cr = self._run_command(sbx, cmd, phase="install", timeout=config.timeout)
                command_results.append(cr)
                if cr.exit_code != 0:
                    install_ok = False
                    all_errors.extend(_extract_errors(cr.stdout + "\n" + cr.stderr))
                    break

            if install_ok:
                # 4. Run lint commands
                for cmd in config.lint_commands:
                    cr = self._run_command(sbx, cmd, phase="lint", timeout=config.timeout)
                    command_results.append(cr)
                    if cr.exit_code != 0:
                        errors = _extract_errors(cr.stdout + "\n" + cr.stderr)
                        all_errors.extend(errors)
                        # Count lint errors from output lines
                        combined = cr.stdout + "\n" + cr.stderr
                        lint_count = combined.count("\n") if cr.exit_code != 0 else 0
                        aggregate_lint_errors += max(lint_count, 1)

                # 5. Run test commands
                for cmd in config.test_commands:
                    cr = self._run_command(sbx, cmd, phase="test", timeout=config.timeout)
                    command_results.append(cr)
                    combined = cr.stdout + "\n" + cr.stderr
                    test_info = _parse_test_output(combined)
                    if test_info["tests_passed"] is not None:
                        aggregate_tests_passed += test_info["tests_passed"]
                    if test_info["tests_failed"] is not None:
                        aggregate_tests_failed += test_info["tests_failed"]
                    if cr.exit_code != 0:
                        all_errors.extend(_extract_errors(combined))

                # 6. Run type commands
                for cmd in config.type_commands:
                    cr = self._run_command(sbx, cmd, phase="type", timeout=config.timeout)
                    command_results.append(cr)
                    if cr.exit_code != 0:
                        combined = cr.stdout + "\n" + cr.stderr
                        all_errors.extend(_extract_errors(combined))
                        type_count = combined.count("error:") if cr.exit_code != 0 else 0
                        aggregate_type_errors += max(type_count, 1)

            # 7. Build results
            duration = time.monotonic() - start
            overall_pass = install_ok and all(
                cr.exit_code == 0 for cr in command_results
            )

            project_result = ProjectValidationResult(
                overall_pass=overall_pass,
                command_results=command_results,
                tests_passed=aggregate_tests_passed,
                tests_failed=aggregate_tests_failed,
                lint_errors=aggregate_lint_errors,
                type_errors=aggregate_type_errors,
                install_ok=install_ok,
                errors=all_errors[:10],
                duration_seconds=round(duration, 2),
            )

            # Determine top-level exit code: 0 if all passed, else first failure
            exit_code = 0
            for cr in command_results:
                if cr.exit_code != 0:
                    exit_code = cr.exit_code
                    break

            return SandboxResult(
                exit_code=exit_code,
                tests_passed=aggregate_tests_passed,
                tests_failed=aggregate_tests_failed,
                errors=all_errors[:10],
                output_summary=self._build_summary(command_results),
                project_validation=project_result,
            )
        finally:
            try:
                sbx.kill()
            except Exception:
                pass

    def _run_command(
        self,
        sbx,
        command: str,
        phase: str,
        timeout: int,
    ) -> CommandResult:
        """Run a single command in the sandbox."""
        logger.info("[PROJECT_VALIDATION] Running [%s]: %s", phase, command)
        try:
            result = sbx.commands.run(command, timeout=timeout)
            stdout = _scan_for_secrets(result.stdout or "")
            stderr = _scan_for_secrets(result.stderr or "")
            return CommandResult(
                command=command,
                exit_code=result.exit_code,
                stdout=stdout,
                stderr=stderr,
                phase=phase,
            )
        except TimeoutError:
            logger.warning("[PROJECT_VALIDATION] Command timed out: %s", command)
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                phase=phase,
            )

    @staticmethod
    def _build_summary(command_results: list[CommandResult]) -> str:
        """Build a human-readable summary from command results."""
        lines = []
        for cr in command_results:
            status = "PASS" if cr.exit_code == 0 else "FAIL"
            lines.append(f"[{cr.phase}] {status}: {cr.command}")
            if cr.exit_code != 0:
                # Include tail of output for failed commands
                output = (cr.stdout + "\n" + cr.stderr).strip()
                if len(output) > 500:
                    output = "..." + output[-500:]
                if output:
                    lines.append(f"  {output}")
        summary = "\n".join(lines)
        if len(summary) > 2000:
            summary = summary[:2000] + "\n... [truncated]"
        return summary
