"""
CLI memory command handlers.
"""

import argparse
import json
import logging
import re
import shutil
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.tree import Tree

from archivyst.agent.memory import MemoryLocation, MemoryStore
from archivyst.agent.prompts import (
    CONTRADICTION_RESOLVE_PROMPT,
    LOBOTOMY_SCAN_PROMPT,
    MEMORY_ORGANIZE_PROMPT,
)
from archivyst.cli.utils import get_memory_store, require_llm
from archivyst.core.models import Config, GlobalAccess, ProjectScope
from archivyst.llm.client import LLMClient, check_lm_studio_available
from archivyst.llm.utils import parse_json_response

logger = logging.getLogger(__name__)

console = Console()


def add_memory_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add memory subcommands to parser."""
    memory_parser = subparsers.add_parser(
        "memory",
        help="Memory management",
    )

    def _memory_help(args: argparse.Namespace, config: Config | None) -> int:
        memory_parser.print_help()
        return 0

    memory_parser.set_defaults(func=_memory_help)
    memory_subs = memory_parser.add_subparsers(dest="memory_command")

    # memory show
    show_parser = memory_subs.add_parser(
        "show",
        help="Show memory contents",
    )
    show_parser.add_argument(
        "path",
        nargs="?",
        help="Specific memory path (e.g., user/preferences.md)",
    )
    show_parser.add_argument(
        "--global",
        dest="global_memory",
        action="store_true",
        help="Show from global memory instead of project memory",
    )
    show_parser.set_defaults(func=handle_memory_show)

    # memory tree
    tree_parser = memory_subs.add_parser(
        "tree",
        help="Show memory structure",
    )
    tree_parser.add_argument(
        "--global",
        dest="global_memory",
        action="store_true",
        help="Show global memory tree instead of project memory",
    )
    tree_parser.set_defaults(func=handle_memory_tree)

    # memory prune
    prune_parser = memory_subs.add_parser(
        "prune",
        help="Prune old working memory",
    )
    prune_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be pruned without doing it",
    )
    prune_parser.set_defaults(func=handle_memory_prune)

    # memory conversations
    conv_parser = memory_subs.add_parser(
        "conversations",
        help="List saved conversations",
    )
    conv_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum conversations to show",
    )
    conv_parser.set_defaults(func=handle_memory_conversations)

    # memory entities
    entities_parser = memory_subs.add_parser(
        "entities",
        help="List extracted entities",
    )
    entities_parser.add_argument(
        "type",
        nargs="?",
        choices=["people", "projects", "concepts"],
        help="Entity type to list",
    )
    entities_parser.set_defaults(func=handle_memory_entities)

    # memory promote
    promote_parser = memory_subs.add_parser(
        "promote",
        help="Promote working memory to long-term memory",
    )
    promote_parser.add_argument(
        "--session",
        help="Specific session ID to promote",
    )
    promote_parser.set_defaults(func=handle_memory_promote)

    # memory organize
    organize_parser = memory_subs.add_parser(
        "organize",
        help="Organize and consolidate memory",
    )
    organize_parser.add_argument(
        "--depth",
        choices=["quick", "full"],
        help="Override organize_depth from config",
    )
    organize_parser.add_argument(
        "--global",
        dest="global_memory",
        action="store_true",
        help="Organize global memory instead of project memory",
    )
    organize_parser.set_defaults(func=handle_memory_organize)

    # memory history
    history_parser = memory_subs.add_parser(
        "history",
        help="Show memory change history",
    )
    history_parser.add_argument(
        "--path",
        help="Filter to specific path",
    )
    history_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum entries to show",
    )
    history_parser.add_argument(
        "--global",
        dest="global_memory",
        action="store_true",
        help="Show global memory history instead of project memory",
    )
    history_parser.set_defaults(func=handle_memory_history)

    # memory lobotomy
    lobotomy_parser = memory_subs.add_parser(
        "lobotomy",
        help="Excise a topic from memory or perform full memory wipe",
    )
    lobotomy_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be excised without deleting",
    )
    lobotomy_parser.set_defaults(func=handle_memory_lobotomy)

    # memory reconcile
    reconcile_parser = memory_subs.add_parser(
        "reconcile",
        help="Detect and resolve contradictions in memory",
    )
    reconcile_parser.set_defaults(func=handle_memory_reconcile)


def handle_memory_show(args: argparse.Namespace, config: Config | None) -> int:
    """Show memory contents."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    location: MemoryLocation = "global" if args.global_memory else "project"

    if args.global_memory and not memory.global_path:
        console.print("[yellow]Global memory not accessible for this project[/yellow]")
        return 0

    if args.path:
        content = memory.read(args.path, location)
        if content:
            console.print(content)
        else:
            console.print(f"[red]Not found: {args.path}[/red]")
            return 1
    else:
        # Show user context summary
        context = memory.load_user_context()

        console.print("[bold]User Context[/bold]\n")

        if context.preferences:
            console.print("[cyan]Preferences:[/cyan]")
            console.print(context.preferences[:500])
            console.print()

        if context.profile:
            console.print("[cyan]Profile:[/cyan]")
            console.print(context.profile[:500])
            console.print()

        if context.patterns:
            console.print("[cyan]Learned Patterns:[/cyan]")
            console.print(context.patterns[:500])

    return 0


def handle_memory_tree(args: argparse.Namespace, config: Config | None) -> int:
    """Show memory structure as tree."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    if args.global_memory:
        if not memory.global_path:
            console.print(
                "[yellow]Global memory not accessible for this project[/yellow]"
            )
            return 0
        memory_path = memory.global_path
        label = f"Global Memory ({scope.global_access.value} access)"
    else:
        memory_path = memory.project_path
        label = f"Project Memory: {scope.project}"

    if not memory_path.exists():
        console.print("[yellow]Memory directory not initialized yet[/yellow]")
        return 0

    tree = Tree(f"[bold]{label}[/bold] [dim]{memory_path}[/dim]")

    def add_dir(parent: Tree, path: Path, depth: int = 0) -> None:
        if depth > 3:
            return

        for item in sorted(path.iterdir()):
            if item.name.startswith("."):
                continue

            if item.is_dir():
                # Count files in directory
                file_count = sum(
                    1
                    for f in item.rglob("*")
                    if f.is_file() and not f.name.startswith(".")
                )
                count_label = f" [dim]({file_count} files)[/dim]" if file_count else ""
                branch = parent.add(f"[blue]{item.name}/[/blue]{count_label}")
                add_dir(branch, item, depth + 1)
            else:
                size = item.stat().st_size
                size_label = (
                    f" [dim]({size:,} bytes)[/dim]"
                    if size > 0
                    else " [dim](empty)[/dim]"
                )
                parent.add(f"[green]{item.name}[/green]{size_label}")

    add_dir(tree, memory_path)
    console.print(tree)

    return 0


async def handle_memory_prune(args: argparse.Namespace, config: Config | None) -> int:
    """Prune old working memory with promote → commit → delete flow."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    to_prune = memory.prune_working_memory()

    if not to_prune:
        console.print("  (nothing to prune)")
        return 0

    # Show what will be pruned
    console.print(
        f"[dim]Sessions beyond retention limit ({config.memory.working.retention_sessions}):[/dim]"
    )
    for session_id in to_prune:
        size = memory.get_session_size(session_id)
        promoted = (
            " [green](promoted)[/green]"
            if memory.is_session_promoted(session_id)
            else ""
        )
        console.print(f"  {session_id} ({size // 1024}KB){promoted}")

    if args.dry_run:
        console.print("\n[dim]Dry run — no files were modified.[/dim]")
        return 0

    # Promote unpromoted sessions if prune_before_delete is enabled
    if config.memory.working.prune_before_delete:
        unpromoted = [s for s in to_prune if not memory.is_session_promoted(s)]
        if unpromoted:
            console.print(
                f"\n[dim]Promoting {len(unpromoted)} unpromoted session(s)...[/dim]"
            )

            if not await check_lm_studio_available(config.llm.base_url):
                console.print(
                    "[red]Cannot connect to LLM — promotion requires LLM server[/red]"
                )
                console.print(
                    "[dim]Run with LLM available, or set prune_before_delete: false[/dim]"
                )
                return 1

            # Reuse promote logic
            promote_args = argparse.Namespace(session=None, format="md", verbose=False)
            # Temporarily override to only promote the sessions being pruned
            for session_id in unpromoted:
                promote_args.session = session_id
                await handle_memory_promote(promote_args, config)

    # Git commit before deletion (so files are recoverable)
    memory.versioning.auto_commit(f"Pre-prune snapshot: {len(to_prune)} sessions")

    # Delete pruned sessions
    memory.delete_sessions(to_prune)

    # Clean up promoted manifest — remove deleted session IDs
    remaining = memory.list_working_sessions()
    promoted_set = memory._load_promoted_manifest()
    cleaned = promoted_set & set(remaining)
    manifest_path = memory.project_path / "working" / "promoted.json"
    manifest_path.write_text(json.dumps({"promoted": sorted(cleaned)}, indent=2))

    memory.versioning.auto_commit(f"Pruned {len(to_prune)} sessions")
    console.print(
        f"\n[green]Pruned {len(to_prune)} sessions (keeping {len(remaining)})[/green]"
    )

    return 0


def handle_memory_conversations(args: argparse.Namespace, config: Config | None) -> int:
    """List saved conversations."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result
    conversations = memory.get_recent_conversations(limit=args.limit)

    if args.format == "json":
        console.print(json.dumps(conversations, indent=2))
    else:
        if not conversations:
            console.print("[dim]No saved conversations[/dim]")
        else:
            console.print("[bold]Recent Conversations[/bold]\n")
            for conv in conversations:
                console.print(f"  • {conv}")

    return 0


def handle_memory_entities(args: argparse.Namespace, config: Config | None) -> int:
    """List extracted entities."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    entity_types = [args.type] if args.type else ["people", "projects", "concepts"]

    all_entities = {}

    for entity_type in entity_types:
        entities = memory.list_entities(entity_type)
        if entities:
            all_entities[entity_type] = entities

    if args.format == "json":
        console.print(json.dumps(all_entities, indent=2))
    else:
        if not all_entities:
            console.print("[dim]No extracted entities[/dim]")
        else:
            for entity_type, entities in all_entities.items():
                console.print(f"\n[bold]{entity_type.capitalize()}[/bold]")
                for entity in entities:
                    console.print(f"  • {entity}")

    return 0


def _promote_all_to_global(
    memory: MemoryStore, scope: ProjectScope, console: Console
) -> int:
    """
    Promote all project memory items to global memory.

    Iterates over facts, entities (people, projects, concepts),
    procedural patterns/corrections, and episodic events, promoting
    each to global memory.

    Args:
        memory: MemoryStore instance
        scope: Project scope with global access level
        console: Console for output

    Returns:
        Count of items promoted to global memory
    """
    global_count = 0
    promote_paths = [
        "semantic/facts",
        "semantic/entities/people",
        "semantic/entities/projects",
        "semantic/entities/concepts",
        "procedural/patterns",
        "procedural/corrections",
        "episodic/events",
    ]

    failed_count = 0
    for dir_path in promote_paths:
        for fname in memory.list_files(dir_path):
            try:
                memory.promote_to_global(
                    f"{dir_path}/{fname}",
                    reason="CLI memory promote",
                )
                global_count += 1
            except Exception as e:
                logger.warning(
                    "[promote] Failed to promote %s/%s to global: %s",
                    dir_path,
                    fname,
                    e,
                )
                failed_count += 1

    if global_count:
        console.print(
            f"  [green]Promoted {global_count} items to global memory[/green]"
        )
    if failed_count:
        console.print(f"  [yellow]Failed to promote {failed_count} items[/yellow]")

    return global_count


async def handle_memory_promote(args: argparse.Namespace, config: Config | None) -> int:
    """
    Promote working memory sessions.

    Per-turn extraction already saved entities/facts/patterns to project
    memory during the session. This command:
    1. Marks sessions as promoted in the manifest
    2. Promotes project memory to global (if global: full)
    """
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    # Determine which sessions to mark as promoted
    session_ids = [args.session] if args.session else memory.list_unpromoted_sessions()

    if not session_ids:
        console.print("[dim]No unpromoted sessions found[/dim]")
        return 0

    # Mark sessions as promoted
    for session_id in session_ids:
        turns = memory.load_session_turns(session_id)
        turn_count = len(turns) if turns else 0
        console.print(
            f"[dim]Marking session {session_id} as promoted ({turn_count} turns)[/dim]"
        )

    memory.mark_sessions_promoted(session_ids)

    # Promote project memory to global if project has FULL access
    global_count = 0
    if scope.global_access == GlobalAccess.FULL:
        console.print("[dim]Promoting to global memory...[/dim]")
        global_count = _promote_all_to_global(memory, scope, console)
        if global_count:
            memory.commit_global("CLI memory promote (batch)")

    memory.versioning.auto_commit("CLI memory promote")
    console.print(f"\n[green]Marked {len(session_ids)} sessions as promoted[/green]")
    if global_count:
        console.print(f"[green]Promoted {global_count} items to global memory[/green]")
    return 0


async def handle_memory_organize(
    args: argparse.Namespace, config: Config | None
) -> int:
    """Organize and consolidate memory."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    organize_global = args.global_memory
    depth = args.depth or config.memory.organize_depth.value
    location: MemoryLocation = "global" if organize_global else "project"
    label = "global" if organize_global else f"project ({scope.project})"

    if organize_global and not memory.global_path:
        console.print("[yellow]Global memory not accessible for this project[/yellow]")
        return 0

    console.print(f"[dim]Organizing {label} memory (depth: {depth})...[/dim]")

    # Quick mode: promote working sessions (project only, not applicable to global)
    if not organize_global:
        console.print("[dim]Step 1: Promoting working sessions...[/dim]")
        promote_args = argparse.Namespace(session=None, format="md", verbose=False)
        await handle_memory_promote(promote_args, config)

    if depth == "full":
        step = "Step 2" if not organize_global else "Step 1"
        console.print(f"\n[dim]{step}: Reviewing {label} memory for quality...[/dim]")

        if not await require_llm(config, console):
            return 1

        entries = _gather_memory_entries(memory, location)

        if not entries:
            console.print(f"[dim]No {label} memory entries to organize[/dim]")
            return 0

        console.print(f"[dim]Reviewing {len(entries)} memory entries...[/dim]")

        # Cap entries to avoid exceeding context window
        MAX_ENTRIES_CHARS = 6000
        entries_text = "\n\n".join(entries)
        if len(entries_text) > MAX_ENTRIES_CHARS:
            entries_text = entries_text[:MAX_ENTRIES_CHARS] + "\n\n[...truncated...]"

        organize_step = "Step 3" if not organize_global else "Step 2"
        console.print(f"\n[dim]{organize_step}: Organizing {label} memory...[/dim]")

        # Collect actual entity names so the LLM uses exact names
        all_entity_names: list[str] = []
        for entity_type in ["people", "projects", "concepts"]:
            try:
                names = memory.list_entities(entity_type, location)
                all_entity_names.extend(names)
            except Exception:
                pass
        entity_names_text = (
            ", ".join(all_entity_names) if all_entity_names else "(none)"
        )

        ta = config.token_allocation
        async with LLMClient(config.llm) as llm:
            response = await llm.complete(
                prompt=MEMORY_ORGANIZE_PROMPT.format(
                    entries=entries_text,
                    entity_names=entity_names_text,
                ),
                task="memory",
                max_tokens=ta.organize.max_tokens,
                temperature=ta.organize.temperature,
            )

        # Save the organize response as an episodic event
        event_path = memory.save_event(
            "organize",
            f"Memory organization review ({label}, {len(entries)} entries analyzed)",
            {
                "depth": depth,
                "location": location,
                "entries_count": len(entries),
                "response": response,
            },
        )
        console.print(
            f"  Organizational notes can be found at [cyan]{event_path}[/cyan]"
        )

        # Extract and save relationships as a wikilink-style markdown file
        try:
            data = parse_json_response(response)
            relationships = data.get("relationships", [])
            if isinstance(relationships, list) and relationships:
                # Load existing relationships and merge (dedup by entity pair)

                existing = (
                    memory.read("semantic/relationships.md", location)
                    or "# Entity Relationships\n"
                )

                # Build set of existing entity pairs for dedup
                existing_pairs: set[frozenset[str]] = set()
                for line in existing.strip().split("\n"):
                    entities = re.findall(r"\[\[([^\]]+)\]\]", line)
                    if len(entities) >= 2:
                        existing_pairs.add(frozenset(entities))

                new_lines = []
                for rel in relationships:
                    if not isinstance(rel, str) or "[[" not in rel:
                        continue
                    # Extract entity pair
                    entities = re.findall(r"\[\[([^\]]+)\]\]", rel)
                    if len(entities) < 2:
                        continue
                    pair = frozenset(entities)
                    if pair in existing_pairs:
                        continue  # Relationship already exists (different wording)
                    existing_pairs.add(pair)
                    new_lines.append(f"- {rel}")

                if new_lines:
                    updated = existing.rstrip() + "\n" + "\n".join(new_lines) + "\n"
                    memory.write("semantic/relationships.md", updated, location)
                    console.print(
                        f"  [dim]Added {len(new_lines)} new relationships "
                        f"to semantic/relationships.md[/dim]"
                    )

                    # Add bidirectional links to individual entity files
                    links_added = 0
                    for line in new_lines:
                        # Extract all [[entity-name]] from the line
                        entities_in_rel = re.findall(r"\[\[([^\]]+)\]\]", line)
                        if len(entities_in_rel) < 2:
                            continue

                        # For each entity, add links to all other entities in this relationship
                        for entity in entities_in_rel:
                            others = [e for e in entities_in_rel if e != entity]
                            if not others:
                                continue

                            # Find the entity file
                            entity_path = _find_entity_file(memory, entity, location)
                            if not entity_path:
                                continue

                            content = memory.read(entity_path, location) or ""

                            # Add Links section if missing
                            if "\n# Links\n" not in content:
                                content = content.rstrip() + "\n\n# Links\n"

                            # Add each linked entity (skip if already present)
                            for other in others:
                                link = f"[[{other}]]"
                                if link not in content:
                                    content += f"{link}\n"
                                    links_added += 1

                            memory.write(entity_path, content, location)

                    if links_added:
                        console.print(
                            f"  [dim]Added {links_added} bidirectional "
                            f"links to entity files[/dim]"
                        )
        except Exception as e:
            logger.warning("[organize] Could not extract relationships: %s", e)

    memory.versioning.auto_commit(f"Memory organize {label} ({depth})")
    if organize_global:
        memory.commit_global(f"Memory organize global ({depth})")

    console.print(f"\n[green]Organization complete ({label}, depth: {depth})[/green]")
    return 0


def _find_entity_file(
    memory: MemoryStore,
    entity_name: str,
    location: MemoryLocation = "project",
) -> str | None:
    """Find the relative path of an entity file by name.

    Searches people, projects, and concepts directories.
    Returns the relative path (e.g., "semantic/entities/concepts/entity-name")
    or None if not found.
    """
    for entity_type in ["people", "projects", "concepts"]:
        for name in memory.list_entities(entity_type, location):
            if name == entity_name:
                # Check .md first, then .json
                md_path = f"semantic/entities/{entity_type}/{name}.md"
                if memory.read(md_path, location) is not None:
                    return md_path
                json_path = f"semantic/entities/{entity_type}/{name}.json"
                if memory.read(json_path, location) is not None:
                    return json_path
    return None


def _gather_memory_entries(
    memory: MemoryStore, location: MemoryLocation = "project"
) -> list[str]:
    """Gather compact summaries of all memory entries for organization review."""
    entries = []

    # Semantic entities
    for entity_type in ["people", "projects", "concepts"]:
        try:
            for name in memory.list_entities(entity_type, location):
                content = memory.load_entity(entity_type, name, location)
                if isinstance(content, dict):
                    summary = ", ".join(f"{k}: {v}" for k, v in content.items() if v)
                else:
                    summary = str(content)[:200] if content else ""
                entries.append(f"[semantic/entities/{entity_type}/{name}]: {summary}")
        except Exception as e:
            logger.warning(
                "[organize] Failed to gather entities for %s: %s", entity_type, e
            )

    # Semantic facts
    for fname in memory.list_files("semantic/facts", location):
        if not fname.endswith((".md", ".json", ".txt")):
            continue
        try:
            content = memory.read(f"semantic/facts/{fname}", location)
            if content:
                entries.append(f"[semantic/facts/{fname}]: {content[:200]}")
        except (UnicodeDecodeError, OSError) as e:
            logger.warning(
                "[organize] Skipping binary file semantic/facts/%s: %s", fname, e
            )

    # Procedural patterns
    for fname in memory.list_files("procedural/patterns", location):
        if not fname.endswith((".md", ".json", ".txt")):
            continue
        try:
            content = memory.read(f"procedural/patterns/{fname}", location)
            if content:
                entries.append(f"[procedural/patterns/{fname}]: {content[:200]}")
        except (UnicodeDecodeError, OSError) as e:
            logger.warning(
                "[organize] Skipping binary file procedural/patterns/%s: %s", fname, e
            )

    # Procedural corrections
    for fname in memory.list_files("procedural/corrections", location):
        if not fname.endswith((".md", ".json", ".txt")):
            continue
        try:
            content = memory.read(f"procedural/corrections/{fname}", location)
            if content:
                entries.append(f"[procedural/corrections/{fname}]: {content[:200]}")
        except (UnicodeDecodeError, OSError) as e:
            logger.warning(
                "[organize] Skipping binary file procedural/corrections/%s: %s",
                fname,
                e,
            )

    # Existing relationships (so LLM can build on them, not duplicate)
    try:
        relationships = memory.read("semantic/relationships.md", location)
        if relationships and relationships.strip():
            entries.append(
                f"[semantic/relationships.md (existing)]: {relationships[:500]}"
            )
    except (UnicodeDecodeError, OSError):
        pass

    return entries


def handle_memory_history(args: argparse.Namespace, config: Config | None) -> int:
    """Show memory change history."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    if args.global_memory:
        if not memory.global_versioning:
            console.print(
                "[yellow]Global memory not accessible for this project[/yellow]"
            )
            return 0
        versioning = memory.global_versioning
        label = "Global"
    else:
        versioning = memory.versioning
        label = f"Project ({scope.project})"

    history = versioning.get_history(
        path=args.path,
        limit=args.limit,
    )

    if not history:
        console.print(f"[dim]No {label.lower()} version history found[/dim]")
        return 0

    console.print(f"[bold]{label} Memory Version History[/bold]\n")
    for entry in history:
        console.print(
            f"  [cyan]{entry['hash']}[/cyan] "
            f"[dim]{entry['date']}[/dim] "
            f"{entry['message']}"
        )

    return 0


async def handle_memory_lobotomy(
    args: argparse.Namespace, config: Config | None
) -> int:
    """Excise a topic from memory or perform a full memory wipe."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    console.print("\n[bold red]Memory Lobotomy[/bold red]\n")

    mode = (
        Prompt.ask(
            "What would you like to do? (excise/wipe/exit)",
            default="exit",
        )
        .strip()
        .lower()
    )

    if mode in ("exit", "q", "quit"):
        console.print("[dim]Cancelled[/dim]")
        return 0
    elif mode not in ("excise", "wipe"):
        console.print(f"[yellow]Unknown option: {mode}[/yellow]")
        return 1
    elif mode == "excise":
        return await _handle_excise(args, config, memory, scope)
    else:
        return _handle_wipe(config, memory, scope)


async def _handle_excise(
    args: argparse.Namespace,
    config: Config,
    memory: MemoryStore,
    scope: ProjectScope,
) -> int:
    """Excise a specific topic/concept/entity from memory using LLM."""
    topic = Prompt.ask("Topic/concept/entity to excise (or exit/q to cancel)")
    if not topic.strip() or topic.strip().lower() in ("exit", "q", "quit"):
        console.print("[dim]Cancelled[/dim]")
        return 0

    console.print(f"\n[dim]Scanning memory for references to '{topic}'...[/dim]")

    # Gather all memory files with content (skip binary files)
    memory_dirs = [
        "user",
        "semantic/entities/people",
        "semantic/entities/projects",
        "semantic/entities/concepts",
        "semantic/vaults",
        "semantic/facts",
        "procedural/patterns",
        "procedural/corrections",
        "episodic/conversations",
        "episodic/events",
    ]

    file_contents = []
    for dir_path in memory_dirs:
        for fname in memory.list_files(dir_path):
            if not fname.endswith((".md", ".json", ".jsonl", ".txt")):
                continue
            try:
                content = memory.read(f"{dir_path}/{fname}")
                if content:
                    file_contents.append(f"[{dir_path}/{fname}]:\n{content[:500]}")
            except (UnicodeDecodeError, OSError):
                pass

    if not file_contents:
        console.print("[dim]No memory files found[/dim]")
        return 0

    # Use LLM to identify related files
    if not await require_llm(config, console):
        return 1

    ta = config.token_allocation
    async with LLMClient(config.llm) as llm:
        response = await llm.complete(
            prompt=LOBOTOMY_SCAN_PROMPT.format(
                topic=topic,
                files="\n\n".join(file_contents),
            ),
            task="memory",
            max_tokens=ta.lobotomy_scan.max_tokens,
            temperature=ta.lobotomy_scan.temperature,
        )

    try:
        data = parse_json_response(response)
    except (json.JSONDecodeError, ValueError):
        console.print("[red]Could not parse LLM response[/red]")
        return 1

    files_to_delete = data.get("files_to_delete", [])
    files_to_edit = data.get("files_to_edit", [])

    if not files_to_delete and not files_to_edit:
        console.print(f"[green]No memory files found related to '{topic}'[/green]")
        return 0

    # Show what will be affected
    console.print(f"\n[bold]Found references to '{topic}':[/bold]\n")

    if files_to_delete:
        console.print("[bold red]Files to delete:[/bold red]")
        for f in files_to_delete:
            console.print(f"  [red]x[/red] {f['path']} — {f.get('reason', '')}")

    if files_to_edit:
        console.print("\n[bold yellow]Files to edit:[/bold yellow]")
        for f in files_to_edit:
            console.print(
                f"  [yellow]~[/yellow] {f['path']} — "
                f"remove: {f.get('content_to_remove', '')}"
            )

    # Dry run — show what would happen and exit
    if args.dry_run:
        console.print("\n[dim]Dry run — no files were modified.[/dim]")
        return 0

    # Confirm
    console.print()
    confirm = Prompt.ask(
        "[bold red]This is irreversible (versioned history will remain). Proceed?[/bold red]",
        choices=["yes", "no"],
        default="no",
    )

    if confirm != "yes":
        console.print("[dim]Lobotomy cancelled[/dim]")
        return 0

    # Execute deletions in project memory
    deleted = 0
    for f in files_to_delete:
        if memory.delete(f["path"]):
            deleted += 1

    # Execute deletions in global memory if accessible
    if scope.global_access == GlobalAccess.FULL:
        for f in files_to_delete:
            try:
                memory.delete(f["path"], location="global")
            except Exception as e:
                logger.warning(
                    "[prune] Failed to delete global file %s: %s", f["path"], e
                )

    memory.versioning.auto_commit(f"Lobotomy: excised '{topic}'")
    memory.save_event(
        "system",
        f"Memory lobotomy: excised topic '{topic}'",
        {"topic": topic, "files_deleted": deleted},
    )

    console.print(f"\n[green]Excised {deleted} files related to '{topic}'[/green]")
    return 0


def _handle_wipe(config: Config, memory: MemoryStore, scope: ProjectScope) -> int:
    """Full memory wipe for the project."""

    console.print(
        "[bold red]Full memory wipe will delete ALL memory for this project.[/bold red]"
    )
    console.print(f"  Project: [cyan]{scope.project}[/cyan]")
    console.print(f"  Path: [dim]{memory.project_path}[/dim]")

    if scope.global_access == GlobalAccess.FULL:
        console.print("  [yellow]Global memory will also be wiped[/yellow]")

    console.print()
    confirm = Prompt.ask(
        "[bold red]Are you sure? This is irreversible[/bold red]",
        choices=["yes", "no"],
        default="no",
    )
    if confirm != "yes":
        console.print("[dim]Wipe cancelled[/dim]")
        return 0

    # Second confirmation
    confirm2 = Prompt.ask(
        "[bold red]Type the project name to confirm[/bold red]",
    )
    if confirm2 != scope.project:
        console.print("[dim]Project name did not match. Wipe cancelled.[/dim]")
        return 0

    # Version commit before wipe (so history is preserved)
    memory.versioning.auto_commit(
        f"Pre-lobotomy snapshot: full wipe of {scope.project}"
    )

    # Wipe project memory directories
    wipe_dirs = [
        "user",
        "semantic",
        "procedural",
        "episodic",
        "working",
    ]

    deleted_count = 0
    for dir_name in wipe_dirs:
        dir_path = memory.project_path / dir_name
        if dir_path.exists():
            for f in dir_path.rglob("*"):
                if f.is_file():
                    deleted_count += 1
            shutil.rmtree(dir_path)

    # Wipe global memory if accessible
    if scope.global_access == GlobalAccess.FULL and memory.global_path:
        global_project_refs = memory.global_path / "projects" / scope.project
        if global_project_refs.exists():
            shutil.rmtree(global_project_refs)

    # Rebuild directory structure
    memory._ensure_structure()

    memory.versioning.auto_commit(f"Lobotomy: full memory wipe of {scope.project}")

    console.print(f"\n[green]Memory wiped. Deleted {deleted_count} files.[/green]")
    console.print("[dim]Directory structure has been recreated empty.[/dim]")
    return 0


async def handle_memory_reconcile(
    args: argparse.Namespace, config: Config | None
) -> int:
    """Detect and interactively resolve contradictions in memory."""
    if not config:
        return 1

    result = get_memory_store(config, console)
    if result is None:
        return 1
    memory, scope = result

    # Load existing unresolved contradictions
    contradictions = memory.load_contradictions(unresolved_only=True)

    if not contradictions:
        console.print("[dim]No unresolved contradictions found.[/dim]")
        console.print(
            "[dim]Run 'archivyst index rebuild' or a session to detect new ones.[/dim]"
        )
        return 0

    console.print(
        f"\n[bold]Found {len(contradictions)} unresolved contradictions:[/bold]\n"
    )

    for i, c in enumerate(contradictions, 1):
        console.print(f"  [{i}] {c.get('type', 'unknown')} conflict")
        console.print(f"      A: {c.get('source_a', '?')}")
        console.print(f"      B: {c.get('source_b', '?')}")
        console.print()

    # Prompt for selection
    choice = Prompt.ask(
        "Resolve?",
        default="skip",
    )

    if choice.lower() in ("skip", "s", "exit", "quit", "q"):
        return 0

    # Parse selection
    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(contradictions):
            console.print("[yellow]Invalid selection[/yellow]")
            return 0
    except ValueError:
        console.print("[yellow]Enter a number or 'skip'[/yellow]")
        return 0

    selected = contradictions[idx]
    console.print(f"\n[bold]Resolving: {selected.get('id', '?')}[/bold]")
    console.print(f"  A: {selected.get('source_a', '?')}")
    console.print(f"  B: {selected.get('source_b', '?')}")

    console.print("""
  Options:
    [a] Keep A
    [b] Keep B
    [c] Keep both (not a real conflict)
    [d] Discuss with LLM
    [e] Skip
""")

    action = Prompt.ask("Choice", default="e")

    if action.lower() == "e":
        return 0

    if action.lower() == "c":
        # Not a real conflict — resolve as-is
        memory.resolve_contradiction(
            scope=selected.get("scope", "semantic"),
            contradiction_id=selected["id"],
            resolution="Marked as not a real conflict — both entries are valid",
            resolved_by="user",
        )
        memory.versioning.auto_commit(f"Resolved contradiction {selected['id']}")
        console.print(f"[green]Resolved {selected['id']} — kept both[/green]")
        return 0

    if action.lower() in ("a", "b"):
        kept = "A" if action.lower() == "a" else "B"
        resolution = f"Kept source {kept}"
        memory.resolve_contradiction(
            scope=selected.get("scope", "semantic"),
            contradiction_id=selected["id"],
            resolution=resolution,
            resolved_by="user",
        )
        memory.versioning.auto_commit(f"Resolved contradiction {selected['id']}")
        console.print(f"[green]Resolved {selected['id']} — kept {kept}[/green]")
        return 0

    if action.lower() == "d":
        # LLM-assisted resolution
        if not await require_llm(config, console):
            return 1

        ta = config.token_allocation
        console.print("\n[dim]Describe how this should be resolved:[/dim]")
        user_input = Prompt.ask("[bold cyan]You[/bold cyan]")

        if not user_input.strip():
            return 0

        async with LLMClient(config.llm) as llm:
            prompt = CONTRADICTION_RESOLVE_PROMPT.format(
                source_a=selected.get("source_a", "?"),
                text_a=selected.get("source_a", "?"),
                source_b=selected.get("source_b", "?"),
                text_b=selected.get("source_b", "?"),
                conflict_type=selected.get("type", "factual"),
                user_message=user_input,
            )

            response = await llm.complete(
                prompt=prompt,
                task="memory",
                max_tokens=getattr(ta, "contradiction_resolve", ta.organize).max_tokens,
                temperature=getattr(
                    ta, "contradiction_resolve", ta.organize
                ).temperature,
            )

        try:
            data = parse_json_response(response)
        except (json.JSONDecodeError, ValueError):
            console.print("[red]Could not parse LLM response[/red]")
            return 1

        updates = data.get("updates", [])
        summary = data.get("summary", "LLM-assisted resolution")

        if updates:
            console.print("\n[bold]Proposed changes:[/bold]")
            for u in updates:
                console.print(
                    f"  {u.get('path', '?')}: "
                    f"\"{u.get('old_text', '')}\" → \"{u.get('new_text', '')}\""
                )

            apply = Prompt.ask("\nApply changes?", choices=["y", "n"], default="y")
            if apply == "y":
                for u in updates:
                    path = u.get("path", "")
                    old_text = u.get("old_text", "")
                    new_text = u.get("new_text", "")
                    if path and old_text:
                        content = memory.read(path)
                        if content and old_text in content:
                            updated = content.replace(old_text, new_text, 1)
                            memory.write(path, updated)

        memory.resolve_contradiction(
            scope=selected.get("scope", "semantic"),
            contradiction_id=selected["id"],
            resolution=summary,
            resolved_by="llm",
        )
        memory.versioning.auto_commit(f"Resolved contradiction {selected['id']}")
        console.print(f"\n[green]Resolved {selected['id']}: {summary}[/green]")

    return 0
