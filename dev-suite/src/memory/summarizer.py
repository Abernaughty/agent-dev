"""Mini-summarizer — deduplicates and compresses memory writes.

Called inside flush_memory before writing to the store. Takes raw
memory_writes accumulated during a task and produces a consolidated
set of key facts.

Graceful fallback: if the LLM call fails, returns the raw writes
unchanged so no data is lost.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _build_summarizer_prompt(raw_writes: list[dict]) -> str:
    """Build the system+user prompt for the summarizer LLM call."""
    return (
        "You are a memory summarizer for an AI agent team. "
        "Given a list of raw memory entries from a completed task, "
        "produce a deduplicated and concise set of key facts worth remembering.\n\n"
        "Rules:\n"
        "- Remove duplicate or near-duplicate entries\n"
        "- Merge related entries into single, comprehensive entries\n"
        "- Preserve constraints and discoveries (these are high-value)\n"
        "- Drop trivial entries (e.g., 'started task', 'installed package')\n"
        "- Keep the tier, module, and source_agent from the original entries\n"
        "- If entries conflict, keep the most recent one\n\n"
        "Respond with ONLY a valid JSON array of objects, each with:\n"
        '  {"content": "string", "tier": "l1"|"l2"|"l0-discovered", '
        '"module": "string", "source_agent": "string", '
        '"confidence": 0.0-1.0, "related_files": "string"}\n\n'
        "Do not include any text before or after the JSON array.\n\n"
        f"Raw entries ({len(raw_writes)} total):\n"
        f"{json.dumps(raw_writes, indent=2)}"
    )


def _parse_summarizer_response(raw_text: str) -> list[dict]:
    """Parse the summarizer LLM response into a list of dicts."""
    text = raw_text.strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try code fence extraction
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:
            inner = part.strip()
            if inner.startswith("json"):
                inner = inner[4:].strip()
            try:
                result = json.loads(inner)
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                continue

    # Try scan for first [ and last ]
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        try:
            result = json.loads(text[first_bracket : last_bracket + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError(
        f"No valid JSON array found in summarizer response ({len(text)} chars)",
        text,
        0,
    )


def summarize_writes_sync(
    raw_writes: list[dict],
    model: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """Synchronously summarize raw memory writes using an LLM.

    Args:
        raw_writes: List of memory write dicts with content, tier, module, etc.
        model: Override model name (default: DEVELOPER_MODEL env or claude-sonnet-4-20250514).
        api_key: Override API key (default: ANTHROPIC_API_KEY env).

    Returns:
        Consolidated list of memory write dicts. On failure, returns raw_writes unchanged.
    """
    if not raw_writes:
        return []

    # Skip summarization for small batches (not worth the LLM call)
    if len(raw_writes) <= 2:
        logger.info("[SUMMARIZER] %d entries - too few to summarize, returning as-is", len(raw_writes))
        return raw_writes

    model_name = model or os.getenv("DEVELOPER_MODEL", "claude-sonnet-4-20250514")
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")

    if not key:
        logger.warning("[SUMMARIZER] No ANTHROPIC_API_KEY - returning raw writes")
        return raw_writes

    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatAnthropic(
            model=model_name,
            api_key=key,
            temperature=0.0,
            max_tokens=4096,
        )

        prompt = _build_summarizer_prompt(raw_writes)
        response = llm.invoke([
            SystemMessage(content="You are a precise JSON-only summarizer."),
            HumanMessage(content=prompt),
        ])

        # Extract text content
        content = response.content
        if isinstance(content, list):
            content = "\n".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )

        consolidated = _parse_summarizer_response(content)
        logger.info(
            "[SUMMARIZER] Compressed %d entries -> %d entries",
            len(raw_writes),
            len(consolidated),
        )
        return consolidated

    except Exception as e:
        logger.warning(
            "[SUMMARIZER] LLM call failed (%s), returning raw writes", e
        )
        return raw_writes
