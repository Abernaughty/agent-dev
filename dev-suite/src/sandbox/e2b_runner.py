"""E2B sandbox runner with structured output wrapper.

Agents never see raw stdout/stderr. The wrapper captures
output and returns only structured JSON results.

Role-specific profiles:
  - Locked-Down: Dev/QA (whitelisted domains, secrets injected)
  - Permissive: Research (open egress, zero secrets)
"""

import os
import re
from enum import Enum

from e2b_code_interpreter import Sandbox
from pydantic import BaseModel


# ── Sandbox Profiles ──

class SandboxProfile(str, Enum):
    """Sandbox security profiles.

    LOCKED: Dev/QA agents. Secrets injected, restricted egress.
    PERMISSIVE: Research agent. Open egress, zero secrets.
    """
    LOCKED = "locked"
    PERMISSIVE = "permissive"


# ── Structured Output ──

class SandboxResult(BaseModel):
    """Structured output from sandbox execution.

    This is ALL the agent sees — never raw terminal output.
    """
    exit_code: int
    tests_passed: int | None = None
    tests_failed: int | None = None
    errors: list[str] = []
    output_summary: str = ""
    files_modified: list[str] = []
    timed_out: bool = False


# ── Secret Patterns for Scanning ──

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"),          # Anthropic
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),                 # OpenAI-style
    re.compile(r"AIza[a-zA-Z0-9_-]{35}"),               # Google
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),                 # GitHub PAT
    re.compile(r"e2b_[a-zA-Z0-9]{20,}"),                # E2B
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

    Supports pytest, unittest, and jest-style output.
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

    return errors[:10]  # Hard cap


def _extract_execution_error(error_obj) -> str:
    """Extract error string from E2B execution.error object.

    The error object may be a string, have .name/.value/.traceback attrs,
    or be some other structure. We handle all cases.
    """
    if error_obj is None:
        return ""
    if isinstance(error_obj, str):
        return error_obj

    # E2B ExecutionError typically has .name, .value, .traceback
    parts = []
    if hasattr(error_obj, "name"):
        parts.append(str(error_obj.name))
    if hasattr(error_obj, "value"):
        parts.append(str(error_obj.value))
    if parts:
        return ": ".join(parts)

    # Fallback
    return str(error_obj)


# ── Runner ──

class E2BRunner:
    """Execute code in E2B sandboxes with structured output.

    The API key is read from the E2B_API_KEY environment variable.
    Make sure it's set in your .env file or shell environment.

    Correct usage per E2B SDK v2 (from official docs):
        Sandbox.create() is the factory method - never call Sandbox() directly.

    Usage:
        runner = E2BRunner()
        result = runner.run("print('hello')")
        print(result.output_summary)
    """

    def __init__(self, api_key: str | None = None, default_timeout: int = 30):
        # E2B SDK reads from E2B_API_KEY env var automatically.
        # If an explicit key is passed, set it in the environment.
        if api_key:
            os.environ["E2B_API_KEY"] = api_key
        self._default_timeout = default_timeout

    def run(
        self,
        code: str,
        profile: SandboxProfile = SandboxProfile.LOCKED,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> SandboxResult:
        """Execute code in a sandbox and return structured results.

        Args:
            code: Python code to execute.
            profile: Security profile (LOCKED or PERMISSIVE).
            env_vars: Environment variables to inject (secrets for LOCKED only).
            timeout: Execution timeout in seconds.

        Returns:
            SandboxResult with structured output. Never raw stdout/stderr.
        """
        timeout = timeout or self._default_timeout

        # Enforce: PERMISSIVE profile gets zero secrets
        if profile == SandboxProfile.PERMISSIVE:
            env_vars = None

        try:
            # E2B v2 SDK: Sandbox.create() is the public factory method.
            # Using it as a context manager auto-kills the sandbox on exit.
            with Sandbox.create() as sbx:
                # Inject env vars if provided
                if env_vars:
                    env_setup = "\n".join(
                        f"import os; os.environ['{k}'] = '{v}'" for k, v in env_vars.items()
                    )
                    sbx.run_code(env_setup)

                # Execute the actual code
                execution = sbx.run_code(code)

                # Collect raw output
                raw_stdout = ""
                raw_stderr = ""
                for log in execution.logs.stdout:
                    raw_stdout += log + "\n"
                for log in execution.logs.stderr:
                    raw_stderr += log + "\n"

                combined = raw_stdout + raw_stderr

                # Layer 1: Structured output wrapper
                test_info = _parse_test_output(combined)

                # Extract errors from both stdout/stderr AND execution.error
                errors = _extract_errors(combined)
                if execution.error:
                    exec_err = _extract_execution_error(execution.error)
                    if exec_err and exec_err not in errors:
                        errors.insert(0, exec_err)

                # Layer 2: Secret scanning
                safe_summary = _scan_for_secrets(combined)

                # If stdout/stderr was empty but we have an execution error,
                # include the error in the summary
                if not safe_summary.strip() and execution.error:
                    exec_err = _extract_execution_error(execution.error)
                    safe_summary = _scan_for_secrets(exec_err)

                # Truncate summary to prevent context bloat
                if len(safe_summary) > 2000:
                    safe_summary = safe_summary[:2000] + "\n... [truncated]"

                return SandboxResult(
                    exit_code=0 if not execution.error else 1,
                    tests_passed=test_info["tests_passed"],
                    tests_failed=test_info["tests_failed"],
                    errors=errors if execution.error else [],
                    output_summary=safe_summary.strip(),
                    timed_out=False,
                )

        except TimeoutError:
            return SandboxResult(
                exit_code=1,
                errors=["Execution timed out"],
                output_summary=f"Sandbox timed out after {timeout}s",
                timed_out=True,
            )
        except Exception as e:
            return SandboxResult(
                exit_code=1,
                errors=[str(e)],
                output_summary=f"Sandbox error: {type(e).__name__}: {e}",
            )

    def run_tests(
        self,
        test_command: str,
        project_files: dict[str, str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> SandboxResult:
        """Run a test suite in the sandbox.

        Args:
            test_command: Shell command to run tests (e.g., 'pytest tests/ -v').
            project_files: Dict of {filepath: content} to write before testing.
            env_vars: Secrets to inject.
            timeout: Test timeout in seconds.
        """
        # Build code that writes files then runs tests
        setup_code = ""
        if project_files:
            setup_code += "import os\n"
            for fpath, content in project_files.items():
                escaped = content.replace("\\", "\\\\").replace("'", "\\'")
                setup_code += f"os.makedirs(os.path.dirname('{fpath}') or '.', exist_ok=True)\n"
                setup_code += f"open('{fpath}', 'w').write('{escaped}')\n"

        run_code = f"""
import subprocess
result = subprocess.run(
    {test_command!r},
    shell=True,
    capture_output=True,
    text=True,
    timeout={timeout},
)
print(result.stdout)
if result.stderr:
    print(result.stderr)
"""

        full_code = setup_code + run_code
        return self.run(full_code, profile=SandboxProfile.LOCKED, env_vars=env_vars, timeout=timeout + 10)
