"""E2B sandbox runner with structured output wrapper.

Agents never see raw stdout/stderr. The wrapper captures
output and returns only structured JSON results.

Role-specific profiles:
  - Locked-Down: Dev/QA (whitelisted domains, secrets injected)
  - Permissive: Research (open egress, zero secrets)

Implementation in Step 3.
"""

from pydantic import BaseModel


class SandboxResult(BaseModel):
    """Structured output from sandbox execution.

    This is all the agent sees - never raw terminal output.
    """

    exit_code: int
    tests_passed: int | None = None
    tests_failed: int | None = None
    errors: list[str] = []
    output_summary: str = ""
    files_modified: list[str] = []


# TODO Step 3:
# - Initialize E2B client with API key
# - Implement run_in_sandbox(code, profile="locked") -> SandboxResult
# - Implement output wrapper script (runs inside E2B, captures stdout/stderr)
# - Define sandbox profiles (locked-down vs permissive)
# - Secret injection via environment variables
# - Regex-based secret scanning on output
