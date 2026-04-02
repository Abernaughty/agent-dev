"""E2B sandbox runner with structured output wrapper.

Agents never see raw stdout/stderr. The wrapper captures
output and returns only structured JSON results.

Role-specific profiles:
  - Locked-Down: Dev/QA (whitelisted domains, secrets injected)
  - Permissive: Research (open egress, zero secrets)

Template support:
  - Default: bare Python (e2b-code-interpreter base image)
  - Fullstack: Python + Node.js 22 + pnpm + SvelteKit toolchain
  - Templates configured via E2B_TEMPLATE_DEFAULT / E2B_TEMPLATE_FULLSTACK env vars

Issue #95: Added validation_skipped/warnings fields to SandboxResult,
sequential run_tests() execution, and run_script() for simple scripts.
"""

import os
import re
from enum import Enum

from e2b_code_interpreter import Sandbox
from pydantic import BaseModel


# -- Sandbox Profiles --

class SandboxProfile(str, Enum):
    """Sandbox security profiles.

    LOCKED: Dev/QA agents. Secrets injected, restricted egress.
    PERMISSIVE: Research agent. Open egress, zero secrets.
    """
    LOCKED = "locked"
    PERMISSIVE = "permissive"


# -- Structured Output --

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


# -- Runner --

class E2BRunner:
    """Execute code in E2B sandboxes with structured output.

    Usage:
        runner = E2BRunner()
        result = runner.run("print('hello')")
        result = runner.run_script("hello.py", project_files={"hello.py": "print('hi')"})
        result = runner.run_tests(commands=["pytest tests/"], project_files={...})
    """

    def __init__(self, api_key: str | None = None, default_timeout: int = 30):
        if api_key:
            os.environ["E2B_API_KEY"] = api_key
        self._default_timeout = default_timeout

    def run(
        self,
        code: str,
        profile: SandboxProfile = SandboxProfile.LOCKED,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
        template: str | None = None,
    ) -> SandboxResult:
        """Execute code in a sandbox and return structured results."""
        timeout = timeout or self._default_timeout

        if profile == SandboxProfile.PERMISSIVE:
            env_vars = None

        template_id = _get_template_id(template)

        try:
            create_kwargs = {}
            if template_id:
                create_kwargs["template"] = template_id

            with Sandbox.create(**create_kwargs) as sbx:
                if env_vars:
                    env_setup = "\n".join(
                        f"import os; os.environ['{k}'] = '{v}'" for k, v in env_vars.items()
                    )
                    sbx.run_code(env_setup)

                execution = sbx.run_code(code)

                raw_stdout = ""
                raw_stderr = ""
                for log in execution.logs.stdout:
                    raw_stdout += log + "\n"
                for log in execution.logs.stderr:
                    raw_stderr += log + "\n"

                combined = raw_stdout + raw_stderr

                test_info = _parse_test_output(combined)

                errors = _extract_errors(combined)
                if execution.error:
                    exec_err = _extract_execution_error(execution.error)
                    if exec_err and exec_err not in errors:
                        errors.insert(0, exec_err)

                safe_summary = _scan_for_secrets(combined)

                if not safe_summary.strip() and execution.error:
                    exec_err = _extract_execution_error(execution.error)
                    safe_summary = _scan_for_secrets(exec_err)

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
        commands: list[str],
        project_files: dict[str, str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int = 60,
        template: str | None = None,
    ) -> SandboxResult:
        """Run validation commands sequentially in the sandbox.

        Each command runs independently -- a missing tool (e.g., ruff)
        does not prevent subsequent commands (e.g., pytest) from running.
        Results are aggregated across all commands.
        """
        import base64 as _b64
        import json as _json

        setup_lines = []
        if project_files:
            setup_lines.append("import os, base64")
            for fpath, content in project_files.items():
                b64 = _b64.b64encode(content.encode("utf-8")).decode("ascii")
                setup_lines.append(f"os.makedirs(os.path.dirname('{fpath}') or '.', exist_ok=True)")
                setup_lines.append(f"open('{fpath}', 'w').write(base64.b64decode('{b64}').decode('utf-8'))")

        commands_json = _json.dumps(commands)
        run_code = f"""
import subprocess, json

commands = {commands_json}
results = []
for cmd in commands:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout={timeout},
        )
        results.append({{"cmd": cmd, "rc": r.returncode, "out": r.stdout, "err": r.stderr}})
    except subprocess.TimeoutExpired:
        results.append({{"cmd": cmd, "rc": -1, "out": "", "err": "TIMEOUT"}})

for r in results:
    print(r["out"])
    if r["err"] and r["err"] != "TIMEOUT":
        print(r["err"])

print("__RESULTS__" + json.dumps([{{"cmd": r["cmd"], "rc": r["rc"]}} for r in results]))
"""

        setup_code = "\n".join(setup_lines) + "\n" if setup_lines else ""
        full_code = setup_code + run_code

        # CR fix: scale outer timeout to account for sequential commands
        num_commands = max(len(commands), 1)
        overall_timeout = timeout * num_commands + 30

        result = self.run(
            full_code,
            profile=SandboxProfile.LOCKED,
            env_vars=env_vars,
            timeout=overall_timeout,
            template=template,
        )

        warnings = list(result.warnings)
        summary = result.output_summary

        # CR fix: aggregate exit_code from __RESULTS__ trailer instead of
        # using the wrapper process exit code. A child command failure (e.g.,
        # ruff finding lint errors) must propagate even if the wrapper exits 0.
        aggregate_exit_code = result.exit_code

        if "[WARN]" in summary:
            for line in summary.split("\n"):
                if "[WARN]" in line:
                    warnings.append(line.strip())

        if "__RESULTS__" in summary:
            try:
                json_part = summary.split("__RESULTS__")[-1].strip()
                cmd_results = _json.loads(json_part)
                # Aggregate: first non-zero rc, or 0 if all passed
                for cr in cmd_results:
                    rc = cr.get("rc", 0)
                    if rc == 127:
                        warnings.append(f"Command not found: {cr['cmd'][:60]}")
                    elif rc != 0 and aggregate_exit_code == 0:
                        aggregate_exit_code = rc
                summary = summary.split("__RESULTS__")[0].strip()
            except (ValueError, IndexError):
                pass

        return SandboxResult(
            exit_code=aggregate_exit_code,
            tests_passed=result.tests_passed,
            tests_failed=result.tests_failed,
            errors=result.errors,
            output_summary=summary,
            files_modified=result.files_modified,
            timed_out=result.timed_out,
            validation_skipped=False,
            warnings=warnings,
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

        For simple scripts that don't have a test suite.
        """
        import base64 as _b64

        setup_lines = []
        if project_files:
            setup_lines.append("import os, base64")
            for fpath, content in project_files.items():
                b64 = _b64.b64encode(content.encode("utf-8")).decode("ascii")
                setup_lines.append(f"os.makedirs(os.path.dirname('{fpath}') or '.', exist_ok=True)")
                setup_lines.append(f"open('{fpath}', 'w').write(base64.b64decode('{b64}').decode('utf-8'))")

        run_code = f"""
import subprocess
result = subprocess.run(
    ["python", {script_file!r}],
    capture_output=True,
    text=True,
    timeout={timeout},
)
print(result.stdout)
if result.stderr:
    print(result.stderr)
print(f"__EXIT_CODE__{{result.returncode}}")
"""

        setup_code = "\n".join(setup_lines) + "\n" if setup_lines else ""
        full_code = setup_code + run_code

        result = self.run(
            full_code,
            profile=SandboxProfile.LOCKED,
            env_vars=env_vars,
            timeout=timeout + 10,
            template=template,
        )

        summary = result.output_summary
        script_exit = 0
        if "__EXIT_CODE__" in summary:
            try:
                code_str = summary.split("__EXIT_CODE__")[-1].strip()
                script_exit = int(code_str)
                summary = summary.split("__EXIT_CODE__")[0].strip()
            except (ValueError, IndexError):
                pass

        passed = 1 if script_exit == 0 and not result.errors else 0
        failed = 0 if passed else 1

        return SandboxResult(
            exit_code=script_exit if not result.errors else 1,
            tests_passed=passed,
            tests_failed=failed,
            errors=result.errors,
            output_summary=summary,
            files_modified=result.files_modified,
            timed_out=result.timed_out,
            validation_skipped=False,
            warnings=[],
        )
