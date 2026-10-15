"""
CLI query command handlers.
"""

import argparse
import json
import logging

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from archivyst.agent.memory import MemoryStore
from archivyst.agent.orchestrator import ArchivystAgent
from archivyst.agent.prompts import (
    REMEMBER_CLASSIFY_PROMPT,
)
from archivyst.cli.utils import get_memory_store, require_llm
from archivyst.core.index import VaultIndex, VaultManager
from archivyst.core.models import Config, ProjectScope
from archivyst.llm.client import LLMClient

logger = logging.getLogger(__name__)

console = Console()


def _check_vaults_indexed(config: Config) -> bool:
    """Check that all active vaults have been indexed. Returns True if OK."""
    vaults = config.get_default_project_vaults()
    if not vaults:
        return True

    missing = []
    for vault_config in vaults:
        if not vault_config.active:
            continue
        index = VaultIndex(vault_config, config)
        if not index.manifest_path.exists():
            missing.append(vault_config.name)

    if missing:
        for name in missing:
            console.print(f"[red]No index found for vault '{name}'.[/red]")
        console.print(
            "\nRun [bold]archivyst index[/bold] to build the index, "
            "then retry your command."
        )
        return False
    return True


async def _check_oversized_sessions(
    config: Config,
    memory: MemoryStore,
    console: Console,
) -> None:
    """
    Check for working session files exceeding size limit.

    If oversized files are found:
    - auto_promote=True: promote and delete automatically
    - auto_promote=False: prompt user for confirmation
    """
    max_size = MemoryStore.MAX_MEMORY_FILE_SIZE
    oversized_sessions: list[str] = []

    for session_id in memory.list_working_sessions():
        session_dir = memory.project_path / "working" / "sessions" / session_id
        if not session_dir.exists():
            continue
        total_size = sum(
            f.stat().st_size for f in session_dir.rglob("*") if f.is_file()
        )
        if total_size > max_size:
            oversized_sessions.append(session_id)

    if not oversized_sessions:
        return

    auto_promote = config.memory.working.auto_promote

    if auto_promote:
        console.print(
            f"[dim]Auto-promoting {len(oversized_sessions)} "
            f"oversized session(s) before context ingestion...[/dim]"
        )
    else:
        console.print(
            f"[yellow]{len(oversized_sessions)} working session(s) "
            f"exceed the {max_size // 1024}KB file size limit:[/yellow]"
        )
        for sid in oversized_sessions:
            session_dir = memory.project_path / "working" / "sessions" / sid
            size = sum(f.stat().st_size for f in session_dir.rglob("*") if f.is_file())
            console.print(f"  {sid} ({size // 1024}KB)")

        confirm = Prompt.ask(
            "Promote these sessions to memory before starting?",
            choices=["yes", "no"],
            default="yes",
        )
        if confirm != "yes":
            return

    # Mark and delete oversized sessions (per-turn extraction already
    # saved entities/facts/patterns during the original session)
    for session_id in oversized_sessions:
        memory.mark_sessions_promoted([session_id])
        memory.delete_sessions([session_id])
        console.print(f"  [dim]Marked and cleaned: {session_id}[/dim]")

    memory.versioning.auto_commit("Pre-session: promoted oversized sessions")


def _show_memory_status(
    agent: ArchivystAgent,
    memory_store: MemoryStore,
    scope: ProjectScope,
    console: Console,
) -> None:
    """
    Display verbose memory status for the current session.

    Shows which project memory layers are loaded and summarizes
    global memory contents.

    Args:
        agent: The active ArchivystAgent instance
        memory_store: MemoryStore for file listing
        scope: Project scope with global access level
        console: Console for output
    """
    ctx = agent._user_context
    if ctx is None:
        console.print("[dim]Memory loaded (project): (empty)[/dim]")
        return
    loaded = []
    if ctx.preferences:
        loaded.append("preferences")
    if ctx.profile:
        loaded.append("profile")
    if ctx.vault_meta:
        loaded.append("vault meta")
    if ctx.entities:
        loaded.append("entities")
    if ctx.facts:
        loaded.append("facts")
    if ctx.patterns:
        loaded.append("patterns")
    if ctx.corrections:
        loaded.append("corrections")
    if loaded:
        console.print(f"[dim]Memory loaded (project): {', '.join(loaded)}[/dim]")
    else:
        console.print("[dim]Memory loaded (project): (empty)[/dim]")

    # Show global memory status
    if memory_store.global_path:
        global_access = scope.global_access.value
        global_items = []
        try:
            for gdir in [
                "user",
                "semantic/entities/people",
                "semantic/entities/projects",
                "semantic/entities/concepts",
                "semantic/facts",
                "procedural/patterns",
                "procedural/corrections",
            ]:
                files = memory_store.list_files(gdir, "global")
                if files:
                    global_items.append(f"{gdir.split('/')[-1]}({len(files)})")
        except Exception as e:
            logger.warning("[session] Failed to read global memory status: %s", e)
        if global_items:
            console.print(
                f"[dim]Memory loaded (global, {global_access}):"
                f" {', '.join(global_items)}[/dim]"
            )
        else:
            console.print(
                f"[dim]Memory loaded (global, {global_access}): (empty)[/dim]"
            )
    else:
        console.print("[dim]Global memory: not accessible[/dim]")


def add_query_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add query subcommands to parser."""
    # One-shot query
    query_parser = subparsers.add_parser(
        "query",
        help="Run a single query",
    )
    query_parser.add_argument(
        "question",
        help="Question to ask",
    )
    query_parser.add_argument(
        "--vault",
        action="append",
        help="Specific vault(s) to search",
    )
    query_parser.add_argument(
        "--show-sources",
        action="store_true",
        help="Show source documents used",
    )
    query_parser.add_argument(
        "--show-trace",
        action="store_true",
        help="Show reasoning trace",
    )
    query_parser.add_argument(
        "--mode",
        choices=["strict", "lax"],
        help="Response mode: strict (vault only) or lax (vault + own knowledge)",
    )
    query_parser.set_defaults(func=handle_query)

    # Interactive session
    session_parser = subparsers.add_parser(
        "session",
        help="Start interactive session",
    )
    session_parser.add_argument(
        "--vault",
        action="append",
        help="Specific vault(s) to use",
    )
    session_parser.add_argument(
        "--mode",
        choices=["strict", "lax"],
        help="Response mode: strict (vault only) or lax (vault + own knowledge)",
    )
    session_parser.set_defaults(func=handle_session)


async def handle_query(args: argparse.Namespace, config: Config | None) -> int:
    """Run a single query."""
    if not config:
        return 1

    if not _check_vaults_indexed(config):
        return 1

    # Check LLM
    console.print("Connecting to LLM server...")
    if not await require_llm(config, console):
        console.print("Make sure LM Studio is running with API server enabled.")
        return 1

    result = get_memory_store(config, console)
    if result is None:
        console.print("[red]Run 'archivyst config init'.[/red]")
        return 1
    memory_store, scope = result

    async with LLMClient(config.llm) as llm:
        vault_manager = VaultManager(config, llm)

        agent = ArchivystAgent(config, llm, vault_manager, memory_store)

        # Set response mode (CLI flag > project config > default)
        mode = getattr(args, "mode", None)
        if mode:
            agent.response_mode = mode
        else:
            project = config.get_default_project()
            if project:
                agent.response_mode = project.response_mode

        # Start session with specified vaults
        await agent.start_session(active_vaults=args.vault)

        console.print(f"Querying: [cyan]{args.question}[/cyan]\n")

        # Run retrieval (for source display)
        if args.show_sources or args.show_trace:
            retrieval = await agent.retrieve(args.question, scope=args.vault)

            if args.show_trace:
                console.print("[dim]Reasoning trace:[/dim]")
                for step in retrieval.reasoning_trace:
                    console.print(f"  [dim]• {step}[/dim]")
                console.print()

            # Synthesize answer
            answer = await agent.synthesize(args.question, retrieval)

            # Show answer
            if args.format == "json":
                output = {
                    "question": args.question,
                    "answer": answer,
                    "sources": [
                        {
                            "vault": s.vault,
                            "note": str(s.note_path),
                            "section": s.section_heading,
                        }
                        for s in retrieval.sources
                    ],
                    "trace": retrieval.reasoning_trace,
                }
                console.print(json.dumps(output, indent=2))
            else:
                console.print(Panel(Markdown(f"\n{answer}"), title="Answer"))

                if args.show_sources and retrieval.sources:
                    console.print("\n[dim]Sources:[/dim]")
                    for source in retrieval.sources:
                        section = (
                            f" > {source.section_heading}"
                            if source.section_heading
                            else ""
                        )
                        console.print(
                            f"  [dim]• {source.vault}:{source.note_path.name}{section}[/dim]"
                        )

        else:
            # Simple query
            answer = await agent.query(args.question)

            if args.format == "json":
                console.print(json.dumps({"answer": answer}, indent=2))
            else:
                console.print(Panel(Markdown(f"\n{answer}"), title="Answer"))

        await agent.end_session(save_conversation=False)

    return 0


async def handle_session(args: argparse.Namespace, config: Config | None) -> int:
    """Start interactive session."""
    if not config:
        return 1

    if not _check_vaults_indexed(config):
        return 1

    # Check LLM
    console.print("Connecting to LLM server...")
    if not await require_llm(config, console):
        return 1

    console.print("[green]Connected[/green]\n")

    result = get_memory_store(config, console)
    if result is None:
        console.print("[red]Run 'archivyst config init'.[/red]")
        return 1
    memory_store, scope = result

    async with LLMClient(config.llm) as llm:
        vault_manager = VaultManager(config, llm)

        # Check for oversized working session files before context ingestion
        await _check_oversized_sessions(config, memory_store, console)

        agent = ArchivystAgent(config, llm, vault_manager, memory_store)

        # Set response mode (CLI flag > project config > default)
        mode = getattr(args, "mode", None)
        if mode:
            agent.response_mode = mode
        else:
            project = config.get_default_project()
            if project:
                agent.response_mode = project.response_mode

        session_id = await agent.start_session(active_vaults=args.vault)

        console.print(f"[dim]Session {session_id} started[/dim]")
        console.print(f"[dim]Response mode: {agent.response_mode}[/dim]")
        console.print(
            f"[dim]Active vaults: {', '.join(agent.session.scope.all_knowledge_vaults)}[/dim]"
        )
        console.print("[dim]Memory briefing sent to LLM[/dim]")

        if args.verbose and agent._user_context:
            _show_memory_status(agent, memory_store, scope, console)

        console.print("[dim]Type 'exit' or Ctrl+C to end session[/dim]\n")

        try:
            while True:
                try:
                    query = Prompt.ask("[bold cyan]You[/bold cyan]")
                except EOFError:
                    break

                if not query.strip():
                    continue

                if query.lower() in ("exit", "quit", "q"):
                    break

                # Skip terminal escape sequences (arrow keys, etc.)
                if "\x1b" in query or query.startswith("^["):
                    console.print("[dim]Input not recognized[/dim]")
                    continue

                # Handle special commands
                if query.startswith("/"):
                    await handle_session_command(query, agent, args)
                    continue

                try:
                    console.print()
                    answer = await agent.query(query)
                    console.print(
                        Panel(
                            Markdown(f"\n{answer}"),
                            title="[bold green]Archivyst[/bold green]",
                        )
                    )

                    # Show retrieval trace in verbose mode
                    if args.verbose and agent._last_retrieval:
                        r = agent._last_retrieval
                        console.print("\n[dim]--- Retrieval Trace ---[/dim]")
                        for step in r.reasoning_trace:
                            console.print(f"  [dim]{step}[/dim]")
                        if r.sources:
                            console.print("[dim]Sources:[/dim]")
                            for s in r.sources:
                                section = (
                                    f" > {s.section_heading}"
                                    if s.section_heading
                                    else ""
                                )
                                console.print(
                                    f"  [dim]  {s.note_path.name}{section}"
                                    f" ({s.relevance_reason})[/dim]"
                                )
                        console.print(
                            f"[dim]Content keys: {list(r.content.keys())}[/dim]"
                        )
                        console.print("[dim]--- End Trace ---[/dim]")

                    console.print()

                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")
                    if args.verbose:
                        console.print_exception()

        finally:
            await agent.end_session(save_conversation=True)
            console.print("\n[dim]Session ended[/dim]")

    return 0


async def handle_session_command(
    command: str,
    agent: ArchivystAgent,
    args: argparse.Namespace,
) -> None:
    """Handle special session commands."""
    parts = command[1:].split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "help":
        mode = agent.response_mode
        console.print(f"""
[bold]Session Commands:[/bold]
  /help              Show this help
  /trace             Show retrieval trace from last query
  /vaults            List active vaults
  /sources           Show sources from last query
  /clear             Clear conversation history
  /save              Save conversation now
  /remember <text>   Save information to memory
  /promote           Promote session learnings to long-term memory
  /mode <strict|lax> Switch response mode (current: {mode})
  /exit              End session
""")

    elif cmd == "vaults":
        console.print(
            f"Active vaults: {', '.join(agent.session.scope.all_knowledge_vaults)}"
        )

    elif cmd == "clear":
        agent.session.conversation.clear()
        console.print("[dim]Conversation cleared[/dim]")

    elif cmd == "save":
        agent.memory.save_conversation(agent.session)
        console.print("[dim]Conversation saved[/dim]")

    elif cmd == "trace":
        r = agent._last_retrieval
        if not r:
            console.print("[dim]No retrieval yet — ask a question first[/dim]")
        else:
            console.print("\n[bold]Retrieval Trace[/bold]")
            for step in r.reasoning_trace:
                console.print(f"  [dim]{step}[/dim]")
            if r.sources:
                console.print("\n[bold]Sources[/bold]")
                for s in r.sources:
                    section = f" > {s.section_heading}" if s.section_heading else ""
                    console.print(
                        f"  {s.note_path.name}{section} [dim]({s.relevance_reason})[/dim]"
                    )
            console.print("\n[bold]Content passed to synthesis[/bold]")
            for key, val in r.content.items():
                preview = val[:150].replace("\n", " ")
                console.print(f"  [cyan]{key}[/cyan]: {preview}...")
            console.print()

    elif cmd == "sources":
        r = agent._last_retrieval
        if not r:
            console.print("[dim]No retrieval yet — ask a question first[/dim]")
        elif not r.sources:
            console.print("[dim]No sources used in last query[/dim]")
        else:
            console.print("\n[bold]Sources[/bold]")
            for s in r.sources:
                section = f" > {s.section_heading}" if s.section_heading else ""
                console.print(
                    f"  {s.note_path.name}{section} "
                    f"[dim]({s.relevance_reason})[/dim]"
                )
            console.print()

    elif cmd == "remember":
        if not arg:
            console.print("[yellow]Usage: /remember <text>[/yellow]")
            return

        # Use LLM to classify the memory
        try:
            response = await agent.llm.complete(
                prompt=REMEMBER_CLASSIFY_PROMPT.format(text=arg),
                task="memory",
                max_tokens=200,
                temperature=0.3,
            )
            data = agent._parse_json_response(response)
            suggested_type = str(data.get("type", "fact"))
            suggested_key = str(data.get("key", arg[:50]))
            suggested_value = str(data.get("value", arg))
        except Exception:
            suggested_type = "fact"
            suggested_key = arg[:50].lower().replace(" ", "-")
            suggested_value = arg

        # Let user confirm or override
        mem_type = Prompt.ask(
            f"Save as [dim](classified: {suggested_type})[/dim]",
            choices=["preference", "fact", "pattern"],
            default=suggested_type,
        )

        if mem_type == "preference":
            agent.memory.save_user_preference(suggested_key, suggested_value)
        elif mem_type == "fact":
            agent.memory.save_fact(suggested_key, suggested_value)
        elif mem_type == "pattern":
            agent.memory.save_pattern(suggested_key, suggested_value)

        agent.memory.versioning.auto_commit(f"Remember: {suggested_key}")
        console.print(f"[dim]Saved to {mem_type} memory[/dim]")

    elif cmd == "promote":
        session_id = agent.session.session_id
        if agent.memory.is_session_promoted(session_id):
            console.print("[dim]Session already promoted[/dim]")
        else:
            await agent.promote_session()
            console.print(
                f"[green]Session {session_id[:8]} marked as promoted[/green]"
            )

    elif cmd == "mode":
        if arg.lower() in ("strict", "lax"):
            agent.response_mode = arg.lower()
            console.print(f"[dim]Response mode set to: {agent.response_mode}[/dim]")
        elif not arg:
            console.print(f"[dim]Current response mode: {agent.response_mode}[/dim]")
        else:
            console.print("[yellow]Usage: /mode strict|lax[/yellow]")

    elif cmd in ("exit", "quit", "q"):
        raise EOFError  # Signal the session loop to exit

    else:
        console.print(f"[yellow]Unknown command: {cmd}[/yellow]")
        console.print("Type /help for available commands")
