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
    summary: str = ""


# TODO Step 4:
# - Define architect prompt template
# - Configure Gemini model via langchain-google-genai
# - Implement blueprint generation with structured output
# - Wire into LangGraph node
