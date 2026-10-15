"""
Shared CLI utilities for Archivyst.

Contains functions used by multiple CLI command modules,
extracted here to avoid circular imports between main.py
and the command modules.
"""

import argparse
from pathlib import Path

from rich.console import Console

from archivyst.agent.memory import MemoryStore
from archivyst.core.models import Config, ProjectScope
from archivyst.llm.client import check_lm_studio_available


def get_memory_store(
    config: Config,
    console: Console,
) -> tuple[MemoryStore, ProjectScope] | None:
    """Get MemoryStore for the default project. Returns None if no project configured."""
    project = config.get_default_project()
    if not project:
        console.print("[red]No project configured.[/red]")
        return None
    scope = config.build_project_scope(project.name)
    memory = MemoryStore(config, scope)
    return memory, scope


async def require_llm(config: Config, console: Console) -> bool:
    """Check LLM availability and print error if not available. Returns True if available."""
    console.print("Checking LLM server...")
    if not await check_lm_studio_available(config.llm.base_url):
        console.print(f"[red]Cannot connect to LLM at {config.llm.base_url}[/red]")
        return False
    return True


def get_config_path(args: argparse.Namespace) -> Path:
    """
    Determine config file path.

    Search order:
    1. --config flag
    2. ./archivyst.yaml
    3. ./config.yaml
    4. ~/.config/archivyst/config.yaml (default)
    """
    if hasattr(args, "config") and args.config:
        return Path(args.config).expanduser()

    # Default locations
    candidates = [
        Path.cwd() / "archivyst.yaml",
        Path.cwd() / "config.yaml",
        Path.home() / ".config" / "archivyst" / "config.yaml",
    ]

    for path in candidates:
        if path.exists():
            return path

    return candidates[-1]
