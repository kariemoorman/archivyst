"""
CLI vault command handlers.
"""

import argparse
import json

from rich.console import Console
from rich.table import Table

from archivyst.core.index import VaultIndex
from archivyst.core.models import Config

console = Console()


def add_vault_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add vault subcommands to parser."""
    vault_parser = subparsers.add_parser(
        "vault",
        help="Vault management",
    )

    def _vault_help(args: argparse.Namespace, config: Config | None) -> int:
        vault_parser.print_help()
        return 0

    vault_parser.set_defaults(func=_vault_help)
    vault_subs = vault_parser.add_subparsers(dest="vault_command")

    # vault list
    list_parser = vault_subs.add_parser(
        "list",
        help="List configured vaults",
    )
    list_parser.set_defaults(func=handle_vault_list)

    # vault add
    add_parser = vault_subs.add_parser(
        "add",
        help="Add a new vault",
    )
    add_parser.add_argument("name", help="Vault name")
    add_parser.add_argument("path", help="Path to vault directory")
    add_parser.add_argument(
        "--inactive",
        action="store_true",
        help="Add as inactive",
    )
    add_parser.set_defaults(func=handle_vault_add)

    # vault activate
    activate_parser = vault_subs.add_parser(
        "activate",
        help="Activate a vault",
    )
    activate_parser.add_argument("name", help="Vault name")
    activate_parser.set_defaults(func=handle_vault_activate)

    # vault deactivate
    deactivate_parser = vault_subs.add_parser(
        "deactivate",
        help="Deactivate a vault",
    )
    deactivate_parser.add_argument("name", help="Vault name")
    deactivate_parser.set_defaults(func=handle_vault_deactivate)

    # vault status
    status_parser = vault_subs.add_parser(
        "status",
        help="Show vault status and index info",
    )
    status_parser.add_argument("name", nargs="?", help="Vault name (all if omitted)")
    status_parser.set_defaults(func=handle_vault_status)


def handle_vault_list(args: argparse.Namespace, config: Config | None) -> int:
    """List configured vaults."""
    if not config:
        return 1

    if args.format == "json":
        vaults = []
        for vault in config.get_default_project_vaults():
            vaults.append(
                {
                    "name": vault.name,
                    "path": str(vault.path),
                    "active": vault.active,
                    "exists": vault.path.exists(),
                    "type": "knowledge",
                }
            )
        vaults.append(
            {
                "name": "memory",
                "path": str(config.memory.path),
                "active": True,
                "exists": config.memory.path.exists(),
                "type": "memory",
            }
        )
        console.print(json.dumps(vaults, indent=2))
        return 0

    table = Table(title="Configured Vaults")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Exists")

    for vault in config.get_default_project_vaults():
        status = "[green]active[/green]" if vault.active else "[dim]inactive[/dim]"
        exists = "[green]yes[/green]" if vault.path.exists() else "[red]no[/red]"
        table.add_row(vault.name, str(vault.path), "knowledge", status, exists)

    # Memory vault
    exists = (
        "[green]yes[/green]"
        if config.memory.path.exists()
        else "[yellow]pending[/yellow]"
    )
    table.add_row(
        "memory",
        str(config.memory.path),
        "memory",
        "[green]active[/green]",
        exists,
    )

    console.print(table)
    return 0


def handle_vault_add(args: argparse.Namespace, config: Config | None) -> int:
    """Add a new vault."""
    console.print(
        "[yellow]Note: Vault add modifies config file. "
        "Manual editing recommended for now.[/yellow]"
    )
    console.print(f"\nTo add vault '{args.name}' at '{args.path}':")
    console.print("\n1. Edit your config.yaml")
    console.print("2. Add under vaults.knowledge:")
    console.print(f"   - name: {args.name}")
    console.print(f"     path: {args.path}")
    console.print(f"     active: {not args.inactive}")

    return 0


def handle_vault_activate(args: argparse.Namespace, config: Config | None) -> int:
    """Activate a vault."""
    if not config:
        return 1

    found = False
    for vault in config.get_default_project_vaults():
        if vault.name == args.name:
            found = True
            if vault.active:
                console.print(f"[yellow]Vault '{args.name}' is already active[/yellow]")
            else:
                console.print(
                    f"[yellow]Edit config to set active: true for vault '{args.name}'[/yellow]"
                )
            break

    if not found:
        console.print(f"[red]Vault '{args.name}' not found[/red]")
        return 1

    return 0


def handle_vault_deactivate(args: argparse.Namespace, config: Config | None) -> int:
    """Deactivate a vault."""
    if not config:
        return 1

    found = False
    for vault in config.get_default_project_vaults():
        if vault.name == args.name:
            found = True
            if not vault.active:
                console.print(
                    f"[yellow]Vault '{args.name}' is already inactive[/yellow]"
                )
            else:
                console.print(
                    f"[yellow]Edit config to set active: false for vault '{args.name}'[/yellow]"
                )
            break

    if not found:
        console.print(f"[red]Vault '{args.name}' not found[/red]")
        return 1

    return 0


def handle_vault_status(args: argparse.Namespace, config: Config | None) -> int:
    """Show vault status and index info."""
    if not config:
        return 1

    vaults_to_check = []

    if args.name:
        # Find specific vault
        for vault in config.get_default_project_vaults():
            if vault.name == args.name:
                vaults_to_check.append(vault)
                break
        if not vaults_to_check:
            console.print(f"[red]Vault '{args.name}' not found[/red]")
            return 1
    else:
        vaults_to_check = config.get_default_project_vaults()

    for vault_config in vaults_to_check:
        console.print(f"\n[bold]{vault_config.name}[/bold]")
        console.print(f"  Path: {vault_config.path}")
        console.print(f"  Active: {vault_config.active}")

        if not vault_config.path.exists():
            console.print("  [red]Directory does not exist[/red]")
            continue

        # Check index
        index = VaultIndex(vault_config, config)
        manifest_path = index.manifest_path

        if manifest_path.exists():
            stats = index.get_stats()
            console.print(f"  Indexed notes: {stats['note_count']}")
            console.print(f"  Total sections: {stats['total_sections']}")
            console.print(f"  Total wikilinks: {stats['total_wikilinks']}")
            console.print(f"  Last indexed: {stats['last_indexed']}")
        else:
            console.print("  [yellow]Not indexed yet[/yellow]")
            console.print("  Run: archivyst index rebuild")

    return 0
