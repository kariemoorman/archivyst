"""
CLI config command handlers.
"""

import argparse
import json
import re
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from archivyst.cli import obsidian_defaults
from archivyst.cli.utils import get_config_path
from archivyst.core.models import Config

console = Console()


def init_obsidian_vault(vault_path: Path, vault_name: str) -> None:
    """
    Initialize an Obsidian vault directory structure.

    Creates:
    - .obsidian/ with core config files
    - attachments/ for media
    - A welcome note
    """
    vault_path.mkdir(parents=True, exist_ok=True)

    obsidian_dir = vault_path / ".obsidian"
    obsidian_dir.mkdir(exist_ok=True)

    # Write Obsidian config files
    (obsidian_dir / "app.json").write_text(
        json.dumps(obsidian_defaults.APP_JSON, indent=2)
    )
    (obsidian_dir / "appearance.json").write_text(
        json.dumps(obsidian_defaults.APPEARANCE_JSON, indent=2)
    )
    (obsidian_dir / "core-plugins.json").write_text(
        json.dumps(obsidian_defaults.CORE_PLUGINS_JSON, indent=2)
    )
    (obsidian_dir / "graph.json").write_text(
        json.dumps(obsidian_defaults.GRAPH_JSON, indent=2)
    )
    (obsidian_dir / "workspace.json").write_text(
        json.dumps(obsidian_defaults.WORKSPACE_JSON, indent=2)
    )

    # Create attachments directory
    (vault_path / "attachments").mkdir(exist_ok=True)

    # Create a welcome note
    welcome_note = vault_path / "Welcome.md"
    if not welcome_note.exists():
        welcome_note.write_text(f"""\
# Welcome to {vault_name}

This vault was created by Archivyst.

## Getting Started

- Create new notes using your preferred method
- Use `[[wikilinks]]` to connect ideas
- Run `archivyst index rebuild --project=<name>` to index this vault

## Structure

```
{vault_name}/
├── .obsidian/      # Obsidian configuration
├── attachments/    # Images and files
└── your notes...
```
""")


def init_shared_memory_vault(memory_path: Path) -> None:
    """
    Initialize the shared agent-memory vault structure.

    Creates:
    - global/ for cross-project knowledge
    - projects/ directory for project-specific memory
    - Obsidian config
    """
    memory_path.mkdir(parents=True, exist_ok=True)

    # Initialize as Obsidian vault
    init_obsidian_vault(memory_path, "agent-memory")

    # Create global structure
    global_dirs = [
        "global/user",
        "global/semantic/entities/people",
        "global/semantic/entities/organizations",
        "global/semantic/entities/concepts",
        "global/procedural/patterns",
        "global/procedural/corrections",
        "global/attachments",
    ]

    for dir_path in global_dirs:
        (memory_path / dir_path).mkdir(parents=True, exist_ok=True)

    # Create default global files
    user_prefs = memory_path / "global/user/preferences.md"
    if not user_prefs.exists():
        user_prefs.write_text("# User Preferences\n\n(No preferences set yet)\n")

    user_profile = memory_path / "global/user/profile.md"
    if not user_profile.exists():
        user_profile.write_text("# User Profile\n\n(No profile information yet)\n")

    # Create projects directory
    (memory_path / "projects").mkdir(exist_ok=True)


def init_project_memory(memory_path: Path, project_name: str, is_shared: bool) -> None:
    """
    Initialize memory structure for a project.

    Args:
        memory_path: Root memory path (shared vault or separate vault)
        project_name: Name of the project
        is_shared: Whether this is in the shared memory vault
    """
    project_path = memory_path / "projects" / project_name if is_shared else memory_path

    project_path.mkdir(parents=True, exist_ok=True)

    # Create project memory structure
    project_dirs = [
        "user",
        "episodic/conversations",
        "episodic/events",
        "semantic/entities/people",
        "semantic/entities/projects",
        "semantic/entities/concepts",
        "semantic/vaults",
        "semantic/facts",
        "procedural/patterns",
        "procedural/corrections",
        "working/queue",
        "attachments",
    ]

    for dir_path in project_dirs:
        (project_path / dir_path).mkdir(parents=True, exist_ok=True)

    # Create default project files
    prefs = project_path / "user/preferences.md"
    if not prefs.exists():
        prefs.write_text(
            f"# {project_name} Preferences\n\n(No project-specific preferences yet)\n"
        )

    readme = project_path / "README.md"
    if not readme.exists():
        readme.write_text(f"""\
# {project_name} Memory

This directory contains agent memory for the **{project_name}** project.

## Structure

- `user/` - Project-specific user preferences
- `episodic/` - Conversations and events
- `semantic/` - Entities, facts, vault metadata
- `procedural/` - Learned patterns and corrections
- `working/` - Current task state and queue
- `attachments/` - Files referenced by memory notes
""")


def add_config_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add config subcommands to parser."""
    config_parser = subparsers.add_parser(
        "config",
        help="Configuration management",
    )

    def _config_help(args: argparse.Namespace, config: Config | None) -> int:
        config_parser.print_help()
        return 0

    config_parser.set_defaults(func=_config_help)
    config_subs = config_parser.add_subparsers(dest="config_command")

    # config show
    show_parser = config_subs.add_parser(
        "show",
        help="Show current configuration",
    )
    show_parser.set_defaults(func=handle_config_show)

    # config validate
    validate_parser = config_subs.add_parser(
        "validate",
        help="Validate configuration file",
    )
    validate_parser.set_defaults(func=handle_config_validate)


def add_init_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the init command to parser."""
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a new project",
    )
    init_parser.add_argument(
        "project_name",
        help="Name of the project to create",
    )
    init_parser.add_argument(
        "--vault",
        help="Path to knowledge vault (default: ~/obsidian-vaults/<project>)",
        type=str,
    )
    init_parser.add_argument(
        "--memory-path",
        help="Path to separate memory vault (default: use shared memory)",
        type=str,
    )
    init_parser.add_argument(
        "--global",
        dest="global_access",
        choices=["full", "read", "none"],
        default="full",
        help="Global memory access level (default: full)",
    )
    init_parser.add_argument(
        "--set-default",
        action="store_true",
        help="Set this as the default project",
    )
    init_parser.set_defaults(func=handle_init)


def get_example_config_path() -> Path:
    """Get path to example config file."""
    # Navigate from cli/config_cmd.py → cli/ → archivyst/ → src/ → project root
    pkg_dir = Path(__file__).parent.parent.parent.parent
    example = pkg_dir / "config.example.yaml"
    if example.exists():
        return example
    # Fallback: try relative to the archivyst package
    return Path(__file__).parent.parent / "config.example.yaml"


def _strip_section_comment(
    yaml: YAML, config_data: CommentedMap, comment_text: str
) -> CommentedMap:
    """
    Strip a section comment from config data and reload.

    Ruamel attaches comments between sections (e.g. "# LLM configuration")
    to the preceding sequence, causing appended items to appear after
    the comment. Fix: dump -> strip the comment -> reload.

    Args:
        yaml: Configured YAML instance
        config_data: Current config data
        comment_text: The comment text to strip (without leading #)

    Returns:
        Reloaded CommentedMap with the comment stripped
    """
    buf = StringIO()
    yaml.dump(config_data, buf)
    raw = buf.getvalue()

    raw = raw.replace(f"\n# {comment_text}\n", "\n")

    result: CommentedMap = yaml.load(raw)
    return result


def _init_existing_config(
    yaml: YAML,
    config_path: Path,
    config_data: CommentedMap,
    project_name: str,
    vault_path: Path,
    memory_path: Path | None,
    shared_memory_path: Path,
    global_access: str,
    args: argparse.Namespace,
) -> tuple[bool, Path]:
    """
    Handle adding a project to an existing config file.

    Args:
        yaml: Configured YAML instance
        config_path: Path to config file
        config_data: Loaded config data
        project_name: Name of the new project
        vault_path: Path to the knowledge vault
        memory_path: Optional separate memory path
        shared_memory_path: Default shared memory path
        global_access: Global memory access level
        args: CLI arguments

    Returns:
        Tuple of (set_as_default, actual_memory_path)
    """
    actual_memory_path = shared_memory_path
    set_as_default = False

    # Use memory path from existing config if set
    if "memory" in config_data and "path" in config_data["memory"]:
        actual_memory_path = Path(config_data["memory"]["path"]).expanduser()

    # Check if project already exists
    existing_projects = config_data.get("projects", [])
    for p in existing_projects:
        if p.get("name") == project_name:
            console.print(f"[yellow]Project '{project_name}' already exists[/yellow]")
            raise SystemExit(1)

    # Build project config using ruamel.yaml types for proper formatting
    vault_config = CommentedMap()
    vault_config["name"] = f"{project_name}-vault"
    vault_config["path"] = str(vault_path)
    vault_config["active"] = True

    vaults_list = CommentedSeq()
    vaults_list.append(vault_config)

    project_config = CommentedMap()
    project_config["name"] = project_name
    project_config["vaults"] = vaults_list

    if memory_path:
        mem_config = CommentedMap()
        mem_config["path"] = str(memory_path)
        project_config["memory"] = mem_config
    elif global_access != "full":
        mem_config = CommentedMap()
        mem_config["global_access"] = global_access
        project_config["memory"] = mem_config

    # Add project to config
    if "projects" not in config_data:
        config_data["projects"] = CommentedSeq()

    # Strip section comment, reload, append project
    config_data = _strip_section_comment(yaml, config_data, "LLM configuration")
    config_data["projects"].append(project_config)

    # Re-attach the comment to the llm key
    if "llm" in config_data:
        config_data.yaml_set_comment_before_after_key(
            "llm", before="\nLLM configuration"
        )

    # Set default if requested or first project
    if args.set_default or not config_data.get("default_project"):
        config_data["default_project"] = project_name
        set_as_default = True

    # Ensure memory section has all required fields
    if "memory" not in config_data:
        config_data["memory"] = CommentedMap()
        config_data["memory"]["path"] = str(shared_memory_path)

    mem = config_data["memory"]
    existing_path = mem.get("path", str(shared_memory_path))

    if "working" not in mem:
        new_mem = CommentedMap()
        new_mem["path"] = existing_path

        working = CommentedMap()
        working["retention_sessions"] = mem.get("working", {}).get(
            "retention_sessions", 5
        )
        working["promotion_interval_minutes"] = mem.get("working", {}).get(
            "promotion_interval_minutes", 30
        )
        working["auto_promote"] = mem.get("working", {}).get("auto_promote", False)
        working["prune_before_delete"] = mem.get("working", {}).get(
            "prune_before_delete", True
        )
        new_mem["working"] = working

        new_mem["organize_depth"] = mem.get("organize_depth", "quick")

        config_data["memory"] = new_mem

    # Write config (preserves formatting)
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    # Clean up double blank lines left by ruamel comment handling
    raw_out = config_path.read_text()
    raw_out = re.sub(r"\n{3,}", "\n", raw_out)
    config_path.write_text(raw_out)

    return set_as_default, actual_memory_path


def _init_new_config(
    config_path: Path,
    project_name: str,
    vault_path: Path,
    memory_path: Path | None,
    shared_memory_path: Path,
    global_access: str,
    memory_section: str,
) -> None:
    """
    Create a brand-new config file from template.

    Args:
        config_path: Path to write the config file
        project_name: Name of the project
        vault_path: Path to the knowledge vault
        memory_path: Optional separate memory path (unused, for signature clarity)
        shared_memory_path: Default shared memory path
        global_access: Global memory access level (unused, for signature clarity)
        memory_section: Pre-formatted memory section YAML string
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config_content = f"""\
# Archivyst Configuration

# Shared memory vault (used by all projects unless overridden)
memory:
  path: {shared_memory_path}
  working:
    # Number of sessions to retain before pruning
    retention_sessions: 5
    # Interval (minutes) for auto promotion check during session
    promotion_interval_minutes: 30
    # Enable auto-promotion (false = interactive, true = autonomous)
    auto_promote: false
    # Attempt to promote important items before deleting during prune
    prune_before_delete: true
  # Memory organization depth (quick or full)
  organize_depth: quick

# Default project when --project flag not specified
default_project: {project_name}

# Project definitions
projects:
  - name: {project_name}
    vaults:
      - name: {project_name}-vault
        path: {vault_path}
        active: true
{memory_section}

# LLM configuration (OpenAI-compatible inference server)
llm:
  # API endpoint (LM Studio, Ollama, vLLM, llama.cpp, OpenAI, Anthropic, etc.)
  base_url: http://localhost:1234/v1

  # API key (optional for local servers, required for OpenAI/Anthropic)
  # Supports ${{ENV_VAR}} syntax. Also auto-detects from environment:
  # OPENAI_API_KEY, ANTHROPIC_API_KEY, ARCHIVYST_API_KEY
  # api_key: ${{OPENAI_API_KEY}}

  # Model assignments per task
  models:
    summarization: qwen2.5-7b-instruct
    routing: qwen2.5-7b-instruct
    synthesis: qwen2.5-14b-instruct
    vision: qwen3-vl
    memory: qwen2.5-7b-instruct

  # Optional: separate endpoint for VLM if running on different port
  vision_base_url: null

  # Request timeout in seconds
  timeout: 120

  # Max retries on transient failures
  max_retries: 3
  min_request_interval: 0.1


# Token allocation per operation
token_allocation:
  synthesis:           {{ max_tokens: 4000, temperature: 0.5 }}
  routing:             {{ max_tokens: 300,  temperature: 0.3 }}
  briefing:            {{ max_tokens: 50,   temperature: 0.3 }}
  session_extraction:  {{ max_tokens: 1000,  temperature: 0.3 }}
  summarization:
    max_section_tokens: 150
    max_note_tokens: 200
    temperature: 0.3
    max_section_chars: 2000
    max_note_chars: 3000
  entity_extraction:   {{ max_tokens: 500,  temperature: 0.3 }}
  memory_extraction:   {{ max_tokens: 500,  temperature: 0.3 }}
  organize:            {{ max_tokens: 4000, temperature: 0.3 }}
  lobotomy_scan:       {{ max_tokens: 1000, temperature: 0.3 }}
  code_description:    {{ max_tokens: 150,  temperature: 0.3 }}
  code_summary:        {{ max_tokens: 150,  temperature: 0.3 }}
  contradiction_check: {{ max_tokens: 1000,  temperature: 0.3 }}
  contradiction_resolve: {{ max_tokens: 1000, temperature: 0.3 }}
  retrieval:
    max_context_chars: 8000
    max_briefing_chars: 24000
    short_file_chars: 4000
    max_query_length: 10000
    heading_match_threshold: 0.5
    content_match_threshold: 0.4

# Indexing configuration
indexing:
  # incremental: only reindex changed files
  # full: rebuild entire index
  strategy: incremental

  # Generate LLM summaries for each section
  summarize_sections: true

  # Wikilink resolution strategy:
  # strict: exact filename match
  # case_insensitive: ignore case
  # case_insensitive_with_aliases: also check frontmatter aliases
  wikilink_resolution: case_insensitive_with_aliases


  # Scan for code files in vault before indexing
  scan_code: true

  # File patterns to exclude from indexing
  exclude_patterns:
    - "*.excalidraw.md"
    - ".obsidian/*"
    - ".trash/*"

# File processing / import configuration
process:
  # File types to convert (others are skipped)
  supported_formats:
    - md
    - txt
    - csv
    - tsv
    - json
    - pdf
    - png
    - jpg
    - jpeg
    - gif
    - webp
    # Code files
    - py
    - c
    - h
    - cpp
    - hpp
    - java
    - js
    - ts
    - go
    - rs
    - rb
    - sql
    - cs
    - swift
    - kt
    - sh
    - bash
    - lua
    - php
    - r
    - cob
    - cbl
  # Exclude patterns for input files
  exclude_patterns:
    - "*.tmp"
    - ".DS_Store"
    - __MACOSX
  # Maximum code file size in bytes (files larger are skipped)
  max_code_file_size: 512000

# PDF processing
pdf:
  enabled: true
  ocr_fallback: true
  store_summaries: true
  max_summary_chars: 8000

# Image processing
images:
  max_image_size: 50000000 # 50MB max image file size
  vlm_descriptions: true
  formats:
    - png
    - jpg
    - jpeg
    - gif
    - webp

# Logging
logging:
  level: INFO
  file: null
  max_file_size: 5000000
  backup_count: 3
"""
    config_path.write_text(config_content)


def handle_init(args: argparse.Namespace, config: Config | None) -> int:
    """Initialize a new project."""

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.default_flow_style = False

    config_path = get_config_path(args)
    project_name = args.project_name

    # Determine paths
    vault_path = (
        Path(args.vault).expanduser()
        if args.vault
        else Path.home() / "obsidian-vaults" / project_name
    )
    memory_path = Path(args.memory_path).expanduser() if args.memory_path else None
    shared_memory_path = Path.home() / "obsidian-vaults" / "agent-memory"

    global_access = args.global_access
    if memory_path:
        global_access = "none"  # Separate memory implies no global access

    # Track the actual memory path to use (may differ from default if config exists)
    actual_memory_path = shared_memory_path
    set_as_default = False

    # Load or create config
    if config_path.exists():
        # Existing config - use ruamel.yaml to preserve formatting
        with open(config_path) as f:
            config_data = yaml.load(f)

        if config_data is None:
            config_data = {}

        try:
            set_as_default, actual_memory_path = _init_existing_config(
                yaml,
                config_path,
                config_data,
                project_name,
                vault_path,
                memory_path,
                shared_memory_path,
                global_access,
                args,
            )
        except SystemExit:
            return 1

    else:
        # New config - always sets as default
        set_as_default = True

        # Build memory config section
        if memory_path:
            memory_section = f"    memory:\n      path: {memory_path}"
        elif global_access != "full":
            memory_section = f"    memory:\n      global_access: {global_access}"
        else:
            memory_section = (
                "    # memory: inherited from global, global_access: full (default)"
            )

        _init_new_config(
            config_path,
            project_name,
            vault_path,
            memory_path,
            shared_memory_path,
            global_access,
            memory_section,
        )

    console.print(f"[green]Created/updated config at {config_path}[/green]")

    # Create knowledge vault
    if not vault_path.exists():
        console.print(f"Creating knowledge vault: [cyan]{vault_path}[/cyan]")
        init_obsidian_vault(vault_path, f"{project_name}-vault")
    else:
        console.print(f"Knowledge vault exists: [dim]{vault_path}[/dim]")

    # Create/initialize memory
    if memory_path:
        # Separate memory vault
        if not memory_path.exists():
            console.print(f"Creating separate memory vault: [cyan]{memory_path}[/cyan]")
            init_obsidian_vault(memory_path, f"{project_name}-memory")
            init_project_memory(memory_path, project_name, is_shared=False)
        else:
            console.print(f"Memory vault exists: [dim]{memory_path}[/dim]")
    else:
        # Shared memory vault - use actual_memory_path (from config or default)
        if not actual_memory_path.exists():
            console.print(
                f"Creating shared memory vault: [cyan]{actual_memory_path}[/cyan]"
            )
            init_shared_memory_vault(actual_memory_path)

        # Create project memory directory
        project_memory = actual_memory_path / "projects" / project_name
        if not project_memory.exists():
            console.print(f"Creating project memory: [cyan]{project_memory}[/cyan]")
            init_project_memory(actual_memory_path, project_name, is_shared=True)
        else:
            console.print(f"Project memory exists: [dim]{project_memory}[/dim]")

    console.print(f"\n[green]Project '{project_name}' initialized![/green]")
    console.print(f"\nGlobal access: [cyan]{global_access}[/cyan]")
    if set_as_default:
        console.print("[dim]Set as default project[/dim]")

    console.print("\n[bold]Next steps:[/bold]")
    console.print(f"  1. Add notes to [cyan]{vault_path}[/cyan]")
    console.print(
        f"  2. Run [bold]archivyst index rebuild --project={project_name}[/bold]"
    )
    console.print(f"  3. Run [bold]archivyst session --project={project_name}[/bold]\n")

    return 0


def handle_config_show(args: argparse.Namespace, config: Config | None) -> int:
    """Show current configuration."""
    config_path = get_config_path(args)

    if not config_path.exists():
        console.print(f"[red]Config not found at {config_path}[/red]")
        console.print("Run [bold]archivyst init <project-name>[/bold] to create one.")
        return 1

    content = config_path.read_text()

    if args.format == "json" and config:
        console.print(config.model_dump_json(indent=2))
    else:
        syntax = Syntax(content, "yaml", theme="monokai", line_numbers=True)
        console.print(syntax)

    return 0


def handle_config_validate(args: argparse.Namespace, config: Config | None) -> int:
    """Validate configuration file."""

    config_path = get_config_path(args)

    if not config_path.exists():
        console.print(f"[red]Config not found at {config_path}[/red]")
        return 1

    try:
        config = Config.load(config_path)
        console.print("[green]Configuration is valid[/green]")

        # Show summary
        console.print(f"\nShared memory: {config.memory.path}")
        if not config.memory.path.exists():
            console.print("  [yellow]Path does not exist (will be created)[/yellow]")

        console.print(f"\nDefault project: {config.default_project or '(none)'}")

        console.print(f"\nProjects ({len(config.projects)}):")
        for project in config.projects:
            vaults = ", ".join(v.name for v in project.vaults)
            global_str = project.memory.global_access.value

            if project.memory.path:
                mem_str = f"separate: {project.memory.path}"
            else:
                mem_str = f"shared (global: {global_str})"

            console.print(f"  [cyan]{project.name}[/cyan]")
            console.print(f"    Vaults: {vaults}")
            console.print(f"    Memory: {mem_str}")

        console.print(f"\nLLM endpoint: {config.llm.base_url}")

        return 0

    except Exception as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        return 1
