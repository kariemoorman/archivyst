"""
CLI project command handlers.
"""

import argparse
import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.table import Table
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from archivyst.cli.config_cmd import (
    init_obsidian_vault,
    init_project_memory,
    init_shared_memory_vault,
)
from archivyst.cli.utils import get_config_path
from archivyst.core.models import Config, GlobalAccess

console = Console()


def _get_yaml() -> YAML:
    """Get configured YAML instance."""
    yaml = YAML()
    yaml.preserve_quotes = True
    # mapping=2: dict keys indented 2 spaces
    # sequence=4: list items indented 4 spaces from parent key
    # offset=2: the "-" is 2 spaces from the key, content 2 more
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.default_flow_style = False
    return yaml


def add_project_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add project subcommands to parser."""
    project_parser = subparsers.add_parser(
        "project",
        help="Project management",
    )

    def _project_help(args: argparse.Namespace, config: Config | None) -> int:
        project_parser.print_help()
        return 0

    project_parser.set_defaults(func=_project_help)
    project_subs = project_parser.add_subparsers(dest="project_command")

    # project list
    list_parser = project_subs.add_parser(
        "list",
        help="List configured projects",
    )
    list_parser.add_argument(
        "--details",
        action="store_true",
        help="Show detailed information",
    )
    list_parser.set_defaults(func=handle_project_list)

    # project add
    add_parser = project_subs.add_parser(
        "add",
        help="Add a new project",
    )
    add_parser.add_argument("name", help="Project name")
    add_parser.add_argument(
        "--vault",
        required=True,
        help="Path to knowledge vault",
    )
    add_parser.add_argument(
        "--memory-path",
        help="Path to separate memory vault",
    )
    add_parser.add_argument(
        "--global",
        dest="global_access",
        choices=["full", "read", "none"],
        default="full",
        help="Global memory access level",
    )
    add_parser.add_argument(
        "--set-default",
        action="store_true",
        help="Set as default project",
    )
    add_parser.set_defaults(func=handle_project_add)

    # project remove
    remove_parser = project_subs.add_parser(
        "remove",
        help="Remove a project from config",
    )
    remove_parser.add_argument("name", help="Project name")
    remove_parser.add_argument(
        "--delete-files",
        action="store_true",
        help="Also delete vault and memory files (DANGEROUS)",
    )
    remove_parser.set_defaults(func=handle_project_remove)

    # project set-default
    default_parser = project_subs.add_parser(
        "set-default",
        help="Set the default project",
    )
    default_parser.add_argument("name", help="Project name")
    default_parser.set_defaults(func=handle_project_set_default)

    # project set-global
    global_parser = project_subs.add_parser(
        "set-global",
        help="Change global memory access for a project",
    )
    global_parser.add_argument("name", help="Project name")
    global_parser.add_argument(
        "level",
        choices=["full", "read", "none"],
        help="Access level",
    )
    global_parser.set_defaults(func=handle_project_set_global)

    # project show
    show_parser = project_subs.add_parser(
        "show",
        help="Show project details",
    )
    show_parser.add_argument("name", help="Project name")
    show_parser.set_defaults(func=handle_project_show)


def handle_project_list(args: argparse.Namespace, config: Config | None) -> int:
    """List configured projects."""
    if not config:
        console.print("[red]No config file found[/red]")
        return 1

    if not config.projects:
        console.print("[yellow]No projects configured[/yellow]")
        console.print("Run [bold]archivyst init <project-name>[/bold] to create one.")
        return 0

    if args.format == "json":
        projects = []
        for p in config.projects:
            proj_data = {
                "name": p.name,
                "vaults": [
                    {"name": v.name, "path": str(v.path), "active": v.active}
                    for v in p.vaults
                ],
                "memory": {
                    "path": str(p.memory.path) if p.memory.path else None,
                    "global_access": p.memory.global_access.value,
                },
                "is_default": p.name == config.default_project,
            }
            projects.append(proj_data)
        console.print(json.dumps(projects, indent=2))
        return 0

    if args.details:
        for p in config.projects:
            is_default = (
                " [green](default)[/green]" if p.name == config.default_project else ""
            )
            console.print(f"\n[bold cyan]{p.name}[/bold cyan]{is_default}")

            console.print("  Vaults:")
            for v in p.vaults:
                status = "[green]active[/green]" if v.active else "[dim]inactive[/dim]"
                exists = (
                    "[green]exists[/green]" if v.path.exists() else "[red]missing[/red]"
                )
                console.print(f"    - {v.name}: {v.path} ({status}, {exists})")

            if p.memory.path:
                console.print(f"  Memory: [cyan]separate[/cyan] at {p.memory.path}")
            else:
                console.print(
                    f"  Memory: [cyan]shared[/cyan] (global: {p.memory.global_access.value})"
                )
    else:
        table = Table(title="Projects")
        table.add_column("Name", style="cyan")
        table.add_column("Vaults")
        table.add_column("Memory")
        table.add_column("Global")
        table.add_column("Default")

        for p in config.projects:
            vault_names = ", ".join(v.name for v in p.vaults)
            memory = "separate" if p.memory.path else "shared"
            global_access = p.memory.global_access.value
            is_default = "✓" if p.name == config.default_project else ""

            table.add_row(p.name, vault_names, memory, global_access, is_default)

        console.print(table)

    return 0


def handle_project_add(args: argparse.Namespace, config: Config | None) -> int:
    """Add a new project."""

    yaml = _get_yaml()
    config_path = get_config_path(args)

    if not config_path.exists():
        console.print("[red]No config file found[/red]")
        console.print("Run [bold]archivyst init <project-name>[/bold] first.")
        return 1

    # Load existing config
    with open(config_path) as f:
        config_data = yaml.load(f)

    # Check if project exists
    for p in config_data.get("projects", []):
        if p.get("name") == args.name:
            console.print(f"[yellow]Project '{args.name}' already exists[/yellow]")
            return 1

    vault_path = Path(args.vault).expanduser().resolve()
    memory_path = (
        Path(args.memory_path).expanduser().resolve() if args.memory_path else None
    )

    # Build project config using ruamel.yaml types for proper formatting
    vault_config = CommentedMap()
    vault_config["name"] = f"{args.name}-vault"
    vault_config["path"] = str(vault_path)
    vault_config["active"] = True

    vaults_list = CommentedSeq()
    vaults_list.append(vault_config)

    project_config = CommentedMap()
    project_config["name"] = args.name
    project_config["vaults"] = vaults_list

    if memory_path:
        mem_config = CommentedMap()
        mem_config["path"] = str(memory_path)
        project_config["memory"] = mem_config
    elif args.global_access != "full":
        mem_config = CommentedMap()
        mem_config["global"] = args.global_access
        project_config["memory"] = mem_config

    # Add to config
    if "projects" not in config_data:
        config_data["projects"] = CommentedSeq()
    config_data["projects"].append(project_config)

    if args.set_default:
        config_data["default_project"] = args.name

    # Write config
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    # Create vault if needed
    if not vault_path.exists():
        console.print(f"Creating vault: [cyan]{vault_path}[/cyan]")
        init_obsidian_vault(vault_path, f"{args.name}-vault")

    # Create memory
    if memory_path:
        if not memory_path.exists():
            console.print(f"Creating separate memory: [cyan]{memory_path}[/cyan]")
            init_obsidian_vault(memory_path, f"{args.name}-memory")
            init_project_memory(memory_path, args.name, is_shared=False)
    else:
        shared_path = Path(config_data["memory"]["path"]).expanduser()
        if not shared_path.exists():
            console.print(f"Creating shared memory vault: [cyan]{shared_path}[/cyan]")
            init_shared_memory_vault(shared_path)

        project_mem = shared_path / "projects" / args.name
        if not project_mem.exists():
            console.print(f"Creating project memory: [cyan]{project_mem}[/cyan]")
            init_project_memory(shared_path, args.name, is_shared=True)

    console.print(f"\n[green]Added project '{args.name}'[/green]")
    return 0


def handle_project_remove(args: argparse.Namespace, config: Config | None) -> int:
    """Remove a project from config."""

    yaml = _get_yaml()

    if not config:
        return 1

    project = config.get_project(args.name)
    if not project:
        console.print(f"[red]Project '{args.name}' not found[/red]")
        return 1

    config_path = get_config_path(args)

    with open(config_path) as f:
        config_data = yaml.load(f)

    # Remove from projects list
    config_data["projects"] = [
        p for p in config_data.get("projects", []) if p.get("name") != args.name
    ]

    # Clear default if it was this project
    if config_data.get("default_project") == args.name:
        config_data["default_project"] = None

    # Delete files if requested
    if args.delete_files:
        console.print("[red]Deleting project files...[/red]")

        for vault in project.vaults:
            if vault.path.exists():
                console.print(f"  Deleting: {vault.path}")
                shutil.rmtree(vault.path)

        if project.memory.path and project.memory.path.exists():
            console.print(f"  Deleting: {project.memory.path}")
            shutil.rmtree(project.memory.path)
        else:
            # Delete from shared memory
            shared_path = config.memory.path / "projects" / args.name
            if shared_path.exists():
                console.print(f"  Deleting: {shared_path}")
                shutil.rmtree(shared_path)

    # Write config
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    console.print(f"[green]Removed project '{args.name}'[/green]")
    return 0


def handle_project_set_default(args: argparse.Namespace, config: Config | None) -> int:
    """Set the default project."""

    yaml = _get_yaml()

    if not config:
        return 1

    if not config.get_project(args.name):
        console.print(f"[red]Project '{args.name}' not found[/red]")
        return 1

    config_path = get_config_path(args)

    with open(config_path) as f:
        config_data = yaml.load(f)

    config_data["default_project"] = args.name

    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    console.print(f"[green]Set default project to '{args.name}'[/green]")
    return 0


def handle_project_set_global(args: argparse.Namespace, config: Config | None) -> int:
    """Change global memory access for a project."""

    yaml = _get_yaml()

    if not config:
        return 1

    project = config.get_project(args.name)
    if not project:
        console.print(f"[red]Project '{args.name}' not found[/red]")
        return 1

    if project.memory.path:
        console.print(
            f"[yellow]Project '{args.name}' has separate memory vault[/yellow]"
        )
        console.print("Global access is not applicable for separate memory vaults.")
        return 1

    config_path = get_config_path(args)

    with open(config_path) as f:
        config_data = yaml.load(f)

    # Find and update project
    for p in config_data.get("projects", []):
        if p.get("name") == args.name:
            if "memory" not in p:
                p["memory"] = {}
            p["memory"]["global"] = args.level
            break

    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    console.print(
        f"[green]Set global access for '{args.name}' to '{args.level}'[/green]"
    )
    return 0


def handle_project_show(args: argparse.Namespace, config: Config | None) -> int:
    """Show project details."""
    if not config:
        return 1

    project = config.get_project(args.name)
    if not project:
        console.print(f"[red]Project '{args.name}' not found[/red]")
        return 1

    if args.format == "json":
        data = {
            "name": project.name,
            "vaults": [
                {
                    "name": v.name,
                    "path": str(v.path),
                    "active": v.active,
                    "exists": v.path.exists(),
                }
                for v in project.vaults
            ],
            "memory": {
                "path": str(project.memory.path) if project.memory.path else None,
                "global_access": project.memory.global_access.value,
                "is_shared": project.memory.path is None,
            },
            "is_default": project.name == config.default_project,
        }
        console.print(json.dumps(data, indent=2))
        return 0

    is_default = (
        " [green](default)[/green]" if project.name == config.default_project else ""
    )
    console.print(f"\n[bold cyan]{project.name}[/bold cyan]{is_default}")

    console.print("\n[bold]Vaults:[/bold]")
    for v in project.vaults:
        status = "[green]active[/green]" if v.active else "[dim]inactive[/dim]"
        exists = "[green]exists[/green]" if v.path.exists() else "[red]missing[/red]"
        console.print(f"  - {v.name}")
        console.print(f"    Path: {v.path}")
        console.print(f"    Status: {status}, {exists}")

    console.print("\n[bold]Memory:[/bold]")
    if project.memory.path:
        exists = (
            "[green]exists[/green]"
            if project.memory.path.exists()
            else "[red]missing[/red]"
        )
        console.print("  Type: [cyan]separate[/cyan]")
        console.print(f"  Path: {project.memory.path} ({exists})")
    else:
        console.print("  Type: [cyan]shared[/cyan]")
        console.print(f"  Global access: {project.memory.global_access.value}")

        project_mem = config.memory.path / "projects" / project.name
        exists = (
            "[green]exists[/green]"
            if project_mem.exists()
            else "[yellow]pending[/yellow]"
        )
        console.print(f"  Project path: {project_mem} ({exists})")

    # Show scope info
    try:
        scope = config.build_project_scope(project.name)
        console.print("\n[bold]Access Scope:[/bold]")
        console.print(f"  Knowledge vaults: {', '.join(scope.knowledge_vaults)}")
        console.print(
            f"  Can read global: {scope.global_access in (GlobalAccess.FULL, GlobalAccess.READ)}"
        )
        console.print(f"  Can write global: {scope.global_access == GlobalAccess.FULL}")
    except Exception as e:
        console.print(f"\n[yellow]Could not build scope: {e}[/yellow]")

    return 0
