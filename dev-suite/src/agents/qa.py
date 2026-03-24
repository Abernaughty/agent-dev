"""QA agent - runs tests, audits security, writes failure reports.

Produces structured failure reports for retry context.
Can escalate architectural failures back to Architect.

Implementation in Step 4.
"""

from pydantic import BaseModel


class FailureReport(BaseModel):
    """Structured report passed back on QA failure.

    Included in retry context so Lead Dev knows exactly
    what broke and why.
    """

    task_id: str
    status: str  # "pass" | "fail" | "escalate"
    tests_passed: int
    tests_failed: int
    errors: list[str]
    failed_files: list[str]
    is_architectural: bool  # If True, escalate to Architect
    recommendation: str


# TODO Step 4:
# - Define QA prompt template
# - Implement test execution via E2B sandbox
# - Parse structured output from sandbox wrapper
# - Determine pass/fail/escalate decision
