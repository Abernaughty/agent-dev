"""Planner agent — lightweight conversational task validator.

Issue #106 Phase B: Validates task completeness against a readiness
checklist before releasing to the Architect. Uses a lightweight/cheap
model (configurable via PLANNER_MODEL env var) with ZERO tool access.

The Planner is a pre-graph component — it runs outside the LangGraph
state machine. It manages a conversational session between the user
and the LLM, producing a TaskSpec that feeds into POST /tasks.

Usage:
    from src.agents.planner import (
        PlannerSession,
        create_planner_session,
        send_planner_message,
        infer_workspace_stack,
    )

    # Start a session with workspace auto-inference
    stack = infer_workspace_stack(Path("/my/project"))
    session = create_planner_session(workspace="/my/project", **stack)

    # Conversational loop
    response = await send_planner_message(session, "Add auth middleware")
    # response.message = "I see this is a TypeScript/React project..."
    # response.checklist = ChecklistStatus(...)
    # response.ready = False  (missing acceptance_criteria)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Default model for the Planner agent — lightweight and cheap.
# Override via PLANNER_MODEL env var.
DEFAULT_PLANNER_MODEL = "gemini-3.1-flash-lite-preview"

# Session TTL in seconds (30 minutes idle timeout)
SESSION_TTL_SECONDS = 30 * 60


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChecklistPriority(str, Enum):
    """Priority tiers for readiness checklist items."""

    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class ChecklistItem(BaseModel):
    """Single item in the task readiness checklist."""

    field: str
    priority: ChecklistPriority
    satisfied: bool = False
    auto_inferred: bool = False
    value: str | list[str] | None = None
    notes: str = ""


class ChecklistStatus(BaseModel):
    """Overall readiness checklist state.

    Tracks which fields are present, missing, and whether
    the task is ready to submit to the Architect.
    """

    items: list[ChecklistItem] = Field(default_factory=list)

    @property
    def required_satisfied(self) -> bool:
        """All required items are satisfied."""
        return all(
            item.satisfied
            for item in self.items
            if item.priority == ChecklistPriority.REQUIRED
        )

    @property
    def has_warnings(self) -> bool:
        """Any recommended items are missing."""
        return any(
            not item.satisfied
            for item in self.items
            if item.priority == ChecklistPriority.RECOMMENDED
        )

    @property
    def missing_required(self) -> list[str]:
        """Field names of unsatisfied required items."""
        return [
            item.field
            for item in self.items
            if item.priority == ChecklistPriority.REQUIRED and not item.satisfied
        ]

    @property
    def missing_recommended(self) -> list[str]:
        """Field names of unsatisfied recommended items."""
        return [
            item.field
            for item in self.items
            if item.priority == ChecklistPriority.RECOMMENDED and not item.satisfied
        ]


class TaskSpec(BaseModel):
    """Structured task specification produced by the Planner.

    This is what ultimately feeds into the Architect as the
    task_description (serialized) and enriched GraphState.
    """

    workspace: str = ""
    objective: str = ""
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    output_type: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    related_files: list[str] = Field(default_factory=list)

    def to_description(self) -> str:
        """Serialize to a rich description string for the Architect."""
        parts = [self.objective]
        if self.languages:
            parts.append(f"Languages: {', '.join(self.languages)}")
        if self.frameworks:
            parts.append(f"Frameworks: {', '.join(self.frameworks)}")
        if self.output_type:
            parts.append(f"Expected output: {self.output_type}")
        if self.acceptance_criteria:
            parts.append("Acceptance criteria:")
            for ac in self.acceptance_criteria:
                parts.append(f"  - {ac}")
        if self.constraints:
            parts.append("Constraints:")
            for c in self.constraints:
                parts.append(f"  - {c}")
        if self.related_files:
            parts.append(f"Related files: {', '.join(self.related_files)}")
        return "\n".join(parts)


class PlannerMessage(BaseModel):
    """A single message in the planner conversation."""

    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: float = Field(default_factory=time.time)


class PlannerSession(BaseModel):
    """Manages a single planning conversation session.

    Holds conversation history, evolving TaskSpec, and checklist state.
    Sessions expire after SESSION_TTL_SECONDS of inactivity.
    """

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_spec: TaskSpec = Field(default_factory=TaskSpec)
    checklist: ChecklistStatus = Field(default_factory=ChecklistStatus)
    messages: list[PlannerMessage] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    last_activity: float = Field(default_factory=time.time)
    submitted: bool = False

    @property
    def is_expired(self) -> bool:
        """Check if session has exceeded idle TTL."""
        return (time.time() - self.last_activity) > SESSION_TTL_SECONDS

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()


class PlannerResponse(BaseModel):
    """Response from a planner message exchange."""

    session_id: str
    message: str
    task_spec: TaskSpec
    checklist: ChecklistStatus
    ready: bool = False
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Workspace auto-inference
# ---------------------------------------------------------------------------

# Exact package name matching for package.json dependencies.
# Keys are matched exactly against dependency names (not substring).
# Scoped packages (starting with @) use prefix matching.
_PACKAGE_JSON_FRAMEWORK_MAP: dict[str, str] = {
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit",
    "angular": "Angular",
    "@angular/core": "Angular",
    "express": "Express",
    "fastify": "Fastify",
    "tailwindcss": "TailwindCSS",
    "@tailwindcss/vite": "TailwindCSS",
    "@tailwindcss/postcss": "TailwindCSS",
}

# Whole-token matching for pyproject.toml dependency names.
# Uses word-boundary regex to avoid substring false positives.
_PYPROJECT_FRAMEWORK_MAP: dict[str, str] = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "starlette": "Starlette",
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "pytest": "pytest",
    "pydantic": "Pydantic",
}


def _match_dep_exact(dep_name: str, pattern: str) -> bool:
    """Check if a dependency name matches a pattern exactly.

    For scoped packages (@scope/name), matches if the dep starts with
    the pattern. For unscoped packages, requires exact match.
    """
    if pattern.startswith("@"):
        return dep_name == pattern or dep_name.startswith(pattern + "/")
    return dep_name == pattern


def infer_workspace_stack(
    workspace_path: Path,
) -> dict[str, list[str]]:
    """Auto-detect languages and frameworks from workspace project files.

    Inspects package.json, pyproject.toml, Cargo.toml, go.mod, etc.
    Returns {"languages": [...], "frameworks": [...]}.

    This runs server-side (orchestrator level), NOT as a Planner tool.
    Only reads a whitelist of known project manifest files.
    """
    languages: list[str] = []
    frameworks: list[str] = []

    workspace = Path(workspace_path).resolve()

    # -- package.json (Node/JS/TS) --
    pkg_json = workspace / "package.json"
    if pkg_json.is_file():
        try:
            with open(pkg_json, encoding="utf-8") as f:
                data = json.load(f)

            # Collect all dependency names
            all_deps: dict[str, str] = {}
            for dep_key in ("dependencies", "devDependencies", "peerDependencies"):
                all_deps.update(data.get(dep_key, {}))

            # Detect TypeScript
            if "typescript" in all_deps or (workspace / "tsconfig.json").is_file():
                if "TypeScript" not in languages:
                    languages.append("TypeScript")
            if "JavaScript" not in languages:
                languages.append("JavaScript")

            # Detect frameworks using exact dependency name matching
            for pattern, framework_name in _PACKAGE_JSON_FRAMEWORK_MAP.items():
                if any(_match_dep_exact(dep, pattern) for dep in all_deps):
                    if framework_name not in frameworks:
                        frameworks.append(framework_name)

        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Failed to parse package.json: %s", e)

    # -- pyproject.toml (Python) --
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        if "Python" not in languages:
            languages.append("Python")
        try:
            content = pyproject.read_text(encoding="utf-8").lower()
            # Use word-boundary matching to avoid substring false positives
            for dep_pattern, framework_name in _PYPROJECT_FRAMEWORK_MAP.items():
                # Match as a quoted dependency name or standalone word
                pattern = rf'(?:^|[\s"\',\[])({re.escape(dep_pattern)})(?:[\s"\',>=<\]\[]|$)'
                if re.search(pattern, content) and framework_name not in frameworks:
                    frameworks.append(framework_name)
        except OSError as e:
            logger.debug("Failed to read pyproject.toml: %s", e)

    # -- requirements.txt fallback (Python) --
    requirements = workspace / "requirements.txt"
    if requirements.is_file() and "Python" not in languages:
        languages.append("Python")

    # -- Cargo.toml (Rust) --
    if (workspace / "Cargo.toml").is_file():
        if "Rust" not in languages:
            languages.append("Rust")

    # -- go.mod (Go) --
    if (workspace / "go.mod").is_file():
        if "Go" not in languages:
            languages.append("Go")

    # -- pom.xml (Java) --
    if (workspace / "pom.xml").is_file():
        if "Java" not in languages:
            languages.append("Java")

    # -- build.gradle / build.gradle.kts (Java/Kotlin) --
    has_gradle = (workspace / "build.gradle").is_file()
    has_gradle_kts = (workspace / "build.gradle.kts").is_file()
    if has_gradle or has_gradle_kts:
        if "Java" not in languages:
            languages.append("Java")
        # Only detect Kotlin from .kts (Kotlin DSL) — plain build.gradle
        # alone is not sufficient evidence for Kotlin.
        if has_gradle_kts and "Kotlin" not in languages:
            languages.append("Kotlin")

    return {"languages": languages, "frameworks": frameworks}


# ---------------------------------------------------------------------------
# Checklist construction
# ---------------------------------------------------------------------------


def build_checklist(task_spec: TaskSpec) -> ChecklistStatus:
    """Build a readiness checklist from the current TaskSpec state.

    Evaluates each field against the Required/Recommended/Optional tiers
    defined in the Dashboard Workflow Roadmap Section 2.3.

    Required: workspace, objective, languages
    Recommended: frameworks, output_type, acceptance_criteria
    Optional: constraints, related_files

    Note: frameworks is RECOMMENDED (not required) because many valid
    tasks (scripts, CLI tools, data processing) don't use any framework.
    """
    items = [
        ChecklistItem(
            field="workspace",
            priority=ChecklistPriority.REQUIRED,
            satisfied=bool(task_spec.workspace),
            value=task_spec.workspace or None,
        ),
        ChecklistItem(
            field="objective",
            priority=ChecklistPriority.REQUIRED,
            satisfied=bool(task_spec.objective),
            value=task_spec.objective or None,
        ),
        ChecklistItem(
            field="languages",
            priority=ChecklistPriority.REQUIRED,
            satisfied=bool(task_spec.languages),
            value=task_spec.languages or None,
            auto_inferred=bool(task_spec.languages),
        ),
        ChecklistItem(
            field="frameworks",
            priority=ChecklistPriority.RECOMMENDED,
            satisfied=bool(task_spec.frameworks),
            value=task_spec.frameworks or None,
            auto_inferred=bool(task_spec.frameworks),
        ),
        ChecklistItem(
            field="output_type",
            priority=ChecklistPriority.RECOMMENDED,
            satisfied=bool(task_spec.output_type),
            value=task_spec.output_type,
        ),
        ChecklistItem(
            field="acceptance_criteria",
            priority=ChecklistPriority.RECOMMENDED,
            satisfied=bool(task_spec.acceptance_criteria),
            value=task_spec.acceptance_criteria or None,
        ),
        ChecklistItem(
            field="constraints",
            priority=ChecklistPriority.OPTIONAL,
            satisfied=bool(task_spec.constraints),
            value=task_spec.constraints or None,
        ),
        ChecklistItem(
            field="related_files",
            priority=ChecklistPriority.OPTIONAL,
            satisfied=bool(task_spec.related_files),
            value=task_spec.related_files or None,
        ),
    ]
    return ChecklistStatus(items=items)


# ---------------------------------------------------------------------------
# Planner session management
# ---------------------------------------------------------------------------


def create_planner_session(
    workspace: str,
    languages: list[str] | None = None,
    frameworks: list[str] | None = None,
) -> PlannerSession:
    """Create a new planner session with optional pre-populated fields.

    The workspace is always set. Languages and frameworks can be
    pre-populated from auto-inference (infer_workspace_stack).
    """
    task_spec = TaskSpec(
        workspace=workspace,
        languages=languages or [],
        frameworks=frameworks or [],
    )
    checklist = build_checklist(task_spec)

    session = PlannerSession(
        task_spec=task_spec,
        checklist=checklist,
    )

    # System message with pre-populated context — formatted with clear
    # separation between workspace info and the user-facing prompt.
    context_lines = [f"**Workspace:** `{workspace}`"]
    if languages:
        context_lines.append(
            f"**Detected languages:** {', '.join(languages)}"
        )
    if frameworks:
        context_lines.append(
            f"**Detected frameworks:** {', '.join(frameworks)}"
        )
    context_lines.append("")
    context_lines.append(
        "Describe what you'd like the agents to accomplish. "
        "I'll help ensure the task specification is complete."
    )

    session.messages.append(
        PlannerMessage(role="system", content="\n".join(context_lines))
    )

    logger.info(
        "Created planner session %s (workspace=%s, languages=%s, frameworks=%s)",
        session.session_id,
        workspace,
        languages,
        frameworks,
    )
    return session


# ---------------------------------------------------------------------------
# Planner LLM interaction
# ---------------------------------------------------------------------------

# System prompt for the Planner agent.
_PLANNER_SYSTEM_PROMPT = """\
You are a Task Planner for an AI agent team. Your job is to help the user \
create a clear, complete task specification before it's sent to the Architect \
agent for blueprint generation.

You have ZERO tool access — no filesystem, no GitHub, no sandbox. You only \
work with information the user provides and any auto-detected project context.

Your responsibilities:
1. Understand the user's objective
2. Validate the task against the readiness checklist
3. Ask for missing REQUIRED fields (workspace, objective, languages) if \
   they are not already satisfied
4. After the user's initial message, ask about any missing information \
   that you judge to be important for the task's success. Only ask about \
   fields that are contextually relevant — for example, acceptance criteria \
   matter for a complex feature but not for a simple script.
5. If the user does not provide the additional info in their response, \
   fill in recommended fields yourself where you can make a reasonable \
   inference, and move on. Do not repeatedly ask for the same field.
6. When the task spec is complete, present a brief summary and ask \
   "Ready to submit to the Architect?"

Current task specification state (JSON):
{task_spec_json}

Checklist status:
- Required missing: {missing_required}
- Recommended missing: {missing_recommended}
- Ready to submit: {ready}

Rules:
- Be concise and conversational — not robotic
- Use **bold** for emphasis, `backticks` for file paths and code references, \
  and numbered lists when asking multiple questions
- If the user gives you enough info, extract fields directly (don't ask \
  obvious questions)
- When auto-inferred fields exist, confirm them briefly: "I see this is a \
  TypeScript/SvelteKit project — does that look right?"
- If the task doesn't require a framework (e.g., standalone scripts), that's \
  perfectly fine — do not warn about missing frameworks
- Do NOT include a JSON specification block or code fences in your \
  conversational response — the system extracts fields automatically. \
  Keep your response purely conversational.
- ALWAYS include a hidden JSON extraction block at the very end of your \
  message, separated by a blank line, in this exact format:
  ```json
  {{
    "objective": "...",
    "languages": [...],
    "frameworks": [...],
    "output_type": "...",
    "acceptance_criteria": [...],
    "constraints": [...],
    "related_files": [...]
  }}
  ```
  Include only fields you can extract from the conversation so far.
  Omit fields you don't have information for.
  This block is stripped before display — the user never sees it.
"""


def _get_planner_model_name() -> str:
    """Get the configured planner model name."""
    return os.getenv("PLANNER_MODEL", DEFAULT_PLANNER_MODEL)


def _build_planner_messages(
    session: PlannerSession,
) -> list[dict[str, str]]:
    """Build the message list for the Planner LLM call."""
    checklist = build_checklist(session.task_spec)

    system_content = _PLANNER_SYSTEM_PROMPT.format(
        task_spec_json=session.task_spec.model_dump_json(indent=2),
        missing_required=checklist.missing_required or "none",
        missing_recommended=checklist.missing_recommended or "none",
        ready=checklist.required_satisfied,
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    for msg in session.messages:
        if msg.role == "system":
            continue  # System context is in the system prompt
        role = "assistant" if msg.role == "assistant" else "user"
        messages.append({"role": role, "content": msg.content})

    return messages


def _extract_task_spec_updates(response_text: str) -> dict[str, Any]:
    """Extract TaskSpec field updates from the Planner's JSON response.

    The Planner is instructed to include a JSON block at the end of
    its response. This extracts and parses it.
    """
    # Look for ```json ... ``` block
    pattern = r"```json\s*\n?(.+?)\n?```"
    match = re.search(pattern, response_text, re.DOTALL)
    if not match:
        # Try bare JSON object as fallback
        pattern_bare = r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})\s*$"
        match = re.search(pattern_bare, response_text, re.DOTALL)
        if not match:
            return {}

    try:
        data = json.loads(match.group(1).strip())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, IndexError):
        logger.debug("Failed to parse planner JSON response")

    return {}


# Fields on TaskSpec that expect list[str] values.
_LIST_FIELDS = frozenset({
    "languages", "frameworks", "acceptance_criteria",
    "constraints", "related_files",
})


def _apply_spec_updates(task_spec: TaskSpec, updates: dict[str, Any]) -> TaskSpec:
    """Apply extracted updates to a TaskSpec, preserving existing values.

    Only overwrites fields that are present in updates, non-empty,
    and pass type validation. List fields accept strings (coerced to
    single-element lists) or lists of strings. String fields accept
    only strings.
    """
    valid_fields = set(TaskSpec.model_fields.keys())

    for field_name, value in updates.items():
        if field_name not in valid_fields:
            continue
        if field_name == "workspace":
            continue  # Never override workspace from LLM output
        if value is None or value == "" or value == []:
            continue

        # Type validation: list fields vs string fields
        if field_name in _LIST_FIELDS:
            if isinstance(value, str):
                value = [value]  # Coerce single string to list
            if not isinstance(value, list) or not all(
                isinstance(v, str) and v.strip() for v in value
            ):
                logger.debug(
                    "Skipping invalid list value for %s: %s",
                    field_name, type(value).__name__,
                )
                continue
        elif not isinstance(value, str):
            logger.debug(
                "Skipping non-string value for %s: %s",
                field_name, type(value).__name__,
            )
            continue

        setattr(task_spec, field_name, value)

    return task_spec


def _strip_code_blocks(text: str) -> str:
    """Remove ALL fenced code blocks from the Planner's response for display.

    Strips any ```...``` blocks (json, python, or unlabelled). The TaskSpec
    JSON is extracted separately and shown in the checklist header preview,
    so code blocks in chat are redundant noise.
    """
    # Remove all fenced code blocks (``` optionally followed by a language tag)
    cleaned = re.sub(
        r"\n?```[a-zA-Z]*\s*\n?.+?\n?```\s*",
        "",
        text,
        flags=re.DOTALL,
    )
    return cleaned.strip()


async def send_planner_message(
    session: PlannerSession,
    user_message: str,
) -> PlannerResponse:
    """Send a user message to the Planner and get a response.

    This is the core Planner interaction loop:
    1. Add user message to session history
    2. Build LLM messages with current TaskSpec context
    3. Call the Planner LLM
    4. Extract TaskSpec updates from the response
    5. Update checklist and return response

    Raises ImportError if the required LLM provider is not installed.
    """
    session.touch()

    # Add user message
    session.messages.append(
        PlannerMessage(role="user", content=user_message)
    )

    # Build messages for LLM
    llm_messages = _build_planner_messages(session)

    # Call the Planner LLM
    model_name = _get_planner_model_name()
    response_text = await _call_planner_llm(model_name, llm_messages)

    # Extract and apply TaskSpec updates
    updates = _extract_task_spec_updates(response_text)
    if updates:
        _apply_spec_updates(session.task_spec, updates)

    # Update checklist
    session.checklist = build_checklist(session.task_spec)

    # Store assistant response (without code blocks for display)
    display_text = _strip_code_blocks(response_text)
    session.messages.append(
        PlannerMessage(role="assistant", content=display_text)
    )

    # Build warnings
    warnings: list[str] = []
    if session.checklist.has_warnings:
        for field in session.checklist.missing_recommended:
            warnings.append(
                f"Recommended field '{field}' is not set. "
                f"Task may have reduced success rate."
            )

    # Check minimal-input condition
    if (
        session.task_spec.objective
        and len(session.task_spec.objective.split()) < 10
        and not session.task_spec.acceptance_criteria
    ):
        warnings.append(
            "Task objective is minimal — success may be impacted. "
            "Consider adding acceptance criteria or more detail."
        )

    return PlannerResponse(
        session_id=session.session_id,
        message=display_text,
        task_spec=session.task_spec,
        checklist=session.checklist,
        ready=session.checklist.required_satisfied,
        warnings=warnings,
    )


async def _call_planner_llm(
    model_name: str,
    messages: list[dict[str, str]],
) -> str:
    """Call the Planner LLM. Supports Gemini and Anthropic models.

    This function is intentionally simple — no tools, no streaming.
    The Planner is a lightweight validation agent, not a code generator.
    """
    if model_name.startswith("gemini"):
        return await _call_gemini(model_name, messages)
    elif model_name.startswith("claude"):
        return await _call_anthropic(model_name, messages)
    else:
        # Fallback: try langchain-community ChatModel
        return await _call_langchain_generic(model_name, messages)


def _extract_text_from_content(content: Any) -> str:
    """Extract plain text from LLM response content.

    Handles multiple response formats:
    - str: returned as-is (Gemini 2.x, most models)
    - list of content blocks: concatenates text from all blocks
      with type='text' (Gemini 3.x, some Anthropic responses)
      e.g. [{'type': 'text', 'text': '...', 'extras': {...}}]
    - other: falls back to str() conversion
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif isinstance(block, str):
                text_parts.append(block)
        if text_parts:
            return "\n".join(text_parts)

    # Last resort — should not normally reach here
    logger.warning(
        "Unexpected LLM response content type: %s", type(content).__name__
    )
    return str(content)


async def _call_gemini(
    model_name: str,
    messages: list[dict[str, str]],
) -> str:
    """Call Google Gemini via langchain-google-genai."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(
        model=model_name,
        temperature=0.3,
        max_output_tokens=1024,
    )
    lc_messages = _to_langchain_messages(messages)
    response = await llm.ainvoke(lc_messages)
    return _extract_text_from_content(response.content)


async def _call_anthropic(
    model_name: str,
    messages: list[dict[str, str]],
) -> str:
    """Call Anthropic Claude via langchain-anthropic."""
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(
        model=model_name,
        temperature=0.3,
        max_tokens=1024,
    )
    lc_messages = _to_langchain_messages(messages)
    response = await llm.ainvoke(lc_messages)
    return _extract_text_from_content(response.content)


async def _call_langchain_generic(
    model_name: str,
    messages: list[dict[str, str]],
) -> str:
    """Fallback: attempt via langchain ChatOpenAI (OpenAI-compatible)."""
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=model_name,
        temperature=0.3,
        max_tokens=1024,
    )
    lc_messages = _to_langchain_messages(messages)
    response = await llm.ainvoke(lc_messages)
    return _extract_text_from_content(response.content)


def _to_langchain_messages(
    messages: list[dict[str, str]],
) -> list:
    """Convert dict messages to LangChain message objects."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    lc_messages = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=content))
    return lc_messages


# ---------------------------------------------------------------------------
# Session store (in-memory, with TTL cleanup)
# ---------------------------------------------------------------------------


class PlannerSessionStore:
    """In-memory store for planner sessions with TTL cleanup.

    Sessions expire after SESSION_TTL_SECONDS of inactivity.
    Cleanup runs lazily on get/create operations.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, PlannerSession] = {}

    def create(self, session: PlannerSession) -> PlannerSession:
        """Store a new session."""
        self._cleanup_expired()
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> PlannerSession | None:
        """Retrieve a session by ID. Returns None if expired or not found."""
        self._cleanup_expired()
        session = self._sessions.get(session_id)
        if session and session.is_expired:
            del self._sessions[session_id]
            logger.info("Session %s expired (idle >%ds)", session_id, SESSION_TTL_SECONDS)
            return None
        return session

    def remove(self, session_id: str) -> bool:
        """Remove a session. Returns True if found and removed."""
        return self._sessions.pop(session_id, None) is not None

    @property
    def count(self) -> int:
        """Number of active (non-expired) sessions."""
        self._cleanup_expired()
        return len(self._sessions)

    def _cleanup_expired(self) -> None:
        """Remove all expired sessions."""
        expired = [
            sid for sid, session in self._sessions.items()
            if session.is_expired
        ]
        for sid in expired:
            del self._sessions[sid]
            logger.debug("Cleaned up expired session: %s", sid)


# Singleton session store
planner_sessions = PlannerSessionStore()
