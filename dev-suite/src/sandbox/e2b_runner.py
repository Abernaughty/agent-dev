"""E2B sandbox runner with structured output wrapper.

Agents never see raw stdout/stderr. The wrapper captures
output and returns only structured JSON results.

Role-specific profiles:
  - Locked-Down: Dev/QA (whitelisted domains, secrets injected)
  - Permissive: Research (open egress, zero secrets)

Template support:
  - Default: bare Python (base e2b sandbox)
  - Fullstack: Python + Node.js 22 + pnpm + SvelteKit toolchain
  - Templates configured via E2B_TEMPLATE_DEFAULT / E2B_TEMPLATE_FULLSTACK env vars

Issue #95: Added validation_skipped/warnings fields to SandboxResult,
sequential run_tests() execution, and run_script() for simple scripts.

Issue #96: Migrated from e2b_code_interpreter (Jupyter kernel on port 49999)
to base e2b SDK (commands.run). Eliminates kernel timeout errors entirely.
Uses sbx.files.write() for project files and sbx.commands.run() for
shell execution. No more base64 encoding or subprocess wrappers.
Must use Sandbox.create() classmethod (not constructor) -- the constructor
does not accept template/envs kwargs in the installed SDK version.
"""

import logging
import os
import re
from enum import Enum

from e2b import Sandbox
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# -- Sandbox Profiles --

class SandboxProfile(str, Enum):
    """Sandbox security profiles.

    LOCKED: Dev/QA agents. Secrets injected, restricted egress.
    PERMISSIVE: Research agent. Open egress, zero secrets.
    """
    LOCKED = "locked"
    PERMISSIVE = "permissive"


# -- Structured Output --

class CommandResult(BaseModel):
    """Result of a single validation command."""
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    phase: str  # "install" | "test" | "lint" | "type"


class ProjectValidationResult(BaseModel):
    """Structured result from project-aware validation (issue #159).

    Returned when a workspace has a .dev-suite.json with validation
    config enabled. Contains per-command results plus aggregated counts.
    """
    overall_pass: bool
    command_results: list[CommandResult] = []
    tests_passed: int = 0
    tests_failed: int = 0
    lint_errors: int = 0
    type_errors: int = 0
    install_ok: bool = True
    errors: list[str] = []
    duration_seconds: float = 0.0


class SandboxResult(BaseModel):
    """Structured output from sandbox execution.

    This is ALL the agent sees - never raw terminal output.
    """
    exit_code: int
    tests_passed: int | None = None
    tests_failed: int | None = None
    errors: list[str] = []
    output_summary: str = ""
    files_modified: list[str] = []
    timed_out: bool = False
    validation_skipped: bool = False
    warnings: list[str] = []
    project_validation: ProjectValidationResult | None = None


# -- Secret Patterns for Scanning --

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"),          # Anthropic
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),                 # OpenAI-style
    re.compile(r"AIza[a-zA-Z0-9_-]{35}"),               # Google
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),                 # GitHub PAT
    re.compile(r"ghs_[a-zA-Z0-9]{36}"),                 # GitHub App token
    re.compile(r"e2b_[a-zA-Z0-9]{20,}"),                # E2B
    re.compile(r"npm_[a-zA-Z0-9]{36}"),                 # npm token
    re.compile(r"(?i)(password|secret|token|key)\s*[=:]\s*\S+"),  # Generic
]


def _scan_for_secrets(text: str) -> str:
    """Redact known secret patterns from output text."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _parse_test_output(output: str) -> dict:
    """Extract test pass/fail counts from common test runner output.

    Supports pytest, unittest, jest-style, and svelte-check output.
    """
    result = {"tests_passed": None, "tests_failed": None}

    # pytest: "5 passed, 2 failed"
    pytest_match = re.search(r"(\d+) passed", output)
    pytest_fail = re.search(r"(\d+) failed", output)
    if pytest_match:
        result["tests_passed"] = int(pytest_match.group(1))
        result["tests_failed"] = int(pytest_fail.group(1)) if pytest_fail else 0
        return result

    # jest: "Tests: 2 failed, 5 passed, 7 total"
    jest_match = re.search(r"Tests:\s+(?:(\d+) failed,\s+)?(\d+) passed", output)
    if jest_match:
        result["tests_failed"] = int(jest_match.group(1)) if jest_match.group(1) else 0
        result["tests_passed"] = int(jest_match.group(2))
        return result

    # unittest: "Ran 7 tests" + "OK" or "FAILED (failures=2)"
    unittest_ran = re.search(r"Ran (\d+) test", output)
    if unittest_ran:
        total = int(unittest_ran.group(1))
        unittest_fail = re.search(r"failures=(\d+)", output)
        failed = int(unittest_fail.group(1)) if unittest_fail else 0
        result["tests_passed"] = total - failed
        result["tests_failed"] = failed
        return result

    # svelte-check: "svelte-check found 0 errors and 0 warnings"
    svelte_match = re.search(r"svelte-check found (\d+) error", output)
    if svelte_match:
        errors = int(svelte_match.group(1))
        result["tests_failed"] = errors
        result["tests_passed"] = 1 if errors == 0 else 0
        return result

    # tsc: "Found 0 errors." or "Found 3 errors."
    tsc_match = re.search(r"Found (\d+) error", output)
    if tsc_match:
        errors = int(tsc_match.group(1))
        result["tests_failed"] = errors
        result["tests_passed"] = 1 if errors == 0 else 0
        return result

    return result


def _extract_errors(output: str) -> list[str]:
    """Extract error messages from output."""
    errors = []

    # Python tracebacks
    tb_matches = re.findall(r"(\w+Error[^\n]*)", output)
    errors.extend(tb_matches[:10])  # Cap at 10

    # Node/JS errors
    js_matches = re.findall(r"((?:TypeError|ReferenceError|SyntaxError)[^\n]*)", output)
    for m in js_matches:
        if m not in errors:
            errors.extend(js_matches[:10])
            break

    # Svelte/TS compilation errors
    svelte_matches = re.findall(r"(Error: [^\n]*)", output)
    for m in svelte_matches:
        if m not in errors:
            errors.append(m)

    return errors[:10]  # Hard cap


# -- Template Configuration --

def _get_template_id(template: str | None) -> str | None:
    """Resolve a template name to an E2B template ID."""
    if template is None:
        return os.getenv("E2B_TEMPLATE_DEFAULT") or None

    template_lower = template.lower()

    env_map = {
        "fullstack": "E2B_TEMPLATE_FULLSTACK",
        "fullstack-dev": "E2B_TEMPLATE_FULLSTACK",
        "default": "E2B_TEMPLATE_DEFAULT",
        "python": "E2B_TEMPLATE_DEFAULT",
        "python-dev": "E2B_TEMPLATE_DEFAULT",
    }

    if template_lower in env_map:
        return os.getenv(env_map[template_lower]) or None

    # Treat as a raw template ID
    return template


# -- File-Type to Template Routing --

FRONTEND_EXTENSIONS = {".svelte", ".ts", ".tsx", ".js", ".jsx", ".css", ".vue"}
PYTHON_EXTENSIONS = {".py", ".pyi"}


def select_template_for_files(target_files: list[str]) -> str | None:
    """Select the appropriate sandbox template based on target file types."""
    if not target_files:
        return None

    has_frontend = False
    for filepath in target_files:
        _, ext = os.path.splitext(filepath.lower())
        if ext in FRONTEND_EXTENSIONS:
            has_frontend = True
            break

    return "fullstack" if has_frontend else None


# -- Sandbox Creation Helper --

def _create_sandbox(
    template: str | None = None,
    env_vars: dict[str, str] | None = None,
) -> Sandbox:
    """Create a sandbox via Sandbox.create() classmethod.

    IMPORTANT: Must use Sandbox.create(), not the Sandbox() constructor.
    The constructor (SandboxBase.__init__) does not accept template/envs
    kwargs -- only the create() classmethod does.

    Env vars are injected at sandbox creation time via the envs param,
    which is more secure than setting them via shell commands.
    """
    create_kwargs: dict = {}
    template_id = _get_template_id(template)
    if template_id:
        create_kwargs["template"] = template_id
    if env_vars:
        create_kwargs["envs"] = env_vars
    logger.info("[SANDBOX] Creating sandbox: template=%s, envs=%s", template_id, bool(env_vars))
    return Sandbox.create(**create_kwargs)


# -- File Upload Helper --

def _write_project_files(sbx: Sandbox, project_files: dict[str, str]) -> None:
    """Write project files into the sandbox using files.write().

    No more base64 encoding or run_code() file creation wrappers.
    Creates parent directories as needed.
    """
    created_dirs: set[str] = set()
    for fpath, content in project_files.items():
        parent = os.path.dirname(fpath)
        if parent and parent not in created_dirs:
            sbx.commands.run(f"mkdir -p '{parent}'")
            created_dirs.add(parent)
        sbx.files.write(fpath, content)


# -- Runner --

class E2BRunner:
    """Execute code in E2B sandboxes with structured output.

    Uses the base e2b SDK with Sandbox.create() + commands.run()
    for shell execution. No Jupyter kernel dependency (port 49999
    eliminated).

    Usage:
        runner = E2BRunner()
        result = runner.run_script("hello.py", project_files={"hello.py": "print('hi')"})
        result = runner.run_tests(commands=["pytest tests/"], project_files={...})
    """

    def __init__(self, api_key: str | None = None, default_timeout: int = 30):
        if api_key:
            os.environ["E2B_API_KEY"] = api_key
        self._default_timeout = default_timeout

    def _build_result(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
        timed_out: bool = False,
    ) -> SandboxResult:
        """Build a SandboxResult from command output."""
        combined = stdout + "\n" + stderr if stderr else stdout

        test_info = _parse_test_output(combined)
        errors = _extract_errors(combined) if exit_code != 0 else []
        safe_summary = _scan_for_secrets(combined)

        if len(safe_summary) > 2000:
            safe_summary = safe_summary[:2000] + "\n... [truncated]"

        return SandboxResult(
            exit_code=exit_code,
            tests_passed=test_info["tests_passed"],
            tests_failed=test_info["tests_failed"],
            errors=errors,
            output_summary=safe_summary,
            timed_out=timed_out,
        )

    def run_tests(
        self,
        commands: list[str],
        project_files: dict[str, str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int = 60,
        template: str | None = None,
    ) -> SandboxResult:
        """Run validation commands sequentially in the sandbox.

        Each command runs independently via commands.run() -- no more
        subprocess wrapper or __RESULTS__ JSON trailer. Exit codes
        are captured directly from each command result.

        A missing tool (e.g., ruff) does not prevent subsequent commands
        (e.g., pytest) from running. Results are aggregated.
        """
        try:
            with _create_sandbox(template=template, env_vars=env_vars) as sbx:
                if project_files:
                    _write_project_files(sbx, project_files)

                all_stdout = []
                all_stderr = []
                aggregate_exit_code = 0
                warnings = []

                for cmd in commands:
                    try:
                        result = sbx.commands.run(cmd, timeout=timeout)
                        stdout = result.stdout or ""
                        stderr = result.stderr or ""
                        rc = result.exit_code

                        all_stdout.append(stdout)
                        if stderr:
                            all_stderr.append(stderr)

                        if rc == 127:
                            warnings.append(f"Command not found: {cmd[:60]}")
                        if rc != 0 and aggregate_exit_code == 0:
                            aggregate_exit_code = rc

                    except Exception as cmd_err:
                        err_msg = f"Command failed: {cmd[:60]} -- {type(cmd_err).__name__}: {cmd_err}"
                        all_stderr.append(err_msg)
                        if aggregate_exit_code == 0:
                            aggregate_exit_code = 1
                        logger.warning("[SANDBOX] %s", err_msg)

                combined_stdout = "\n".join(all_stdout)
                combined_stderr = "\n".join(all_stderr)

                base_result = self._build_result(
                    combined_stdout, combined_stderr, aggregate_exit_code,
                )

                return SandboxResult(
                    exit_code=base_result.exit_code,
                    tests_passed=base_result.tests_passed,
                    tests_failed=base_result.tests_failed,
                    errors=base_result.errors,
                    output_summary=base_result.output_summary,
                    files_modified=base_result.files_modified,
                    timed_out=False,
                    validation_skipped=False,
                    warnings=warnings,
                )

        except TimeoutError:
            return SandboxResult(
                exit_code=1,
                errors=["Sandbox execution timed out"],
                output_summary=f"Sandbox timed out after {timeout}s",
                timed_out=True,
            )
        except Exception as e:
            logger.warning("[SANDBOX] run_tests error: %s", e)
            return SandboxResult(
                exit_code=1,
                errors=[str(e)],
                output_summary=f"Sandbox error: {type(e).__name__}: {e}",
            )

    def run_script(
        self,
        script_file: str,
        project_files: dict[str, str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int = 30,
        template: str | None = None,
    ) -> SandboxResult:
        """Execute a script and check exit code + stdout.

        Uses commands.run("python3 <script>") directly -- no subprocess
        wrapper, no __EXIT_CODE__ trailer parsing.
        """
        try:
            with _create_sandbox(template=template, env_vars=env_vars) as sbx:
                if project_files:
                    _write_project_files(sbx, project_files)

                result = sbx.commands.run(
                    f"python3 {script_file}",
                    timeout=timeout,
                )

                stdout = result.stdout or ""
                stderr = result.stderr or ""
                exit_code = result.exit_code

                base_result = self._build_result(stdout, stderr, exit_code)

                passed = 1 if exit_code == 0 else 0
                failed = 0 if passed else 1

                return SandboxResult(
                    exit_code=exit_code,
                    tests_passed=passed,
                    tests_failed=failed,
                    errors=base_result.errors,
                    output_summary=base_result.output_summary,
                    files_modified=base_result.files_modified,
                    timed_out=False,
                    validation_skipped=False,
                    warnings=[],
                )

        except TimeoutError:
            return SandboxResult(
                exit_code=1,
                tests_passed=0,
                tests_failed=1,
                errors=[f"Script execution timed out after {timeout}s"],
                output_summary=f"Sandbox timed out running {script_file}",
                timed_out=True,
            )
        except Exception as e:
            logger.warning("[SANDBOX] run_script error: %s", e)
            return SandboxResult(
                exit_code=1,
                tests_passed=0,
                tests_failed=1,
                errors=[str(e)],
                output_summary=f"Sandbox error: {type(e).__name__}: {e}",
            )
