"""
Archivyst CLI entry point.
"""

import argparse
import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import argcomplete
from rich.console import Console
from rich.prompt import Prompt

from archivyst.cli.config_cmd import add_config_subcommands, add_init_command
from archivyst.cli.index_cmd import add_index_subcommands
from archivyst.cli.memory_cmd import add_memory_subcommands
from archivyst.cli.process_cmd import add_process_command
from archivyst.cli.project_cmd import add_project_subcommands
from archivyst.cli.query_cmd import add_query_subcommands
from archivyst.cli.scan_cmd import add_scan_command
from archivyst.cli.utils import get_config_path
from archivyst.cli.vault_cmd import add_vault_subcommands
from archivyst.core.models import Config

console = Console()


def load_config(args: argparse.Namespace) -> Config | None:
    """Load configuration file."""
    config_path = get_config_path(args)

    if not config_path.exists():
        if args.command not in ("config", "init"):
            console.print(f"[yellow]Config not found at {config_path}[/yellow]")
            console.print(
                "Run [bold]archivyst init <project-name>[/bold] to create one."
            )
            return None
        return None

    try:
        return Config.load(config_path)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        return None


def resolve_project(
    args: argparse.Namespace, config: Config | None
) -> list[str] | None:
    """
    Resolve which project(s) to use for this command.

    Returns list of project names, or None if resolution failed.
    """
    if not config:
        return None

    # Check if --project was specified
    if hasattr(args, "project") and args.project:
        # Handle comma-separated projects
        projects = [p.strip() for p in args.project.split(",")]

        # Validate all projects exist
        for p in projects:
            if not config.get_project(p):
                console.print(f"[red]Project '{p}' not found[/red]")
                console.print(f"Available: {', '.join(config.get_project_names())}")
                return None

        return projects

    # Check for default project
    if config.default_project:
        default = config.get_project(config.default_project)
        if default:
            return [config.default_project]

    # No default, prompt user
    if not config.projects:
        console.print("[red]No projects configured[/red]")
        console.print("Run [bold]archivyst init <project-name>[/bold] to create one.")
        return None

    project_names = config.get_project_names()

    if len(project_names) == 1:
        return project_names

    console.print("[yellow]No project specified. Available projects:[/yellow]")
    for i, name in enumerate(project_names, 1):
        default_marker = " (default)" if name == config.default_project else ""
        console.print(f"  {i}. {name}{default_marker}")

    choice = Prompt.ask(
        "Select project",
        choices=[str(i) for i in range(1, len(project_names) + 1)] + project_names,
    )

    # Handle numeric or name input
    if choice.isdigit():
        return [project_names[int(choice) - 1]]
    return [choice]


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="archivyst",
        description="Vectorless, reasoning-based RAG for Obsidian vaults",
    )

    parser.add_argument(
        "--config",
        "-c",
        help="Path to config file",
        type=str,
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["md", "json"],
        default="md",
        help="Output format (default: md)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Add subcommand groups
    add_init_command(subparsers)
    add_config_subcommands(subparsers)
    add_project_subcommands(subparsers)
    add_vault_subcommands(subparsers)
    add_index_subcommands(subparsers)
    add_query_subcommands(subparsers)
    add_memory_subcommands(subparsers)
    add_process_command(subparsers)
    add_scan_command(subparsers)

    # completions
    comp_parser = subparsers.add_parser(
        "completions",
        help="Output shell completion script",
    )
    comp_parser.add_argument(
        "--shell",
        choices=["zsh", "bash", "fish"],
        default=None,
        help="Shell type (auto-detected if omitted)",
    )
    comp_parser.set_defaults(func=handle_completions)

    return parser


def handle_completions(args: argparse.Namespace, config: Config | None = None) -> int:
    """Output shell completion script."""

    shell = args.shell
    if not shell:
        shell_path = os.environ.get("SHELL", "")
        if "zsh" in shell_path:
            shell = "zsh"
        elif "fish" in shell_path:
            shell = "fish"
        else:
            shell = "bash"

    entry_points = ["archivyst", "archive"]

    if shell == "zsh":
        print("\n# Archivyst shell completions for zsh")
        print("# Add this to your ~/.zshrc")
        print("autoload -U bashcompinit && bashcompinit")
        for ep in entry_points:
            print(f'eval "$(register-python-argcomplete {ep})"')
    elif shell == "bash":
        print("# Archivyst shell completions for bash")
        print("# Add this to your ~/.bashrc")
        for ep in entry_points:
            print(f'eval "$(register-python-argcomplete {ep})"')
    elif shell == "fish":
        print("# Archivyst shell completions for fish")
        print("# Add this to your ~/.config/fish/config.fish")
        for ep in entry_points:
            print(f"register-python-argcomplete --shell fish {ep} | source")

    print()
    print(f"# Shell detected: {shell}")
    print("# To install, run:")
    if shell == "zsh":
        print("#   archivyst completions >> ~/.zshrc && source ~/.zshrc")
    elif shell == "bash":
        print("#   archivyst completions >> ~/.bashrc && source ~/.bashrc")
    elif shell == "fish":
        print("#   archivyst completions >> ~/.config/fish/config.fish")

    return 0


def main() -> int:
    """Main entry point."""
    parser = create_parser()
    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Configure logging based on verbosity
    log_level = logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(name)s %(levelname)s %(message)s",
    )

    # Load config (may be None for init)
    config = load_config(args)

    # Apply config-based logging (file output)
    # Console stays at the CLI-determined level (WARNING or DEBUG).
    # File handler gets the config-specified level independently.
    if config and config.logging and config.logging.file:
        log_path = Path(config.logging.file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=config.logging.max_file_size,
            backupCount=config.logging.backup_count,
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        cfg_level = getattr(logging, config.logging.level.upper(), logging.INFO)
        file_handler.setLevel(cfg_level)
        # Root logger must be at lowest level so file handler receives events
        root = logging.getLogger()
        root.setLevel(min(root.level, cfg_level))
        # Set console handler to CLI level so it doesn't get noisy
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(log_level)
        root.addHandler(file_handler)

    # Resolve project for commands that need it
    needs_project = hasattr(args, "needs_project") and args.needs_project
    if needs_project and config:
        projects = resolve_project(args, config)
        if projects is None:
            return 1
        args.resolved_projects = projects

    # Dispatch to appropriate handler
    if hasattr(args, "func"):
        try:
            result = args.func(args, config)

            # Handle async functions
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)

            print()  # Trailing newline to separate output from shell prompt
            return result if isinstance(result, int) else 0

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted[/yellow]")
            print()
            return 130
        except Exception as e:
            if args.verbose:
                console.print_exception()
            else:
                console.print(f"[red]Error: {e}[/red]")
            print()
            return 1

    parser.print_help()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
