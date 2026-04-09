"""Architect agent - plans tasks, generates structured Blueprints.

Uses Gemini 2.0 Ultra for large-context reasoning.
Never writes code directly.

Implementation in Step 4.
"""

from pydantic import BaseModel


class Blueprint(BaseModel):
    """Structured task specification passed from Architect to Lead Dev.

    This eliminates interpretation drift by using a schema
    instead of natural language instructions.
    """

    task_id: str
    target_files: list[str]
    instructions: str
    constraints: list[str]
    acceptance_criteria: list[str]


class SubTask(BaseModel):
    """A single sub-task within a multi-file task decomposition."""

    sub_task_id: str
    parent_task_id: str
    sequence: int
    depends_on: list[str] = []
    target_files: list[str]
    instructions: str
    description: str


class TaskDecomposition(BaseModel):
    """Decomposition of a complex task into ordered sub-tasks."""

    parent_task_id: str
    sub_tasks: list[SubTask]
    rationale: str
