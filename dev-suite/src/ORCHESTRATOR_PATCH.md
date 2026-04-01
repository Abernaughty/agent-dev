# Orchestrator Patch Required (Issue #92)

The orchestrator.py file is too large (35KB) for the GitHub MCP push tools.
Apply these two changes locally:

## 1. Add `memory_writes_flushed` to GraphState

In `class GraphState(TypedDict, total=False):`, add:
```python
    memory_writes_flushed: list[dict]
```

## 2. Return consolidated entries from flush_memory_node

In `flush_memory_node()`, change the final return from:
```python
    return {"trace": trace}
```
to:
```python
    return {"trace": trace, "memory_writes_flushed": consolidated}
```

This ensures the runner can read flushed entries and bridge them
into StateManager for dashboard display.

Without this patch, all other fixes work correctly — the memory
panel just won't populate from flush_memory (it will show entries
added via other paths or on next GET /memory refresh).
