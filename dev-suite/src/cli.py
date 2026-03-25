"""CLI entry point for the Dev Suite orchestrator.

Provides the `dev-suite` console script with subcommands:
    dev-suite run "Create a function that..." [OPTIONS]
    dev-suite --help
    dev-suite --version

Modes:
    run             Full agent workflow (Architect → Lead Dev → QA)
    run --dry-run   Validate config, show execution plan (zero API calls)
    run --plan      Run Architect only, show Blueprint (one API call)
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# ── Version ──

__version__ = "0.2.0"


# ── ANSI Colors ──
# Minimal color support for terminal output. Disabled when NO_COLOR is set
# or stdout is not a TTY.

def _colors_enabled() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class _Colors:
    """ANSI color codes, disabled gracefully."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def cyan(self, t: str) -> str:
        return self._wrap("36", t)

    def green(self, t: str) -> str:
        return self._wrap("32", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33", t)

    def red(self, t: str) -> str:
        return self._wrap("31", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def bold(self, t: str) -> str:
        return self._wrap("1", t)


C = _Colors(_colors_enabled())


# ── Config Validation ──

# Required env vars and their descriptions
_REQUIRED_KEYS = {
    "GOOGLE_API_KEY": "Gemini (Architect agent)",
    "ANTHROPIC_API_KEY": "Claude (Lead Dev + QA agents)",
    "E2B_API_KEY": "E2B sandbox execution",
}

# Optional env vars that enhance functionality
_OPTIONAL_KEYS = {
    "LANGFUSE_PUBLIC_KEY": "Langfuse observability (tracing)",
    "LANGFUSE_SECRET_KEY": "Langfuse observability (tracing)",
}


def _check_env_key(key: str) -> bool:
    """Check if an env var is set and not a placeholder."""
    val = os.getenv(key, "")
    return bool(val and not val.startswith("your-"))


def validate_config() -> dict:
    """Validate environment configuration.

    Returns a dict with:
        valid: bool - whether all required keys are present and config is clean
        missing: list[str] - missing required key names
        optional_missing: list[str] - missing optional key names
        errors: list[str] - config errors (e.g. invalid numeric values)
        models: dict - resolved model names
        budget: dict - token budget and retry config
    """
    missing = [k for k in _REQUIRED_KEYS if not _check_env_key(k)]
    optional_missing = [k for k in _OPTIONAL_KEYS if not _check_env_key(k)]

    models = {
        "architect": os.getenv("ARCHITECT_MODEL", "gemini-3-flash-preview"),
        "developer": os.getenv("DEVELOPER_MODEL", "claude-sonnet-4-20250514"),
        "qa": os.getenv("QA_MODEL", "claude-sonnet-4-20250514"),
    }

    budget: dict = {}
    errors: list[str] = []
    for key, default in [("TOKEN_BUDGET", 50000), ("MAX_RETRIES", 3)]:
        raw = os.getenv(key, str(default))
        try:
            budget[key.lower()] = int(raw)
        except ValueError:
            budget[key.lower()] = default
            errors.append(f"{key}={raw!r} is not a valid integer (using default {default})")

    return {
        "valid": len(missing) == 0 and len(errors) == 0,
        "missing": missing,
        "optional_missing": optional_missing,
        "errors": errors,
        "models": models,
        "budget": budget,
    }


# ── Output Formatting ──

def _print_header(text: str) -> None:
    """Print a section header."""
    print(f"\n{C.dim('─' * 50)}")
    print(f"  {C.bold(text)}")
    print(C.dim("─" * 50))


def _print_kv(key: str, value: str, indent: int = 2) -> None:
    """Print a key-value pair."""
    pad = " " * indent
    print(f"{pad}{C.dim(key + ':')} {value}")


def print_dry_run(config: dict, workspace: str, task: str) -> None:
    """Print dry-run output: config validation and execution plan."""
    _print_header("DRY RUN — Execution Plan")

    print(f"\n  {C.cyan('Task:')} {task[:120]}{'...' if len(task) > 120 else ''}")
    print(f"  {C.cyan('Workspace:')} {workspace}")

    # Models
    print(f"\n  {C.bold('Models')}")
    for role, model in config["models"].items():
        _print_kv(f"  {role.capitalize()}", model, indent=2)

    # Budget
    print(f"\n  {C.bold('Budget')}")
    _print_kv("  Token ceiling", f"{config['budget']['token_budget']:,}", indent=2)
    _print_kv("  Max retries", str(config["budget"]["max_retries"]), indent=2)

    # API Keys
    print(f"\n  {C.bold('API Keys')}")
    for key, desc in _REQUIRED_KEYS.items():
        if _check_env_key(key):
            print(f"    {C.green('✓')} {desc} ({key})")
        else:
            print(f"    {C.red('✗')} {desc} ({key}) — {C.red('MISSING')}")

    for key, desc in _OPTIONAL_KEYS.items():
        if _check_env_key(key):
            print(f"    {C.green('✓')} {desc} ({key})")
        else:
            print(f"    {C.yellow('○')} {desc} ({key}) — optional")

    # Tracing
    tracing_ok = all(_check_env_key(k) for k in _OPTIONAL_KEYS)
    print(f"\n  {C.bold('Tracing')}")
    langfuse_status = C.green("enabled") if tracing_ok else C.yellow("disabled (keys missing)")
    _print_kv("  Langfuse", langfuse_status, indent=2)

    # Verdict
    config_errors = config.get("errors", [])
    if config_errors:
        print(f"\n  {C.bold('Config Errors')}")
        for err in config_errors:
            print(f"    {C.red('✗')} {err}")

    if config["valid"]:
        print(f"\n  {C.green('✓ All required keys present. Ready to run.')}")
    else:
        print(f"\n  {C.red('✗ Missing required API keys:')}")
        for key in config["missing"]:
            print(f"    → Set {C.bold(key)} in your .env file")
        print(f"\n  {C.dim('Copy .env.example to .env and fill in your keys.')}")


def print_blueprint(blueprint_data: dict, tokens_used: int, elapsed: float) -> None:
    """Print a Blueprint from --plan mode."""
    _print_header("PLAN — Architect Blueprint")

    print(f"\n  {C.cyan('Task ID:')} {blueprint_data.get('task_id', 'unknown')}")

    # Target files
    files = blueprint_data.get("target_files", [])
    if files:
        print(f"\n  {C.bold('Target Files')}")
        for f in files:
            print(f"    {C.cyan(f)}")

    # Instructions
    instructions = blueprint_data.get("instructions", "")
    if instructions:
        print(f"\n  {C.bold('Instructions')}")
        # Wrap long instructions
        for line in instructions.split("\n"):
            print(f"    {line}")

    # Constraints
    constraints = blueprint_data.get("constraints", [])
    if constraints:
        print(f"\n  {C.bold('Constraints')}")
        for c in constraints:
            print(f"    {C.yellow('•')} {c}")

    # Acceptance criteria
    criteria = blueprint_data.get("acceptance_criteria", [])
    if criteria:
        print(f"\n  {C.bold('Acceptance Criteria')}")
        for c in criteria:
            print(f"    {C.green('□')} {c}")

    # Stats
    print(f"\n  {C.dim(f'Tokens: {tokens_used:,} | Elapsed: {elapsed:.1f}s')}")

    # Raw JSON
    print(f"\n  {C.bold('Raw JSON')}")
    print(C.dim(json.dumps(blueprint_data, indent=2)))


def print_run_result(state, elapsed: float) -> None:
    """Print the result of a full run."""
    from .orchestrator import WorkflowStatus

    status = state.status
    is_pass = status == WorkflowStatus.PASSED

    _print_header("RUN COMPLETE" if is_pass else "RUN FINISHED")

    # Status
    status_str = {
        WorkflowStatus.PASSED: C.green("✓ PASSED"),
        WorkflowStatus.FAILED: C.red("✗ FAILED"),
        WorkflowStatus.ESCALATED: C.yellow("⚠ ESCALATED"),
    }.get(status, C.dim(str(status.value)))

    print(f"\n  {C.bold('Status:')} {status_str}")

    if state.error_message:
        print(f"  {C.red('Error:')} {state.error_message}")

    # Metrics
    print(f"\n  {C.bold('Metrics')}")
    budget = int(os.getenv("TOKEN_BUDGET", "50000"))
    pct = round((state.tokens_used / budget) * 100) if budget else 0
    cost_est = state.tokens_used * 0.000012  # rough estimate

    _print_kv("Tokens used", f"{state.tokens_used:,} / {budget:,} ({pct}%)")
    _print_kv("Estimated cost", f"${cost_est:.4f}")
    _print_kv("Retries", f"{state.retry_count} / {int(os.getenv('MAX_RETRIES', '3'))}")
    _print_kv("Elapsed", f"{elapsed:.1f}s")

    # Blueprint summary
    if state.blueprint:
        print(f"\n  {C.bold('Blueprint')}")
        _print_kv("Task ID", state.blueprint.task_id)
        _print_kv("Files", ", ".join(state.blueprint.target_files))

    # Trace URL hint
    if _check_env_key("LANGFUSE_PUBLIC_KEY"):
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        print(f"\n  {C.dim(f'Trace: {host} (check Langfuse dashboard)')}")

    # Trace log
    if state.trace:
        print(f"\n  {C.bold('Trace')}")
        for entry in state.trace:
            print(f"    {C.dim('→')} {entry}")


# ── Argument Parsing ──

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the dev-suite CLI."""
    parser = argparse.ArgumentParser(
        prog="dev-suite",
        description="Dev Suite — Stateful AI Workforce Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  dev-suite run "Create a login page with email/password auth"
  dev-suite run "Add rate limiting to /api/*" --plan
  dev-suite run "Refactor auth module" --dry-run
  dev-suite run "Build REST API" --budget 100000 --verbose""",
    )

    parser.add_argument(
        "--version", action="version", version=f"dev-suite {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -- run subcommand --
    run_parser = subparsers.add_parser(
        "run",
        help="Run a task through the agent workforce",
        description="Execute a task through the Architect → Lead Dev → QA pipeline.",
    )

    run_parser.add_argument(
        "task",
        type=str,
        help="Task description for the agents",
    )

    # Mode flags (mutually exclusive)
    mode_group = run_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and show execution plan (zero API calls)",
    )
    mode_group.add_argument(
        "--plan",
        action="store_true",
        help="Run Architect only, show Blueprint (one API call)",
    )

    # Model overrides
    run_parser.add_argument(
        "--model-architect",
        type=str,
        default=None,
        metavar="MODEL",
        help="Override Architect model (default: gemini-3-flash-preview)",
    )
    run_parser.add_argument(
        "--model-developer",
        type=str,
        default=None,
        metavar="MODEL",
        help="Override Lead Dev model (default: claude-sonnet-4-20250514)",
    )
    run_parser.add_argument(
        "--model-qa",
        type=str,
        default=None,
        metavar="MODEL",
        help="Override QA model (default: claude-sonnet-4-20250514)",
    )

    # Budget
    run_parser.add_argument(
        "--budget",
        type=int,
        default=None,
        metavar="TOKENS",
        help="Token budget ceiling (default: 50000)",
    )

    # Workspace
    run_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Workspace root directory (default: current directory)",
    )

    # Verbosity / tracing
    run_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Stream agent activity to stdout (DEBUG logging)",
    )
    run_parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Disable Langfuse tracing",
    )

    return parser


# ── Command Handlers ──

def _apply_overrides(args: argparse.Namespace) -> None:
    """Apply CLI flag overrides to environment variables.

    Model and budget overrides are set as env vars so they propagate
    to the orchestrator's LLM initialization and config.
    """
    if args.model_architect:
        os.environ["ARCHITECT_MODEL"] = args.model_architect
    if args.model_developer:
        os.environ["DEVELOPER_MODEL"] = args.model_developer
    if args.model_qa:
        os.environ["QA_MODEL"] = args.model_qa
    if args.budget is not None:
        os.environ["TOKEN_BUDGET"] = str(args.budget)


def _setup_logging(verbose: bool) -> None:
    """Configure logging level based on verbosity flag."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def handle_dry_run(args: argparse.Namespace) -> int:
    """Handle --dry-run: validate config, show plan, exit."""
    workspace = args.workspace or os.getcwd()

    if not Path(workspace).is_dir():
        print(f"{C.red('Error:')} Workspace path does not exist: {workspace}")
        return 1

    config = validate_config()
    print_dry_run(config, workspace, args.task)
    return 0 if config["valid"] else 1


def handle_plan(args: argparse.Namespace) -> int:
    """Handle --plan: run Architect only, show Blueprint."""
    # Validate workspace before anything else
    workspace = args.workspace or os.getcwd()
    if not Path(workspace).is_dir():
        print(f"{C.red('Error:')} Workspace path does not exist: {workspace}")
        return 1

    # Change to workspace so file/tool operations resolve correctly
    os.chdir(workspace)

    # Reload .env from the workspace directory so workspace-local
    # API keys are picked up (the initial load_dotenv() in main()
    # only reads from the original cwd).
    load_dotenv(override=True)

    # --plan only needs GOOGLE_API_KEY (Architect uses Gemini).
    # Don't gate on ANTHROPIC_API_KEY or E2B_API_KEY.
    if not _check_env_key("GOOGLE_API_KEY"):
        print(f"{C.red('Error:')} GOOGLE_API_KEY is required for --plan mode.")
        print(f"{C.dim('Run with --dry-run to see full config status.')}")
        return 1

    from .orchestrator import (
        GraphState,
        WorkflowStatus,
        architect_node,
    )
    from .tracing import create_trace_config

    enable_tracing = not args.no_trace
    trace_config = create_trace_config(
        enabled=enable_tracing,
        task_description=args.task,
    )

    # Build a minimal LangGraph with just the Architect
    from langgraph.graph import END, START, StateGraph

    plan_graph = StateGraph(GraphState)
    plan_graph.add_node("architect", architect_node)
    plan_graph.add_edge(START, "architect")
    plan_graph.add_edge("architect", END)
    workflow = plan_graph.compile()

    initial_state: GraphState = {
        "task_description": args.task,
        "blueprint": None,
        "generated_code": "",
        "failure_report": None,
        "status": WorkflowStatus.PLANNING,
        "retry_count": 0,
        "tokens_used": 0,
        "error_message": "",
        "memory_context": [],
        "trace": [],
    }

    invoke_config: dict = {"recursion_limit": 10}
    if trace_config.callbacks:
        invoke_config["callbacks"] = trace_config.callbacks

    start = time.time()
    try:
        result = workflow.invoke(initial_state, config=invoke_config)
    except Exception as e:
        print(f"{C.red('Error:')} Architect failed: {e}")
        return 1
    finally:
        trace_config.flush()

    elapsed = time.time() - start

    blueprint = result.get("blueprint")
    if not blueprint:
        error = result.get("error_message", "Unknown error")
        print(f"{C.red('Error:')} Architect did not produce a Blueprint: {error}")
        return 1

    print_blueprint(blueprint.model_dump(), result.get("tokens_used", 0), elapsed)
    return 0


def handle_run(args: argparse.Namespace) -> int:
    """Handle full run: Architect → Lead Dev → QA loop."""
    # Validate workspace before anything else
    workspace = args.workspace or os.getcwd()
    if not Path(workspace).is_dir():
        print(f"{C.red('Error:')} Workspace path does not exist: {workspace}")
        return 1

    # Change to workspace so file/tool operations resolve correctly
    os.chdir(workspace)

    # Reload .env from the workspace directory so workspace-local
    # API keys are picked up (the initial load_dotenv() in main()
    # only reads from the original cwd).
    load_dotenv(override=True)

    config = validate_config()
    if not config["valid"]:
        print(f"{C.red('Error:')} Missing required API keys: {', '.join(config['missing'])}")
        print(f"{C.dim('Run with --dry-run to see full config status.')}")
        return 1

    from .orchestrator import run_task

    enable_tracing = not args.no_trace
    start = time.time()

    try:
        result = run_task(
            task_description=args.task,
            enable_tracing=enable_tracing,
        )
    except Exception as e:
        print(f"{C.red('Error:')} Workflow failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    elapsed = time.time() - start
    print_run_result(result, elapsed)

    from .orchestrator import WorkflowStatus
    return 0 if result.status == WorkflowStatus.PASSED else 1


# ── Main Entry Point ──

def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).
              Accepts a list for testability.

    Returns:
        Exit code (0 = success, 1 = error).
    """
    # Load .env from cwd as a baseline. When --workspace is used,
    # handlers reload .env from the workspace directory.
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "run":
        _apply_overrides(args)
        _setup_logging(args.verbose)

        if args.dry_run:
            return handle_dry_run(args)
        elif args.plan:
            return handle_plan(args)
        else:
            return handle_run(args)

    # Shouldn't reach here, but just in case
    parser.print_help()
    return 0


def cli_entry() -> None:
    """Console script entry point (called by pyproject.toml [project.scripts]).

    Wraps main() and converts the return code to sys.exit().
    """
    sys.exit(main())
