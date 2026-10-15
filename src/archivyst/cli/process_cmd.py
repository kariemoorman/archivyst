"""
CLI process command handler.

Converts mixed file types into markdown for vault ingestion.
"""

import argparse
import csv
import json
import logging
import shutil
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from archivyst.agent.prompts import (
    STRUCTURE_FILES_PROMPT,
    STRUCTURE_PARA_INSTRUCTION,
    STRUCTURE_TOPIC_INSTRUCTION,
)
from archivyst.cli.config_cmd import handle_init
from archivyst.cli.index_cmd import handle_index_rebuild
from archivyst.cli.scan_cmd import load_code_manifest, save_code_manifest
from archivyst.cli.utils import get_config_path, require_llm
from archivyst.core.code_converter import (
    compute_source_hash,
    convert_code_to_markdown,
)
from archivyst.core.code_parser import get_all_code_extensions, parse_code_file
from archivyst.core.media import describe_image, extract_pdf_text
from archivyst.core.models import Config
from archivyst.llm.client import LLMClient
from archivyst.llm.utils import parse_json_response

logger = logging.getLogger(__name__)

console = Console()


def add_process_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the process command to parser."""
    process_parser = subparsers.add_parser(
        "process",
        help="Convert files to markdown for vault ingestion",
    )
    process_parser.add_argument(
        "input_path",
        nargs="?",
        help="Path to directory containing source files",
    )
    process_parser.add_argument(
        "project_name",
        nargs="?",
        help="Project name (creates project if needed)",
    )
    process_parser.add_argument(
        "--output",
        help="Override output vault path (skips project lookup)",
    )
    process_parser.add_argument(
        "--structure",
        nargs="?",
        const="prompt",
        choices=["prompt", "flat", "para", "topic"],
        help="Organize output files (flat/para/topic, or prompt to choose)",
    )
    process_parser.add_argument(
        "--index",
        action="store_true",
        help="Auto-run index after conversion",
    )
    process_parser.set_defaults(func=handle_process)


async def handle_process(args: argparse.Namespace, config: Config | None) -> int:
    """Convert files to markdown for vault ingestion."""

    # Resolve input path
    input_path = _resolve_input_path(args)
    if input_path is None:
        return 1

    # Resolve output path and project
    output_path, project_name = await _resolve_output(args, config)
    if output_path is None:
        return 1

    # Get supported formats from config
    supported = (
        set(config.process.supported_formats)
        if config
        else {
            "md",
            "txt",
            "csv",
            "tsv",
            "json",
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "gif",
            "webp",
        }
    )
    exclude_patterns = (
        config.process.exclude_patterns if config else ["*.tmp", ".DS_Store"]
    )

    # Scan input directory
    files_to_process = _scan_input(input_path, supported, exclude_patterns)
    if not files_to_process:
        console.print("[yellow]No supported files found in input directory[/yellow]")
        return 0

    console.print(
        f"\n[bold]Processing {len(files_to_process)} files "
        f"from {input_path}[/bold]\n"
    )

    # Check if LLM is needed (for PDF/image/code conversion)
    llm_extensions = {
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
    } | get_all_code_extensions()
    needs_llm = any(
        f.suffix.lower().lstrip(".") in llm_extensions for f in files_to_process
    )

    llm_client: LLMClient | None = None
    if needs_llm and config:
        if not await require_llm(config, console):
            console.print(
                "[yellow]Continuing without LLM — "
                "PDF/image files will use basic extraction[/yellow]"
            )
        else:
            llm_client = LLMClient(config.llm)
            await llm_client.__aenter__()

    # Convert files
    converted: list[Path] = []
    code_extensions = get_all_code_extensions()
    pageindex_path = output_path / ".pageindex"
    manifest = load_code_manifest(pageindex_path)

    try:
        for i, source_file in enumerate(files_to_process, 1):
            rel_path = source_file.relative_to(input_path)
            # Preserve file type in name: parser.py → parser.py.md, data.csv → data.csv.md
            output_file = output_path / rel_path.parent / f"{rel_path.name}.md"
            output_file.parent.mkdir(parents=True, exist_ok=True)

            console.print(f"  [{i}/{len(files_to_process)}] {rel_path}")

            max_code_size = config.process.max_code_file_size if config else 512_000
            code_ta = config.token_allocation.code_description if config else None
            result = await _convert_file(
                source_file, output_file, llm_client, max_code_size, code_ta
            )
            if result:
                converted.append(result)

                # Track code files in manifest for dedup with scan
                ext = source_file.suffix.lower().lstrip(".")
                if ext in code_extensions:
                    from datetime import datetime as dt

                    output_rel = result.relative_to(output_path)
                    manifest[str(output_rel)] = {
                        "generated_md": str(output_rel),
                        "source_mtime": source_file.stat().st_mtime,
                        "source_size": source_file.stat().st_size,
                        "source_hash": compute_source_hash(source_file),
                        "converted_at": dt.now().isoformat(),
                        "model": (config.llm.models.memory if config else "unknown"),
                        "input_source": "external",
                    }
    finally:
        if llm_client:
            await llm_client.__aexit__(None, None, None)

    # Save code manifest if any code files were converted
    if any(str(e.get("input_source")) == "external" for e in manifest.values()):
        save_code_manifest(pageindex_path, manifest)

    console.print(f"\n[green]Converted {len(converted)} files to markdown[/green]")

    # Structure if requested
    if args.structure and config:
        await _handle_structure(args.structure, converted, output_path, config)

    # Index prompt
    did_index = False
    if args.index:
        console.print("\n[dim]Running index...[/dim]")
        index_args = argparse.Namespace(
            vault=None,
            full=True,
            no_summaries=False,
            no_scan=True,
            no_link=False,
            format="md",
            verbose=False,
        )
        await handle_index_rebuild(index_args, config)
        did_index = True
    else:
        run_index = Prompt.ask(
            "\nRun index now?",
            choices=["y", "n"],
            default="y",
        )
        if run_index == "y":
            index_args = argparse.Namespace(
                vault=None,
                full=True,
                no_summaries=False,
                no_scan=False,
                format="md",
                verbose=False,
            )
            await handle_index_rebuild(index_args, config)
            did_index = True

    # Show completion hint
    if project_name:
        console.print("\n[green]To begin interacting with your data:[/green]")
        if not did_index:
            console.print(f"  archivyst index --project={project_name}")
        console.print(f"  archivyst session --project={project_name}")

    return 0


def _resolve_input_path(args: argparse.Namespace) -> Path | None:
    """Resolve and validate the input directory path."""
    if args.input_path:
        if args.input_path.lower() in ("exit", "quit", "q"):
            console.print("[dim]Cancelled[/dim]")
            return None
        input_path = Path(args.input_path).expanduser().resolve()
    else:
        raw = Prompt.ask("Enter path to data directory (or exit/q to cancel)")
        if not raw.strip() or raw.strip().lower() in ("exit", "quit", "q"):
            console.print("[dim]Cancelled[/dim]")
            return None
        input_path = Path(raw).expanduser().resolve()

    if not input_path.exists():
        console.print(f"[red]Path does not exist: {input_path}[/red]")
        return None
    if not input_path.is_dir():
        console.print(f"[red]Path is not a directory: {input_path}[/red]")
        return None

    return input_path


async def _resolve_output(
    args: argparse.Namespace, config: Config | None
) -> tuple[Path | None, str | None]:
    """Resolve output path and project name. Returns (output_path, project_name)."""

    # Explicit output overrides everything
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        # Reject obviously dangerous paths
        dangerous = {"/", "/etc", "/usr", "/var", "/bin", "/sbin", "/tmp"}
        if str(output_path) in dangerous or output_path == Path.home():
            console.print(
                f"[red]Refusing to write to {output_path} — "
                f"specify a vault directory[/red]"
            )
            return None, None
        output_path.mkdir(parents=True, exist_ok=True)
        return output_path, None

    # Get or prompt for project name
    project_name = args.project_name
    if not project_name:
        project_name = Prompt.ask("Enter a project name (or exit/q to cancel)")
        if not project_name.strip() or project_name.strip().lower() in (
            "exit",
            "quit",
            "q",
        ):
            console.print("[dim]Cancelled[/dim]")
            return None, None

    # Check if project exists
    if config and config.get_project(project_name):
        project = config.get_project(project_name)
        if project and project.vaults:
            vault_path = project.vaults[0].path
            console.print(
                f"[dim]Using existing project '{project_name}' "
                f"vault: {vault_path}[/dim]"
            )
            return vault_path, project_name

    # Create new project via init
    console.print(f"\n[dim]Creating project '{project_name}'...[/dim]")

    init_args = argparse.Namespace(
        project_name=project_name,
        vault=None,
        memory_path=None,
        global_access="full",
        set_default=True,
        config=getattr(args, "config", None),
    )
    result = handle_init(init_args, config)
    if result != 0:
        console.print("[red]Failed to create project[/red]")
        return None, None

    # Reload config to get the new project's vault path
    config_path = get_config_path(args)
    new_config = Config.load(config_path)
    project = new_config.get_project(project_name)
    if project and project.vaults:
        return project.vaults[0].path, project_name

    console.print("[red]Could not determine vault path[/red]")
    return None, None


def _scan_input(
    input_path: Path,
    supported: set[str],
    exclude_patterns: list[str],
) -> list[Path]:
    """Scan input directory for supported files."""
    files = []
    for f in sorted(input_path.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower().lstrip(".")
        if ext not in supported:
            continue
        rel = str(f.relative_to(input_path))
        if any(fnmatch(rel, pat) for pat in exclude_patterns):
            continue
        if any(fnmatch(f.name, pat) for pat in exclude_patterns):
            continue
        files.append(f)
    return files


async def _convert_file(
    source: Path,
    output: Path,
    llm_client: LLMClient | None,
    max_code_file_size: int = 512_000,
    code_token_config: object | None = None,
) -> Path | None:
    """Convert a single file to markdown. Returns output path or None."""
    ext = source.suffix.lower().lstrip(".")
    now = datetime.now().isoformat()

    try:
        if ext == "md":
            shutil.copy2(source, output)
            return output

        elif ext == "txt":
            content = source.read_text(encoding="utf-8", errors="replace")
            _write_md(output, source, ext, now, source.stem, content)
            return output

        elif ext in ("csv", "tsv"):
            delimiter = "\t" if ext == "tsv" else ","
            content = _convert_tabular(source, delimiter)
            _write_md(output, source, ext, now, source.stem, content)
            return output

        elif ext == "json":
            content = _convert_json_file(source)
            _write_md(output, source, ext, now, source.stem, content)
            return output

        elif ext == "pdf":
            content = _convert_pdf(source, llm_client)
            _write_md(output, source, ext, now, source.stem, content)
            return output

        elif ext in ("png", "jpg", "jpeg", "gif", "webp"):
            content = await _convert_image(source, output, llm_client)
            _write_md(output, source, ext, now, source.stem, content)
            return output

        elif ext in get_all_code_extensions():
            parsed = parse_code_file(source, max_file_size=max_code_file_size)
            code_mt = int(getattr(code_token_config, "max_tokens", 150))
            code_temp = float(getattr(code_token_config, "temperature", 0.3))
            content = await convert_code_to_markdown(
                parsed,
                llm_client,
                max_tokens=code_mt,
                temperature=code_temp,
            )
            extra_fm = {
                "language": parsed.language,
                "source_hash": compute_source_hash(source),
                "input_source": "external",
            }
            _write_md(
                output,
                source,
                ext,
                now,
                source.stem,
                content,
                extra_frontmatter=extra_fm,
            )
            return output

        else:
            logger.warning("[process] Unsupported format: %s", ext)
            return None

    except Exception as e:
        logger.warning("[process] Failed to convert %s: %s", source.name, e)
        console.print(f"    [yellow]Failed: {e}[/yellow]")
        return None


def _write_md(
    output: Path,
    source: Path,
    fmt: str,
    timestamp: str,
    title: str,
    content: str,
    extra_frontmatter: dict[str, str] | None = None,
) -> None:
    """Write a converted markdown file with frontmatter."""
    # Quote source path to prevent YAML injection from filenames
    safe_source = str(source).replace('"', '\\"')
    fm_lines = [
        "---",
        f'source: "{safe_source}"',
        f'converted_at: "{timestamp}"',
        f"original_format: {fmt}",
    ]
    if extra_frontmatter:
        for key, value in extra_frontmatter.items():
            fm_lines.append(f"{key}: {value}")
    fm_lines.append("---")

    md = "\n".join(fm_lines) + f"\n\n# {title}\n\n{content}\n"
    output.write_text(md, encoding="utf-8")


def _convert_tabular(source: Path, delimiter: str) -> str:
    """Convert CSV/TSV to markdown table."""
    lines: list[str] = []
    with open(source, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        rows = list(reader)

    if not rows:
        return "(empty file)"

    # Header row
    headers = rows[0]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")

    # Data rows
    for row in rows[1:]:
        # Pad or truncate to match header count
        padded = row + [""] * (len(headers) - len(row))
        lines.append("| " + " | ".join(padded[: len(headers)]) + " |")

    return "\n".join(lines)


def _convert_json_file(source: Path) -> str:
    """Convert JSON to structured markdown."""
    raw = source.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return f"```json\n{raw[:5000]}\n```"

    return _json_to_markdown(data)


def _json_to_markdown(data: object, depth: int = 0) -> str:
    """Recursively convert JSON to markdown with headings."""
    lines: list[str] = []
    heading = "#" * min(depth + 2, 6)

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{heading} {key}\n")
                lines.append(_json_to_markdown(value, depth + 1))
            else:
                lines.append(f"**{key}**: {value}\n")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                lines.append(_json_to_markdown(item, depth))
                lines.append("")
            else:
                lines.append(f"- {item}")
    else:
        lines.append(str(data))

    return "\n".join(lines)


def _convert_pdf(source: Path, llm_client: LLMClient | None) -> str:
    """Convert PDF to markdown text."""
    try:
        return extract_pdf_text(source)
    except ImportError:
        return f"(PDF extraction requires pymupdf: {source.name})"
    except Exception as e:
        return f"(PDF extraction failed: {e})"


async def _convert_image(
    source: Path,
    output: Path,
    llm_client: LLMClient | None,
) -> str:
    """Convert image to markdown with VLM description."""
    # Copy image to output directory alongside the markdown
    img_dest = output.parent / source.name
    shutil.copy2(source, img_dest)

    description = f"![[{source.name}]]"

    if llm_client:
        try:

            result = await describe_image(llm_client, source)
            description = (
                f"![[{source.name}]]\n\n" f"## Description\n\n{result.description}"
            )
        except Exception as e:
            logger.warning(
                "[process] VLM description failed for %s: %s",
                source.name,
                e,
            )

    return description


async def _handle_structure(
    strategy: str,
    converted: list[Path],
    output_path: Path,
    config: Config,
) -> None:
    """Apply folder structure to converted files."""

    # Prompt for strategy if not specified
    if strategy == "prompt":
        console.print("\nHow would you like to organize the files?")
        console.print("  1. flat    — all files in vault root")
        console.print("  2. para    — Projects / Areas / Resources / Archive")
        console.print("  3. topic   — group by topic/theme (LLM suggests)")
        strategy = Prompt.ask(
            "\nSelect",
            choices=["1", "2", "3", "flat", "para", "topic"],
            default="1",
        )
        # Normalize numeric choices
        strategy_map = {"1": "flat", "2": "para", "3": "topic"}
        strategy = strategy_map.get(strategy, strategy)

    if strategy == "flat":
        _flatten_files(converted, output_path)
        return

    # Para and topic need LLM
    if not await require_llm(config, console):
        console.print("[yellow]LLM not available — skipping structure[/yellow]")
        return

    # Build file summaries for LLM
    file_summaries = []
    for f in converted:
        content = f.read_text(encoding="utf-8", errors="replace")
        # Extract title and first paragraph
        lines = content.split("\n")
        title = f.stem
        for line in lines:
            if line.startswith("# "):
                title = line[2:].strip()
                break
        first_para = ""
        in_body = False
        for line in lines:
            if in_body and line.strip():
                first_para = line.strip()[:200]
                break
            if line.startswith("# "):
                in_body = True
        file_summaries.append(f"- {f.name}: {title} — {first_para}")

    files_text = "\n".join(file_summaries)

    # Select instruction
    instruction = (
        STRUCTURE_PARA_INSTRUCTION
        if strategy == "para"
        else STRUCTURE_TOPIC_INSTRUCTION
    )

    async with LLMClient(config.llm) as llm:
        response = await llm.complete(
            prompt=STRUCTURE_FILES_PROMPT.format(
                files=files_text,
                strategy_instruction=instruction,
            ),
            task="memory",
            max_tokens=1000,
            temperature=0.3,
        )

    try:
        data = parse_json_response(response)
        folders = data.get("folders", {})
    except Exception:
        console.print("[yellow]Could not parse structure suggestion[/yellow]")
        return

    if not folders:
        console.print("[dim]No structure suggested[/dim]")
        return

    # Show proposed structure
    console.print("\n[bold]Proposed structure:[/bold]")
    for folder, files in folders.items():
        console.print(f"  [cyan]{folder}/[/cyan]")
        for fname in files:
            console.print(f"    {fname}")

    confirm = Prompt.ask(
        "\nApply this structure?",
        choices=["y", "n"],
        default="y",
    )
    if confirm != "y":
        return

    # Move files (validate folder names from LLM to prevent path traversal)
    moved = 0
    output_resolved = output_path.resolve()
    for folder, files in folders.items():
        # Reject unsafe folder names
        if ".." in folder or folder.startswith("/") or "\x00" in folder:
            logger.warning(
                "[process] Rejecting unsafe folder name from LLM: %s", folder
            )
            continue
        folder_path = output_path / folder
        if not folder_path.resolve().is_relative_to(output_resolved):
            logger.warning("[process] Folder escapes output path: %s", folder)
            continue
        folder_path.mkdir(parents=True, exist_ok=True)
        for fname in files:
            if ".." in fname or fname.startswith("/"):
                continue
            source = output_path / fname
            if source.exists():
                dest = folder_path / fname
                if not dest.resolve().is_relative_to(output_resolved):
                    continue
                source.rename(dest)
                moved += 1

    console.print(f"[green]Moved {moved} files into {len(folders)} folders[/green]")


def _flatten_files(files: list[Path], output_path: Path) -> None:
    """Move all files to vault root (flat structure)."""
    moved = 0
    for f in files:
        if f.parent != output_path:
            dest = output_path / f.name
            f.rename(dest)
            moved += 1
    if moved:
        console.print(f"[green]Flattened {moved} files to vault root[/green]")
