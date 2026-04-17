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

from ..tools.github_fetch import extract_github_refs, fetch_refs_as_context_items

logger = logging.getLogger(__name__)

# Default model for the Planner agent — lightweight and cheap.
# Override via PLANNER_MODEL env var.
DEFAULT_PLANNER_MODEL = "gemini-3.1-flash-lite-preview"

# Session TTL in seconds (30 minutes idle timeout)
SESSION_TTL_SECONDS = 30 * 60

# Issue #193: cap GitHub refs pre-fetched per Planner message. Keeps
# the context injection bounded if the user pastes a long list of refs.
PLANNER_MAX_GITHUB_REFS = 5

# Planner read-only tool loop turn cap. Intentionally generous — the
# cap exists as a safety net against runaway loops, not as a cost shape.
# Real tasks have been observed taking 6+ calls for well-documented
# issues and 10-20+ for complex ones. 50 is high enough that realistic
# work will never hit it and low enough that a pathological infinite
# loop still terminates in bounded wall time. Tune via the env var if
# the default proves too low or too high — we'll adjust as we get data.
try:
    MAX_PLANNER_TOOL_TURNS = int(os.getenv("MAX_PLANNER_TOOL_TURNS", "50"))
except ValueError:
    MAX_PLANNER_TOOL_TURNS = 50

# Issue #193: loose heuristic for "the user mentioned a `#N` ref but we
# couldn't resolve it." Used only for warning diagnostics when the
# precise `extract_github_refs` returns nothing because no default
# owner/repo is configured. Intentionally wider than the real pattern.
_LOOSE_HASH_REF_RE = re.compile(r"(?<![\w&])#\d+\b")


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
    # Issue #193: Planner-side pre-fetched GitHub issue/PR summaries,
    # shape matches `gathered_context` entries. Passed through to the
    # orchestrator on submit so the Architect doesn't re-fetch.
    github_context: list[dict] = Field(default_factory=list)

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
    # Issue #193: default GitHub repo for resolving same-repo refs like
    # "Issue #113" in user messages. Populated from the dashboard repo
    # picker (REMOTE mode) or auto-detected from `.git/config` for LOCAL
    # workspaces. Falls back to GITHUB_OWNER/GITHUB_REPO env vars when
    # both the session value and auto-detect come up empty.
    github_repo: str | None = None

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


_GIT_REMOTE_GITHUB_RE = re.compile(
    r"""
    (?:git@github\.com:|https?://(?:[^@/]+@)?github\.com/)  # ssh or https prefix
    (?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)                  # owner/repo
    (?:\.git)?/?\s*$                                       # optional .git + trailing slash
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _detect_github_repo_from_workspace(workspace: str) -> str | None:
    """Best-effort parse of `.git/config` for the origin GitHub repo.

    Returns "owner/repo" or None. Handles both SSH (`git@github.com:o/r.git`)
    and HTTPS (`https://github.com/o/r(.git)`) remote formats. Never raises.
    """
    if not workspace:
        return None
    try:
        config_path = Path(workspace) / ".git" / "config"
        if not config_path.is_file():
            return None
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    in_origin = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_origin = line.lower() == '[remote "origin"]'
            continue
        if not in_origin:
            continue
        if line.lower().startswith("url"):
            _, _, value = line.partition("=")
            match = _GIT_REMOTE_GITHUB_RE.search(value.strip())
            if match:
                owner = match.group("owner")
                repo = match.group("repo")
                if repo.endswith(".git"):
                    repo = repo[:-4]
                return f"{owner}/{repo}"
    return None


def create_planner_session(
    workspace: str,
    languages: list[str] | None = None,
    frameworks: list[str] | None = None,
    github_repo: str | None = None,
) -> PlannerSession:
    """Create a new planner session with optional pre-populated fields.

    The workspace is always set. Languages and frameworks can be
    pre-populated from auto-inference (infer_workspace_stack).

    If ``github_repo`` is not provided, attempts to auto-detect it by
    parsing ``remote.origin.url`` from the workspace's ``.git/config``.
    The resolved repo is used as the default owner/repo when the Planner
    pre-fetches issue/PR refs like "Issue #113" from user messages.
    """
    task_spec = TaskSpec(
        workspace=workspace,
        languages=languages or [],
        frameworks=frameworks or [],
    )
    checklist = build_checklist(task_spec)

    resolved_repo = github_repo or _detect_github_repo_from_workspace(workspace)

    session = PlannerSession(
        task_spec=task_spec,
        checklist=checklist,
        github_repo=resolved_repo,
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

You have read-only access to the workspace filesystem via tools \
(filesystem_list, filesystem_read) and may also have github_read_diff. \
Use these tools whenever doing so would avoid asking the user a question \
you can answer yourself — e.g. finding a file path, confirming a \
framework, or reading a config/package manifest to understand the stack. \
Do NOT ask the user for information the filesystem can tell you. Use \
tools sparingly and with purpose — one or two quick lookups per turn is \
typical; do not exhaustively crawl the repo. If tools aren't available \
this turn (no provider configured), fall back to asking the user.

When the user references a GitHub issue or PR (e.g. "fix #42", "review \
issue #113"), the orchestrator deterministically pre-fetches the issue/PR \
body and injects it as a message labelled "=== PRE-FETCHED GITHUB CONTEXT \
===" below. Treat that block as authoritative source material — never \
tell the user you cannot access GitHub when that block is present; \
summarise its contents and move the task forward.

CRITICAL anti-hallucination rule: if NO "=== PRE-FETCHED GITHUB CONTEXT ===" \
block is present below, the pre-fetch did not run (missing token, wrong \
repo configured, or network error). In that case you MUST NOT invent, \
guess, or describe the issue/PR contents — doing so produces confidently \
wrong task specs. Instead, briefly tell the user the context wasn't \
injected and ask them to paste the issue title and body, or confirm the \
repo configuration.

You also work with information the user provides and any auto-detected \
project context.

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


def _format_github_context_block(github_context: list[dict]) -> str:
    """Render pre-fetched GitHub issue/PR summaries for the system prompt.

    Returns a human-readable block of summaries, or an empty string
    when there's nothing to show. Called from `_build_planner_messages`
    so the Planner LLM can reference issue/PR content that the user
    mentioned without asking the user to paste it.
    """
    if not github_context:
        return ""
    sections: list[str] = [
        "=== PRE-FETCHED GITHUB CONTEXT ===",
        "The orchestrator already fetched the GitHub issue/PR the user "
        "mentioned. Use these summaries to orient the task. Do not tell "
        "the user you cannot access GitHub — the content is below.",
    ]
    body_count = 0
    for item in github_context:
        if not isinstance(item, dict):
            continue
        path = item.get("path") or "github://unknown"
        content = (item.get("content") or "").strip()
        if not content:
            continue
        sections.append(f"--- {path} ---\n{content}")
        body_count += 1
    if body_count == 0:
        return ""
    sections.append("=== END PRE-FETCHED GITHUB CONTEXT ===")
    return "\n\n".join(sections)


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

    github_block = _format_github_context_block(session.task_spec.github_context)
    if github_block:
        messages.append({"role": "system", "content": github_block})

    for msg in session.messages:
        if msg.role == "system":
            continue  # System context is in the system prompt
        role = "assistant" if msg.role == "assistant" else "user"
        messages.append({"role": role, "content": msg.content})

    return messages


async def _prefetch_github_refs_for_message(
    session: PlannerSession,
    user_message: str,
) -> list[dict]:
    """Deterministically pre-fetch GitHub refs mentioned in the user message.

    Populates `session.task_spec.github_context` with gathered_context-
    shaped dicts. Dedupes against refs already in the session (so follow-up
    messages that mention the same issue don't re-fetch). Best-effort:
    missing GITHUB_TOKEN or network errors quietly return no new items.
    """
    # Resolve default owner/repo for same-repo refs ("Issue #113" without
    # an owner/repo prefix). Session-level value wins — that's what the
    # dashboard repo picker or the git-remote auto-detect stored.
    # Env vars are a fallback for CLI/testing scenarios.
    default_owner = ""
    default_repo = ""
    source_repo = session.github_repo or ""
    if source_repo and "/" in source_repo:
        default_owner, _, default_repo = source_repo.partition("/")
    if not default_owner or not default_repo:
        default_owner = os.getenv("GITHUB_OWNER", "") or default_owner
        default_repo = os.getenv("GITHUB_REPO", "") or default_repo

    detected_refs = extract_github_refs(
        user_message,
        default_owner=default_owner,
        default_repo=default_repo,
        max_refs=PLANNER_MAX_GITHUB_REFS,
    )

    # Loose heuristic: catch the case where the user clearly referenced
    # something like "Issue #113" but we had no default repo configured
    # to resolve it. `extract_github_refs` drops same-repo refs silently
    # when default_owner/repo are empty, so we'd otherwise have no
    # breadcrumb in the logs pointing at the missing config.
    if not detected_refs and (not default_owner or not default_repo):
        if _LOOSE_HASH_REF_RE.search(user_message):
            logger.warning(
                "[PLANNER] Session %s message contains '#N' refs but no "
                "GitHub repo is configured — pre-fetch skipped. Set the "
                "dashboard repo picker, ensure the workspace has a GitHub "
                "`remote.origin.url`, or set GITHUB_OWNER/GITHUB_REPO.",
                session.session_id,
            )

    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        if detected_refs:
            logger.warning(
                "[PLANNER] Detected %d GitHub ref(s) in session %s but "
                "GITHUB_TOKEN is not set — pre-fetch skipped",
                len(detected_refs), session.session_id,
            )
        return []

    existing_paths = {
        item.get("path") for item in session.task_spec.github_context
        if isinstance(item, dict) and item.get("path")
    }

    items = await fetch_refs_as_context_items(
        user_message,
        default_owner=default_owner,
        default_repo=default_repo,
        token=token,
        max_refs=PLANNER_MAX_GITHUB_REFS,
    )
    if detected_refs and not items:
        logger.warning(
            "[PLANNER] Detected %d GitHub ref(s) in session %s but "
            "fetch returned 0 items (bad token, 404, or network error)",
            len(detected_refs), session.session_id,
        )
    new_items = [
        item for item in items
        if item.get("path") and item["path"] not in existing_paths
    ]
    if new_items:
        session.task_spec.github_context.extend(new_items)
        logger.info(
            "[PLANNER] Pre-fetched %d GitHub ref(s) for session %s",
            len(new_items), session.session_id,
        )
    return new_items


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
        if field_name == "github_context":
            continue  # Populated deterministically by pre-fetch, not LLM
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

    # Issue #193: deterministic GitHub pre-fetch. Scans the user's
    # message for issue/PR refs and populates task_spec.github_context
    # before the LLM sees the turn so the Planner can orient itself
    # without consuming tool tokens.
    try:
        await _prefetch_github_refs_for_message(session, user_message)
    except Exception as exc:  # noqa: BLE001 - best-effort pre-fetch
        logger.debug("[PLANNER] GitHub pre-fetch failed: %s", exc)

    # Build messages for LLM
    llm_messages = _build_planner_messages(session)

    # Read-only tools scoped to this session's workspace. Best-effort —
    # returns [] when no provider is configured, letting the Planner
    # gracefully degrade to the no-tool path for CLI/test scenarios.
    tools = _get_planner_readonly_tools(session.task_spec.workspace)

    # Turn-start diagnostic log. Gives us a single line to correlate
    # subsequent tool-loop output against — session id, model, workspace,
    # how many read-only tools loaded, and the user message preview.
    model_name = _get_planner_model_name()
    logger.info(
        "[PLANNER] turn start: session=%s model=%s workspace=%s "
        "tools_loaded=%d github_context=%d user_msg=%s",
        session.session_id,
        model_name,
        session.task_spec.workspace or "<unset>",
        len(tools),
        len(session.task_spec.github_context),
        (user_message[:120] + "...") if len(user_message) > 120 else user_message,
    )

    # Call the Planner LLM
    response_text = await _call_planner_llm(model_name, llm_messages, tools=tools)

    # Extract and apply TaskSpec updates
    updates = _extract_task_spec_updates(response_text)
    if updates:
        _apply_spec_updates(session.task_spec, updates)

    # Update checklist
    session.checklist = build_checklist(session.task_spec)

    # Store assistant response (without code blocks for display).
    # Guard against blank display_text if the LLM returned only a
    # fenced code block with no conversational text.
    display_text = _strip_code_blocks(response_text) or (
        "Task specification updated."
    )
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


def _get_planner_readonly_tools(workspace: str) -> list:
    """Best-effort: build the read-only tool set scoped to the session's
    workspace. Returns [] on any failure (no mcp-config.json, provider
    init error, filtered set empty) — the Planner still works via the
    GitHub pre-fetch alone, just without filesystem visibility.
    """
    if not workspace:
        logger.info(
            "[PLANNER] No workspace set; skipping tool provider init"
        )
        return []
    try:
        # Imported here to avoid a top-level dependency cycle — the tools
        # module pulls in provider + bridge, which aren't needed when
        # Planner is used in isolation (tests, CLI smoke).
        from ..orchestrator import _get_mcp_config_path
        from ..tools import create_provider, load_mcp_config
        from ..tools.mcp_bridge import READONLY_TOOLS, get_tools
    except Exception as exc:  # noqa: BLE001
        logger.info("[PLANNER] Tool imports failed: %s", exc)
        return []

    try:
        config_path = _get_mcp_config_path()
        if not config_path.is_file():
            logger.info(
                "[PLANNER] No mcp-config.json at %s; tools disabled",
                config_path,
            )
            return []
        mcp_config = load_mcp_config(str(config_path))
        provider = create_provider(mcp_config, workspace)
        tools = get_tools(provider, tool_filter=READONLY_TOOLS)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[PLANNER] Tool provider init failed for workspace %s: %s",
            workspace, exc,
        )
        return []

    return list(tools)


async def _call_planner_llm(
    model_name: str,
    messages: list[dict[str, str]],
    tools: list | None = None,
) -> str:
    """Call the Planner LLM, optionally with a read-only tool loop.

    When ``tools`` is non-empty, the LLM is given ``bind_tools(tools)``
    and a bounded tool-calling loop (``MAX_PLANNER_TOOL_TURNS`` turns)
    so the Planner can look up file paths / read configs without
    asking the user. When empty, falls back to a single no-tool call.
    """
    if model_name.startswith("gemini"):
        return await _call_gemini(model_name, messages, tools=tools)
    elif model_name.startswith("claude"):
        return await _call_anthropic(model_name, messages, tools=tools)
    else:
        # Fallback: try langchain-community ChatModel
        return await _call_langchain_generic(model_name, messages, tools=tools)


async def _invoke_with_optional_tools(
    llm,
    lc_messages: list,
    tools: list | None,
) -> str:
    """Shared helper: run the LLM with or without a tool loop.

    Centralizes the tool-loop logic so all three LLM call paths
    (Gemini, Anthropic, generic) behave identically when tools are
    available. Best-effort — if the loop raises, logs and falls back
    to a single tool-free ainvoke.
    """
    if not tools:
        response = await llm.ainvoke(lc_messages)
        return _extract_text_from_content(response.content)

    try:
        from ..orchestrator import _run_tool_loop
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PLANNER] Could not import tool loop: %s", exc)
        response = await llm.ainvoke(lc_messages)
        return _extract_text_from_content(response.content)

    try:
        llm_with_tools = llm.bind_tools(tools)
        response, _tokens, _log, loop_messages = await _run_tool_loop(
            llm_with_tools,
            lc_messages,
            tools,
            max_turns=MAX_PLANNER_TOOL_TURNS,
            tokens_used=0,
            trace=[],
            agent_name="planner",
            return_messages=True,
        )
        text = _extract_text_from_content(response.content)
        if text.strip():
            return text

        # Loop exhausted mid-tool-call — the returned response is a pure
        # tool_use block with no user-facing text. Build a wrap-up call
        # that preserves the REAL tool results the loop gathered so the
        # LLM doesn't have to fabricate. We must drop the unresolved
        # final assistant message (its tool_use blocks have no paired
        # tool_result; Anthropic would reject the sequence). Then
        # append an explicit "no more tools" instruction and a final
        # user prompt so the conversation ends on a user turn.
        wrap_up_messages = list(loop_messages)
        # Drop only the single trailing assistant message if it has
        # unresolved tool_use blocks. Earlier paired tool_call /
        # tool_result messages are valid context — we want to keep
        # them so the wrap-up LLM has real data to summarise. Looping
        # would erase those pairs too.
        if _is_unresolved_tail(wrap_up_messages):
            wrap_up_messages.pop()

        from langchain_core.messages import HumanMessage
        nudge = (
            "You've used your tool budget for this turn. Do NOT call or "
            "simulate any tools. Do NOT write `<tool_call>` tags, JSON "
            "tool-call syntax, or any tool-invocation-shaped text. "
            "Using ONLY what you've already learned from the tool "
            "results above in this conversation, produce the final "
            "conversational task-spec response per the system prompt, "
            "including the hidden JSON extraction block at the end. "
            "If the tool results were insufficient to answer, say so "
            "plainly and ask the user for the missing information — "
            "do not invent file paths, frameworks, or content."
        )
        wrap_up_messages.append(HumanMessage(content=nudge))

        logger.info(
            "[PLANNER] Wrap-up triggered: loop_messages=%d -> wrap_up=%d "
            "(dropped %d unresolved tail message(s))",
            len(loop_messages),
            len(wrap_up_messages),
            len(loop_messages) - (len(wrap_up_messages) - 1),
        )

        final_response = await llm.ainvoke(wrap_up_messages)
        final_text = _extract_text_from_content(final_response.content)
        if not final_text.strip():
            logger.warning(
                "[PLANNER] Wrap-up call also returned empty/non-text "
                "(content type=%s); falling through to empty reply",
                type(final_response.content).__name__,
            )
        return final_text
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PLANNER] Tool loop failed (%s); falling back to no-tool call",
            exc,
        )
        response = await llm.ainvoke(lc_messages)
        return _extract_text_from_content(response.content)


def _is_unresolved_tail(messages: list) -> bool:
    """True if the last message in a sequence is a tool_use-bearing
    assistant message with no paired tool_result following it, OR a
    standalone tool_result with no assistant message preceding it.
    Used to trim the sequence before a wrap-up call so the message
    history is valid for Anthropic's paired-block requirement.
    """
    if not messages:
        return False
    from langchain_core.messages import AIMessage, ToolMessage
    last = messages[-1]
    if isinstance(last, ToolMessage):
        # Rare: ends on a tool result with nothing consuming it.
        # Safe to drop.
        return True
    if isinstance(last, AIMessage):
        content = getattr(last, "content", None)
        tool_calls = getattr(last, "tool_calls", None) or []
        if tool_calls:
            return True
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    return True
    return False


def _extract_text_from_content(content: Any) -> str:
    """Extract plain text from LLM response content.

    Handles multiple response formats:
    - str: returned as-is (Gemini 2.x, most models)
    - list of content blocks: concatenates text from all blocks
      with type='text' (Gemini 3.x, some Anthropic responses)
      e.g. [{'type': 'text', 'text': '...', 'extras': {...}}]
    - list of only tool_use blocks (Anthropic mid-loop): returns ""
      rather than dumping the raw dicts to the user.
    - other: falls back to str() conversion
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        if not content:
            return ""
        text_parts = []
        unknown_block_seen = False
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype in ("tool_use", "tool_result", "input_json_delta"):
                    # Intermediate tool-calling blocks — not user-facing
                    # text. Silently skip so we don't render raw dicts.
                    continue
                else:
                    unknown_block_seen = True
            elif isinstance(block, str):
                text_parts.append(block)
            else:
                unknown_block_seen = True
        if text_parts:
            return "\n".join(text_parts)
        if not unknown_block_seen:
            # Pure tool_use / empty-recognized response — no text to
            # surface. Return empty string; caller decides how to
            # handle (typically: force a final no-tool call).
            return ""

    # Last resort — should not normally reach here
    logger.warning(
        "Unexpected LLM response content type: %s", type(content).__name__
    )
    return str(content)


async def _call_gemini(
    model_name: str,
    messages: list[dict[str, str]],
    tools: list | None = None,
) -> str:
    """Call Google Gemini via langchain-google-genai, optionally with tools."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(
        model=model_name,
        temperature=0.3,
        max_output_tokens=1024,
    )
    lc_messages = _to_langchain_messages(messages)
    return await _invoke_with_optional_tools(llm, lc_messages, tools)


async def _call_anthropic(
    model_name: str,
    messages: list[dict[str, str]],
    tools: list | None = None,
) -> str:
    """Call Anthropic Claude via langchain-anthropic, optionally with tools."""
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(
        model=model_name,
        temperature=0.3,
        max_tokens=1024,
    )
    lc_messages = _to_langchain_messages(messages)
    return await _invoke_with_optional_tools(llm, lc_messages, tools)


async def _call_langchain_generic(
    model_name: str,
    messages: list[dict[str, str]],
    tools: list | None = None,
) -> str:
    """Fallback: attempt via langchain ChatOpenAI, optionally with tools."""
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=model_name,
        temperature=0.3,
        max_tokens=1024,
    )
    lc_messages = _to_langchain_messages(messages)
    return await _invoke_with_optional_tools(llm, lc_messages, tools)


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
