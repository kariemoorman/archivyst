"""
CLI index command handlers.
"""

import argparse
import json
import logging
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.prompt import Prompt

from archivyst.agent.extraction import (
    check_contradictions,
    save_extracted_entities,
    save_extracted_memory,
)
from archivyst.agent.prompts import ENTITY_EXTRACTION_PROMPT, MEMORY_EXTRACTION_PROMPT
from archivyst.cli.utils import get_memory_store
from archivyst.core.index import VaultIndex
from archivyst.core.models import Config, IndexStrategy
from archivyst.core.summarizer import generate_vault_meta_summary
from archivyst.llm.client import LLMClient, check_lm_studio_available
from archivyst.llm.utils import parse_json_response

logger = logging.getLogger(__name__)

console = Console()


def add_index_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add index subcommands to parser."""
    index_parser = subparsers.add_parser(
        "index",
        help="Index management",
    )

    index_parser.set_defaults(func=handle_index_default)
    index_subs = index_parser.add_subparsers(dest="index_command")

    # index rebuild
    rebuild_parser = index_subs.add_parser(
        "rebuild",
        help="Rebuild vault index",
    )
    rebuild_parser.add_argument(
        "--vault",
        help="Specific vault to rebuild (all active if omitted)",
    )
    rebuild_parser.add_argument(
        "--full",
        action="store_true",
        help="Force full rebuild (ignore incremental)",
    )
    rebuild_parser.add_argument(
        "--no-summaries",
        action="store_true",
        help="Skip LLM summary generation",
    )
    rebuild_parser.add_argument(
        "--no-scan",
        action="store_true",
        help="Skip pre-index code file scan",
    )
    rebuild_parser.set_defaults(func=handle_index_rebuild)

    # index status
    status_parser = index_subs.add_parser(
        "status",
        help="Show index status",
    )
    status_parser.add_argument(
        "--vault",
        help="Specific vault to check",
    )
    status_parser.set_defaults(func=handle_index_status)

    # index check
    check_parser = index_subs.add_parser(
        "check",
        help="Check for changed files",
    )
    check_parser.add_argument(
        "--vault",
        help="Specific vault to check",
    )
    check_parser.set_defaults(func=handle_index_check)


async def handle_index_default(args: argparse.Namespace, config: Config | None) -> int:
    """Smart default when `archivyst index` is run with no subcommand."""
    if not config:
        return 1

    project_vaults = config.get_default_project_vaults()
    vaults = [v for v in project_vaults if v.active]
    if not vaults:
        console.print("[yellow]No active vaults configured.[/yellow]")
        return 0

    for vault_config in vaults:
        index = VaultIndex(vault_config, config)

        if not vault_config.path.exists():
            console.print(
                f"[red]Vault '{vault_config.name}' path does not exist: "
                f"{vault_config.path}[/red]"
            )
            continue

        if not index.manifest_path.exists():
            # No index — offer to build
            answer = Prompt.ask(
                f"No index found for [bold]{vault_config.name}[/bold]. "
                f"Build it now?",
                choices=["y", "n"],
                default="y",
            )
            if answer == "y":
                args.vault = vault_config.name
                args.full = False
                args.no_summaries = False
                return await handle_index_rebuild(args, config)
        else:
            # Index exists — show status and offer options
            stats = index.get_stats()
            console.print(f"\n[bold]{vault_config.name}[/bold]")
            console.print(f"  Notes: {stats['note_count']}")
            console.print(f"  Last indexed: {stats['last_indexed']}")

            changed, deleted = index.get_changed_notes()
            if changed or deleted:
                console.print(
                    f"  [yellow]{len(changed)} changed, "
                    f"{len(deleted)} deleted since last index[/yellow]"
                )
            else:
                console.print("  [green]Index is up to date[/green]")

            console.print()
            choice = Prompt.ask(
                "What would you like to do?",
                choices=["check", "rebuild", "cancel"],
                default="check" if (changed or deleted) else "cancel",
            )

            if choice == "check":
                args.vault = None
                return handle_index_check(args, config)
            elif choice == "rebuild":
                args.vault = None
                args.full = True
                args.no_summaries = False
                return await handle_index_rebuild(args, config)

    return 0


async def handle_index_rebuild(args: argparse.Namespace, config: Config | None) -> int:
    """Rebuild vault index."""
    if not config:
        return 1

    # Check LLM availability if summaries enabled
    use_summaries = not args.no_summaries and config.indexing.summarize_sections
    llm_client = None

    if use_summaries:
        console.print("Checking LLM server...")
        if await check_lm_studio_available(config.llm.base_url):
            console.print("[green]LLM server available[/green]")
            llm_client = LLMClient(config.llm)
        else:
            console.print(
                "[yellow]LLM server not available - "
                "proceeding without summaries[/yellow]"
            )
            use_summaries = False

    # Determine vaults to index
    project_vaults = config.get_default_project_vaults()
    vaults_to_index = []

    if args.vault:
        for vault in project_vaults:
            if vault.name == args.vault:
                vaults_to_index.append(vault)
                break
        if not vaults_to_index:
            console.print(f"[red]Vault '{args.vault}' not found[/red]")
            return 1
    else:
        vaults_to_index = [v for v in project_vaults if v.active]

    if not vaults_to_index:
        console.print("[yellow]No active vaults to index[/yellow]")
        return 0

    strategy = IndexStrategy.FULL if args.full else config.indexing.strategy

    # Pre-index code scan
    no_scan = getattr(args, "no_scan", False)
    if config.indexing.scan_code and not no_scan:
        from archivyst.cli.scan_cmd import (
            convert_and_update_manifest,
            handle_deleted_files,
            handle_moved_files,
            scan_vault_for_code,
        )

        current_model = config.llm.models.memory
        max_code_size = config.process.max_code_file_size
        code_ta = config.token_allocation.code_description

        for vault_config in vaults_to_index:
            vault_path = vault_config.path
            if not vault_path.exists():
                continue

            pageindex_path = vault_path / ".pageindex"
            scan_result = scan_vault_for_code(
                vault_path, pageindex_path, current_model, max_code_size
            )

            if not scan_result.has_changes and not scan_result.model_changed:
                continue

            # Print scan summary
            console.print(
                f"\n[dim]Scanning {vault_config.name} for code files...[/dim]"
            )
            if scan_result.new:
                console.print(f"  Found {len(scan_result.new)} new code files")
            if scan_result.modified:
                console.print(
                    f"  Found {len(scan_result.modified)} modified code files"
                )

            if args.full:
                # --full: convert everything, no prompts
                all_files = scan_result.new + scan_result.modified
                for _, path in scan_result.moved:
                    all_files.append(path)
                if all_files:
                    scan_llm: LLMClient | None = None
                    if llm_client:
                        scan_llm = llm_client
                    elif await check_lm_studio_available(config.llm.base_url):
                        scan_llm = LLMClient(config.llm)
                        await scan_llm.__aenter__()

                    await convert_and_update_manifest(
                        all_files,
                        vault_path,
                        pageindex_path,
                        scan_llm,
                        current_model,
                        max_code_size,
                        code_max_tokens=code_ta.max_tokens,
                        code_temperature=code_ta.temperature,
                    )

                    if scan_llm and scan_llm is not llm_client:
                        await scan_llm.__aexit__(None, None, None)

                if scan_result.deleted:
                    handle_deleted_files(
                        scan_result.deleted, vault_path, pageindex_path
                    )
            else:
                # Incremental: prompt user
                from rich.prompt import Prompt as ScanPrompt

                answer = ScanPrompt.ask(
                    "Convert and include in index?",
                    choices=["y", "n"],
                    default="y",
                )
                if answer == "y":
                    files = scan_result.new + scan_result.modified
                    scan_llm = None
                    if llm_client:
                        scan_llm = llm_client
                    elif await check_lm_studio_available(config.llm.base_url):
                        scan_llm = LLMClient(config.llm)
                        await scan_llm.__aenter__()

                    if files:
                        await convert_and_update_manifest(
                            files,
                            vault_path,
                            pageindex_path,
                            scan_llm,
                            current_model,
                            max_code_size,
                        )
                    if scan_result.moved:
                        await handle_moved_files(
                            scan_result.moved,
                            vault_path,
                            pageindex_path,
                            scan_llm,
                            current_model,
                            max_code_size,
                        )
                    if scan_result.deleted:
                        handle_deleted_files(
                            scan_result.deleted, vault_path, pageindex_path
                        )

                    if scan_llm and scan_llm is not llm_client:
                        await scan_llm.__aexit__(None, None, None)

            if scan_result.model_changed and not args.full:
                model_answer = ScanPrompt.ask(
                    "Model changed. Regenerate descriptions?",
                    choices=["y", "n"],
                    default="n",
                )
                if model_answer == "y":
                    # Treat all unchanged as needing reconversion
                    is_available = await check_lm_studio_available(config.llm.base_url)
                    scan_llm = llm_client or (
                        LLMClient(config.llm) if is_available else None
                    )
                    if scan_llm and scan_llm is not llm_client:
                        await scan_llm.__aenter__()
                    await convert_and_update_manifest(
                        scan_result.unchanged,
                        vault_path,
                        pageindex_path,
                        scan_llm,
                        current_model,
                        max_code_size,
                        code_max_tokens=code_ta.max_tokens,
                        code_temperature=code_ta.temperature,
                    )
                    if scan_llm and scan_llm is not llm_client:
                        await scan_llm.__aexit__(None, None, None)

    # Index each vault
    try:
        if llm_client:
            await llm_client.__aenter__()

        for vault_config in vaults_to_index:
            console.print(f"\n[bold]Indexing {vault_config.name}[/bold]")

            if not vault_config.path.exists():
                console.print(f"  [red]Path does not exist: {vault_config.path}[/red]")
                continue

            index = VaultIndex(vault_config, config, llm_client)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console,
            ) as progress:
                task = progress.add_task("Indexing...", total=None)

                def progress_callback(
                    current: int, total: int, path: Path, _task: TaskID = task
                ) -> None:
                    progress.update(_task, completed=current, total=total)
                    progress.update(
                        _task,
                        description=f"[{current}/{total}] {path.name}",
                    )
                    progress.console.print(
                        f"  [dim][{current}/{total}][/dim] {path.name}"
                    )

                stats = await index.rebuild_index(
                    strategy=strategy,
                    progress_callback=progress_callback,
                )

                # Hide the progress bar on completion
                progress.update(task, visible=False)

            # Process images separately (after progress bar completes)
            images_processed = 0
            if llm_client:
                images_processed = await index.process_all_images(
                    progress_callback=lambda c, t, ref: console.print(
                        f"    [dim][{c}/{t}][/dim] {ref}"
                    ),
                )

            console.print()
            console.print(f"  Indexed: {stats['indexed']} notes")
            if images_processed:
                console.print(f"  Processed: {images_processed} images")
            console.print(f"  Deleted: {stats['deleted']} notes")
            console.print(f"  Total: {stats['total']} notes")

            # Log system event
            event_result = get_memory_store(config, console)
            if event_result:
                event_memory, _ = event_result
                event_memory.save_event(
                    "system",
                    f"Index rebuilt for vault '{vault_config.name}'",
                    {
                        "vault": vault_config.name,
                        "strategy": strategy.value,
                        "indexed": stats.get("indexed", 0),
                        "deleted": stats.get("deleted", 0),
                        "total": stats.get("total", 0),
                    },
                )

            # Generate vault meta-summary and extract entities from notes
            if llm_client and stats.get("indexed", 0) > 0:
                console.print("\n  [dim]Generating vault summary...[/dim]")
                note_summaries = index.get_note_summaries()
                if note_summaries:
                    meta = await generate_vault_meta_summary(
                        llm_client,
                        vault_config.name,
                        note_summaries,
                    )
                    # Save to memory if project scope available
                    meta_result = get_memory_store(config, console)
                    if meta_result:
                        memory, _ = meta_result
                        memory.save_vault_meta(vault_config.name, meta)

                        # Extract entities from each note summary
                        ta = config.token_allocation
                        console.print("  [dim]Extracting entities...[/dim]")
                        for title, summary in note_summaries:
                            try:
                                response = await llm_client.complete(
                                    prompt=ENTITY_EXTRACTION_PROMPT.format(
                                        text=f"# {title}\n\n{summary}",
                                        source=f"vault:{vault_config.name}:{title}",
                                    ),
                                    task="memory",
                                    max_tokens=ta.entity_extraction.max_tokens,
                                    temperature=ta.entity_extraction.temperature,
                                )
                                data = parse_json_response(response)
                                save_extracted_entities(memory, data)
                            except Exception as e:
                                logger.warning(
                                    "[indexing] Entity extraction failed for %s: %s",
                                    title,
                                    e,
                                )

                        # Extract facts and patterns from each note
                        console.print("  [dim]Extracting facts and patterns...[/dim]")
                        for title, summary in note_summaries:
                            try:
                                response = await llm_client.complete(
                                    prompt=MEMORY_EXTRACTION_PROMPT.format(
                                        conversation=(f"Note: {title}\n\n{summary}"),
                                    ),
                                    task="memory",
                                    max_tokens=ta.memory_extraction.max_tokens,
                                    temperature=ta.memory_extraction.temperature,
                                )
                                data = parse_json_response(response)
                                save_extracted_memory(memory, data)
                            except Exception as e:
                                logger.warning(
                                    "[indexing] Memory extraction failed" " for %s: %s",
                                    title,
                                    e,
                                )

                        # Save a fact for each note
                        for title, summary in note_summaries:
                            memory.save_fact(
                                title.lower().replace(" ", "-"),
                                f"# {title}\n\n{summary}",
                            )

                        # Check for contradictions
                        console.print("  [dim]Checking for contradictions...[/dim]")
                        total_contradictions = 0
                        for title, summary in note_summaries:
                            try:
                                c_ids = await check_contradictions(
                                    memory,
                                    f"# {title}\n\n{summary}",
                                    llm_client,
                                    context=f"index:{vault_config.name}:{title}",
                                    max_tokens=ta.entity_extraction.max_tokens,
                                    temperature=ta.entity_extraction.temperature,
                                )
                                total_contradictions += len(c_ids)
                            except Exception as e:
                                logger.warning(
                                    "[indexing] Contradiction check failed"
                                    " for %s: %s",
                                    title,
                                    e,
                                )
                        if total_contradictions > 0:
                            console.print(
                                f"  [yellow]{total_contradictions} "
                                f"contradiction(s) detected — run "
                                f"'archivyst memory reconcile' to "
                                f"resolve[/yellow]"
                            )

                        memory.save_event(
                            "milestone",
                            (
                                f"Vault meta-summary, entities, and facts "
                                f"generated for '{vault_config.name}'"
                            ),
                            {
                                "vault": vault_config.name,
                                "notes_summarized": len(note_summaries),
                            },
                        )
                        memory.versioning.auto_commit(
                            f"Post-index: vault meta + knowledge for {vault_config.name}"
                        )
                        console.print(
                            "  [green]Vault summary and knowledge saved to memory[/green]"
                        )

            # Step 4: Wikilink insertion (disabled — links to memory
            # entities don't resolve to vault notes in Obsidian)

    finally:
        if llm_client:
            await llm_client.__aexit__(None, None, None)

    return 0


def handle_index_status(args: argparse.Namespace, config: Config | None) -> int:
    """Show index status."""
    if not config:
        return 1

    project_vaults = config.get_default_project_vaults()
    vaults_to_check = []

    if args.vault:
        for vault in project_vaults:
            if vault.name == args.vault:
                vaults_to_check.append(vault)
                break
        if not vaults_to_check:
            console.print(f"[red]Vault '{args.vault}' not found[/red]")
            return 1
    else:
        vaults_to_check = project_vaults

    all_stats = []

    for vault_config in vaults_to_check:
        index = VaultIndex(vault_config, config)

        if not index.manifest_path.exists():
            stats: dict[str, object] = {
                "vault_name": vault_config.name,
                "status": "not indexed",
            }
        else:
            stats = index.get_stats()
            stats["status"] = "indexed"

        all_stats.append(stats)

    if args.format == "json":
        console.print(json.dumps(all_stats, indent=2, default=str))
    else:
        for stats in all_stats:
            console.print(f"\n[bold]{stats['vault_name']}[/bold]")

            if stats.get("status") == "not indexed":
                console.print("  [yellow]Not indexed[/yellow]")
                continue

            console.print(f"  Notes: {stats['note_count']}")
            console.print(f"  Sections: {stats['total_sections']}")
            console.print(f"  Wikilinks: {stats['total_wikilinks']}")
            console.print(f"  Images: {stats['total_images']}")
            console.print(f"  Last indexed: {stats['last_indexed']}")

    return 0


def handle_index_check(args: argparse.Namespace, config: Config | None) -> int:
    """Check for changed files."""
    if not config:
        return 1

    project_vaults = config.get_default_project_vaults()
    vaults_to_check = []

    if args.vault:
        for vault in project_vaults:
            if vault.name == args.vault:
                vaults_to_check.append(vault)
                break
        if not vaults_to_check:
            console.print(f"[red]Vault '{args.vault}' not found[/red]")
            return 1
    else:
        vaults_to_check = [v for v in project_vaults if v.active]

    for vault_config in vaults_to_check:
        console.print(f"\n[bold]{vault_config.name}[/bold]")

        index = VaultIndex(vault_config, config)

        if not index.manifest_path.exists():
            console.print("  [yellow]Not indexed - all files need indexing[/yellow]")
            continue

        changed, deleted = index.get_changed_notes()

        if not changed and not deleted:
            console.print("  [green]Index is up to date[/green]")
        else:
            if changed:
                console.print(f"  Changed/new: {len(changed)} files")
                if args.verbose:
                    for path in changed[:10]:
                        console.print(f"    {path.name}")
                    if len(changed) > 10:
                        console.print(f"    ... and {len(changed) - 10} more")

            if deleted:
                console.print(f"  Deleted: {len(deleted)} files")

    return 0
