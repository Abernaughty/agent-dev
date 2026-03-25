"""Allow running as `python -m src` (fallback for console script).

Primary usage is via the installed console script:
    dev-suite run "Create a function that..."

This __main__.py provides a fallback:
    python -m src run "Create a function that..."
"""

from .cli import cli_entry

if __name__ == "__main__":
    cli_entry()
