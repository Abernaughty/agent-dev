"""QA agent - runs tests, audits security, writes failure reports.

Produces structured failure reports for retry context.
Can escalate architectural failures back to Architect via
failure_type classification.
"""

import logging
from enum import Enum

from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)


class FailureType(str, Enum):
    """Classification of QA failure for routing decisions.

    The orchestrator uses this to decide where to route after QA:
    - CODE: Bug, syntax error, test failure in the implementation.
      Action: retry with Lead Dev using the same Blueprint.
    - ARCHITECTURAL: Wrong target file, missing dependency, design flaw.
      Action: escalate to Architect for a new Blueprint.
    """

    CODE = "code"
    ARCHITECTURAL = "architectural"


class FailureReport(BaseModel):
    """Structured report passed back on QA failure.

    Included in retry context so the orchestrator can route to the
    correct agent: Lead Dev for code fixes, Architect for re-planning.

    The failure_type field is the primary classifier. The is_architectural
    bool is kept for backward compatibility and stays in sync via the
    model validator.
    """

    task_id: str
    status: str  # "pass" | "fail" | "escalate"
    tests_passed: int
    tests_failed: int
    errors: list[str]
    failed_files: list[str]
    is_architectural: bool  # If True, escalate to Architect
    recommendation: str
    failure_type: FailureType | None = None

    @field_validator("failure_type", mode="before")
    @classmethod
    def normalize_failure_type(cls, v: object) -> object:
        """Accept case-insensitive failure_type values from LLM output.

        LLMs may return "ARCHITECTURAL", "Code", or even typos like
        "design_flaw". Normalize known values to lowercase; coerce
        unknown strings to None so the model_validator can fall back
        to is_architectural/status instead of crashing the workflow.
        """
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized in ("code", "architectural"):
                return normalized
            # Unknown value -- log and fall back to None
            if normalized:
                logger.warning(
                    "Unknown failure_type '%s' from LLM output, "
                    "falling back to is_architectural/status",
                    v,
                )
            return None
        return v

    @model_validator(mode="after")
    def sync_failure_type(self) -> "FailureReport":
        """Keep failure_type and is_architectural in sync.

        If failure_type is provided, it takes precedence and syncs
        is_architectural. If only is_architectural is set (backward
        compat from older LLM output), failure_type is derived.
        """
        if self.failure_type is not None:
            # failure_type takes precedence
            self.is_architectural = (
                self.failure_type == FailureType.ARCHITECTURAL
            )
        elif self.is_architectural:
            self.failure_type = FailureType.ARCHITECTURAL
        elif self.status == "fail":
            self.failure_type = FailureType.CODE
        # status == "pass" leaves failure_type as None (no failure)
        return self
