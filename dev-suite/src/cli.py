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

_REQUIRED_KEYS = {
    "GOOGLE_API_KEY": "Gemini (Architect agent)",
    "ANTHROPIC_API_KEY": "Claude (Lead Dev + QA agents)",
    "E2B_API_KEY": "E2B sandbox execution",
}

_OPTIONAL_KEYS = {
    "LANGFUSE_PUBLIC_KEY": "Langfuse observability (tracing)",
    "LANGFUSE_SECRET_KEY": "Langfuse observability (tracing)",
}


def _check_env_key(key: str) -> bool:
    """Check if an env var is set and not a placeholder."""
    val = os.getenv(key, "")
    return bool(val and not val.startswith("your-"))


def validate_config() -> dict:
    """Validate environment configuration."""
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
    print(f"\n{C.dim('─' * 50)}")
    print(f"  {C.bold(text)}")
    print(C.dim("─" * 50))


def _print_kv(key: str, value: str, indent: int = 2) -> None:
    pad = " " * indent
    print(f"{pad}{C.dim(key + ':')} {value}")


def print_dry_run(config: dict, workspace: str, task: str) -> None:
    """Print dry-run output: config validation and execution plan."""
    _print_header("DRY RUN — Execution Plan")
    print(f"\n  {C.cyan('Task:')} {task[:120]}{'...' if len(task) > 120 else ''}")
    print(f"  {C.cyan('Workspace:')} {workspace}")

    print(f"\n  {C.bold('Models')}")
    for role, model in config["models"].items():
        _print_kv(f"  {role.capitalize()}", model, indent=2)

    print(f"\n  {C.bold('Budget')}")
    _print_kv("  Token ceiling", f"{config['budget']['token_budget']:,}", indent=2)
    _print_kv("  Max retries", str(config["budget"]["max_retries"]), indent=2)

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

    tracing_ok = all(_check_env_key(k) for k in _OPTIONAL_KEYS)
    print(f"\n  {C.bold('Tracing')}")
    langfuse_status = C.green("enabled") if tracing_ok else C.yellow("disabled (keys missing)")
    _print_kv("  Langfuse", langfuse_status, indent=2)

    for err in config.get("errors", []):
        print(f"\n  {C.bold('Config Errors')}")
        print(f"    {C.red('✗')} {err}")

    if config["valid"]:
        print(f"\n  {C.green('✓ All required keys present. Ready to run.')}")
    else:
        if config["missing"]:
            print(f"\n  {C.red('✗ Missing required API keys:')}")
            for key in config["missing"]:
                print(f"    → Set {C.bold(key)} in your .env file")
        print(f"\n  {C.dim('Copy .env.example to .env and fill in your keys.')}")


def print_blueprint(blueprint_data: dict, tokens_used: int, elapsed: float) -> None:
    """Print a Blueprint from --plan mode."""
    _print_header("PLAN — Architect Blueprint")
    print(f"\n  {C.cyan('Task ID:')} {blueprint_data.get('task_id', 'unknown')}")

    for section, items in [("Target Files", "target_files"), ("Constraints", "constraints"),
                           ("Acceptance Criteria", "acceptance_criteria")]:
        vals = blueprint_data.get(items, [])
        if vals:
            print(f"\n  {C.bold(section)}")
            for v in vals:
                print(f"    {C.cyan(v)}" if items == "target_files" else f"    {v}")

    instructions = blueprint_data.get("instructions", "")
    if instructions:
        print(f"\n  {C.bold('Instructions')}")
        for line in instructions.split("\n"):
            print(f"    {line}")

    print(f"\n  {C.dim(f'Tokens: {tokens_used:,} | Elapsed: {elapsed:.1f}s')}")
    print(f"\n  {C.bold('Raw JSON')}")
    print(C.dim(json.dumps(blueprint_data, indent=2)))


def print_run_result(state, elapsed: float) -> None:
    """Print the result of a full run."""
    from .orchestrator import WorkflowStatus
    status = state.status
    _print_header("RUN COMPLETE" if status == WorkflowStatus.PASSED else "RUN FINISHED")

    status_str = {
        WorkflowStatus.PASSED: C.green("✓ PASSED"),
        WorkflowStatus.FAILED: C.red("✗ FAILED"),
        WorkflowStatus.ESCALATED: C.yellow("⚠ ESCALATED"),
    }.get(status, C.dim(str(status.value)))
    print(f"\n  {C.bold('Status:')} {status_str}")

    if state.error_message:
        print(f"  {C.red('Error:')} {state.error_message}")

    print(f"\n  {C.bold('Metrics')}")
    budget = int(os.getenv("TOKEN_BUDGET", "50000"))
    pct = round((state.tokens_used / budget) * 100) if budget else 0
    _print_kv("Tokens used", f"{state.tokens_used:,} / {budget:,} ({pct}%)")
    _print_kv("Estimated cost", f"${state.tokens_used * 0.000012:.4f}")
    _print_kv("Retries", f"{state.retry_count} / {int(os.getenv('MAX_RETRIES', '3'))}")
    _print_kv("Elapsed", f"{elapsed:.1f}s")

    if state.blueprint:
        print(f"\n  {C.bold('Blueprint')}")
        _print_kv("Task ID", state.blueprint.task_id)
        _print_kv("Files", ", ".join(state.blueprint.target_files))

    if _check_env_key("LANGFUSE_PUBLIC_KEY"):
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        print(f"\n  {C.dim(f'Trace: {host} (check Langfuse dashboard)')}")

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
    parser.add_argument("--version", action="version", version=f"dev-suite {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    run_parser = subparsers.add_parser(
        "run", help="Run a task through the agent workforce",
        description="Execute a task through the Architect → Lead Dev → QA pipeline.",
    )
    run_parser.add_argument("task", type=str, help="Task description for the agents")

    mode_group = run_parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true",
                            help="Validate config and show execution plan (zero API calls)")
    mode_group.add_argument("--plan", action="store_true",
                            help="Run Architect only, show Blueprint (one API call)")

    run_parser.add_argument("--model-architect", type=str, default=None, metavar="MODEL",
                            help="Override Architect model (default: gemini-3-flash-preview)")
    run_parser.add_argument("--model-developer", type=str, default=None, metavar="MODEL",
                            help="Override Lead Dev model (default: claude-sonnet-4-20250514)")
    run_parser.add_argument("--model-qa", type=str, default=None, metavar="MODEL",
                            help="Override QA model (default: claude-sonnet-4-20250514)")
    run_parser.add_argument("--budget", type=int, default=None, metavar="TOKENS",
                            help="Token budget ceiling (default: 50000)")
    run_parser.add_argument("--workspace", type=str, default=None, metavar="PATH",
                            help="Workspace root directory (default: current directory)")
    run_parser.add_argument("--verbose", "-v", action="store_true",
                            help="Stream agent activity to stdout (DEBUG logging)")
    run_parser.add_argument("--no-trace", action="store_true",
                            help="Disable Langfuse tracing")
    return parser


# ── Command Handlers ──

def _apply_overrides(args: argparse.Namespace) -> None:
    """Apply CLI flag overrides to environment variables.

    Must be called AFTER load_dotenv() so CLI flags take precedence.
    """
    if args.model_architect:
        os.environ["ARCHITECT_MODEL"] = args.model_architect
    if args.model_developer:
        os.environ["DEVELOPER_MODEL"] = args.model_developer
    if args.model_qa:
        os.environ["QA_MODEL"] = args.model_qa
    if args.budget is not None:
        if args.budget <= 0:
            print(f"{C.red('Error:')} --budget must be a positive integer, got {args.budget}")
            sys.exit(1)
        os.environ["TOKEN_BUDGET"] = str(args.budget)


def _setup_logging(verbose: bool) -> None:
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

    os.chdir(workspace)
    load_dotenv()
    _apply_overrides(args)

    config = validate_config()
    print_dry_run(config, workspace, args.task)
    return 0 if config["valid"] else 1


def handle_plan(args: argparse.Namespace) -> int:
    """Handle --plan: run Architect only, show Blueprint."""
    workspace = args.workspace or os.getcwd()
    if not Path(workspace).is_dir():
        print(f"{C.red('Error:')} Workspace path does not exist: {workspace}")
        return 1

    os.chdir(workspace)
    load_dotenv()
    _apply_overrides(args)

    if not _check_env_key("GOOGLE_API_KEY"):
        print(f"{C.red('Error:')} GOOGLE_API_KEY is required for --plan mode.")
        print(f"{C.dim('Run with --dry-run to see full config status.')}")
        return 1

    config = validate_config()
    if config["errors"]:
        for err in config["errors"]:
            print(f"{C.red('Config error:')} {err}")
        return 1

    from .orchestrator import GraphState, WorkflowStatus, architect_node
    from .tracing import create_trace_config

    trace_config = create_trace_config(
        enabled=not args.no_trace, task_description=args.task,
    )

    from langgraph.graph import END, START, StateGraph
    plan_graph = StateGraph(GraphState)
    plan_graph.add_node("architect", architect_node)
    plan_graph.add_edge(START, "architect")
    plan_graph.add_edge("architect", END)
    workflow = plan_graph.compile()

    initial_state: GraphState = {
        "task_description": args.task, "blueprint": None,
        "generated_code": "", "failure_report": None,
        "status": WorkflowStatus.PLANNING, "retry_count": 0,
        "tokens_used": 0, "error_message": "",
        "memory_context": [], "trace": [],
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
        print(f"{C.red('Error:')} Architect did not produce a Blueprint: "
              f"{result.get('error_message', 'Unknown error')}")
        return 1

    print_blueprint(blueprint.model_dump(), result.get("tokens_used", 0), elapsed)
    return 0


def handle_run(args: argparse.Namespace) -> int:
    """Handle full run: Architect → Lead Dev → QA loop."""
    workspace = args.workspace or os.getcwd()
    if not Path(workspace).is_dir():
        print(f"{C.red('Error:')} Workspace path does not exist: {workspace}")
        return 1

    os.chdir(workspace)
    load_dotenv()
    _apply_overrides(args)

    config = validate_config()
    if not config["valid"]:
        if config["missing"]:
            print(f"{C.red('Error:')} Missing required API keys: {', '.join(config['missing'])}")
        for err in config.get("errors", []):
            print(f"{C.red('Config error:')} {err}")
        print(f"{C.dim('Run with --dry-run to see full config status.')}")
        return 1

    from .orchestrator import run_task

    start = time.time()
    try:
        result = run_task(
            task_description=args.task,
            enable_tracing=not args.no_trace,
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
    """Main CLI entry point."""
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "run":
        _setup_logging(args.verbose)
        if args.dry_run:
            return handle_dry_run(args)
        elif args.plan:
            return handle_plan(args)
        else:
            return handle_run(args)

    parser.print_help()
    return 0


def cli_entry() -> None:
    """Console script entry point (pyproject.toml [project.scripts])."""
    sys.exit(main())
