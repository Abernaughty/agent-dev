"""LangGraph orchestrator - Architect -> Lead Dev -> QA loop.

This is the main entry point for the agent workflow.
Implementation in Step 4.
"""

# TODO Step 4: Define LangGraph state machine
# - AgentState schema (task, blueprint, code, test_results, retry_count)
# - Architect node: generates structured Blueprint JSON
# - Lead Dev node: executes Blueprint, writes code
# - QA node: runs tests, produces structured failure report
# - Conditional edges: QA pass -> done, QA fail -> retry or escalate
# - Human escalation webhook on budget/retry exhaustion
