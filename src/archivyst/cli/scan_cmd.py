"""
CLI scan command handler.

Discovers non-markdown files inside a vault (code files and orphaned images),
converts them to companion .md notes, and maintains a manifest for incremental
change detection.
"""

import argparse
import json
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from archivyst.core.code_converter import compute_source_hash, convert_code_to_markdown
from archivyst.core.code_parser import (
    get_all_code_extensions,
    parse_code_file,
)
from archivyst.core.media import describe_image
from archivyst.core.models import Config
from archivyst.llm.client import LLMClient, check_lm_studio_available

logger = logging.getLogger(__name__)

console = Console()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


@dataclass
class ScanResult:
    """Result of scanning a vault for non-markdown files."""

    new: list[Path] = field(default_factory=list)
    modified: list[Path] = field(default_factory=list)
    moved: list[tuple[str, Path]] = field(default_factory=list)  # (old_key, new_path)
    deleted: list[str] = field(default_factory=list)  # manifest keys
    unchanged: list[Path] = field(default_factory=list)
    orphaned_images: list[Path] = field(default_factory=list)
    model_changed: bool = False
    # Cache of computed hashes (path → hash) to avoid recomputation
    hash_cache: dict[str, str] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        """Whether any actionable changes were found."""
        return bool(
            self.new
            or self.modified
            or self.moved
            or self.deleted
            or self.orphaned_images
        )


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

MANIFEST_FILENAME = "code_manifest.json"


def load_code_manifest(pageindex_path: Path) -> dict[str, dict[str, object]]:
    """Load the code manifest from .pageindex/code_manifest.json."""
    manifest_path = pageindex_path / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[scan] Cannot read code manifest: %s", e)
    return {}


def save_code_manifest(
    pageindex_path: Path, manifest: dict[str, dict[str, object]]
) -> None:
    """Write the code manifest atomically (temp file + rename)."""
    pageindex_path.mkdir(parents=True, exist_ok=True)
    manifest_path = pageindex_path / MANIFEST_FILENAME

    # Symlink guard
    if manifest_path.exists() and manifest_path.is_symlink():
        logger.error(
            "[scan] Manifest path is a symlink — refusing to write: %s", manifest_path
        )
        return

    # Atomic write: temp file in same directory, then rename
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=pageindex_path,
            suffix=".tmp",
            delete=False,
        ) as tf:
            json.dump(manifest, tf, indent=2)
            temp_path = Path(tf.name)

        # Re-check symlink before rename (TOCTOU defense)
        if manifest_path.exists() and manifest_path.is_symlink():
            temp_path.unlink()
            logger.error(
                "[scan] Manifest became symlink before rename — aborting: %s",
                manifest_path,
            )
            return

        temp_path.rename(manifest_path)
    except OSError as e:
        logger.error("[scan] Cannot write code manifest: %s", e)


def _validate_manifest_path(vault_path: Path, rel_path: str) -> Path | None:
    """Validate a path from the manifest is safe (no traversal, no symlink).

    Returns the resolved absolute path, or None if unsafe.
    """
    if ".." in rel_path or rel_path.startswith("/"):
        logger.warning("[scan] Rejecting unsafe manifest path: %s", rel_path)
        return None

    full_path = vault_path / rel_path

    # Check symlink before resolve
    if full_path.is_symlink():
        logger.warning("[scan] Rejecting symlink manifest path: %s", rel_path)
        return None

    # Verify resolved path stays within vault
    try:
        resolved = full_path.resolve()
        vault_resolved = vault_path.resolve()
        if not resolved.is_relative_to(vault_resolved):
            logger.warning("[scan] Path escapes vault: %s -> %s", rel_path, resolved)
            return None
    except (OSError, ValueError):
        return None

    return full_path


# ---------------------------------------------------------------------------
# Vault scanning
# ---------------------------------------------------------------------------


def scan_vault_for_code(
    vault_path: Path,
    pageindex_path: Path,
    current_model: str,
    max_file_size: int = 512_000,
    max_image_size: int = 50_000_000,
) -> ScanResult:
    """Scan a vault for non-markdown files and classify them.

    Finds code files and orphaned images (images not referenced by any note).

    Args:
        vault_path: Root path of the vault.
        pageindex_path: Path to .pageindex/ directory.
        current_model: Current LLM model name from config.
        max_file_size: Skip code files larger than this.
        max_image_size: Skip images larger than this.

    Returns:
        ScanResult with classified files.
    """
    manifest = load_code_manifest(pageindex_path)
    result = ScanResult()
    code_extensions = get_all_code_extensions()

    # Scan vault for code files and image files
    found_files: dict[str, Path] = {}  # relative_key → absolute_path
    found_images: dict[str, Path] = {}  # relative_key → absolute_path

    for path in vault_path.rglob("*"):
        if not path.is_file():
            continue

        # Skip symlinks (prevent scanning outside vault)
        if path.is_symlink():
            continue

        # Skip hidden dirs, .pageindex, .obsidian, .trash
        rel = path.relative_to(vault_path)
        parts = rel.parts
        if any(p.startswith(".") for p in parts):
            continue

        ext = path.suffix.lower().lstrip(".")

        if ext in code_extensions:
            # Skip files that are too large
            try:
                if path.stat().st_size > max_file_size:
                    continue
            except OSError:
                continue
            found_files[str(rel)] = path

        elif ext in IMAGE_EXTENSIONS:
            # Skip images that are too large
            try:
                if path.stat().st_size > max_image_size:
                    continue
            except OSError:
                continue
            found_images[str(rel)] = path

    # Build hash index from manifest for move detection
    manifest_hashes: dict[str, str] = {}
    for mkey, mdata in manifest.items():
        h = str(mdata.get("source_hash", ""))
        if h:
            manifest_hashes[h] = mkey

    # Classify each found code file
    for key, path in found_files.items():
        if key in manifest:
            # Known file — check for modifications
            entry = manifest[key]
            try:
                current_mtime = path.stat().st_mtime
            except OSError:
                continue

            stored_mtime = float(str(entry.get("source_mtime", 0)))
            if abs(current_mtime - stored_mtime) < 0.01:
                result.unchanged.append(path)
            else:
                # mtime differs — verify with hash
                current_hash = compute_source_hash(path)
                result.hash_cache[str(path)] = current_hash
                if current_hash == str(entry.get("source_hash", "")):
                    result.unchanged.append(path)
                else:
                    result.modified.append(path)
        else:
            # Unknown file — check if hash is already tracked (dedup)
            current_hash = compute_source_hash(path)
            result.hash_cache[str(path)] = current_hash
            if current_hash in manifest_hashes:
                old_key = manifest_hashes[current_hash]
                old_path = vault_path / old_key
                if not old_path.exists():
                    # Source at old path gone → file was moved
                    result.moved.append((old_key, path))
                    continue
                # Hash exists and old file/md still present — already handled
                result.unchanged.append(path)
                continue
            result.new.append(path)

    # Check for deleted files (in manifest but not found)
    for mkey in manifest:
        if mkey not in found_files and not (vault_path / mkey).exists():
            result.deleted.append(mkey)

    # Check for model change
    for entry in manifest.values():
        stored_model = str(entry.get("model", ""))
        if stored_model and stored_model != current_model:
            result.model_changed = True
            break

    # Classify orphaned images
    # Collect all image references from indexed notes
    referenced_images: set[str] = set()
    toc_path = pageindex_path / "toc"
    if toc_path.exists():
        for toc_file in toc_path.glob("*.json"):
            try:
                data = json.loads(toc_file.read_text(encoding="utf-8"))
                for ref in data.get("images", []):
                    # Normalize: store both the reference as-is and just the filename
                    referenced_images.add(ref)
                    referenced_images.add(Path(ref).name)
            except (json.JSONDecodeError, OSError):
                continue

    for key, path in found_images.items():
        rel = path.relative_to(vault_path)
        # Check if referenced by any note
        if str(rel) in referenced_images or path.name in referenced_images:
            continue
        # Check if companion .md already exists
        companion = path.parent / f"{path.name}.md"
        if companion.exists():
            continue
        # Check if already in manifest
        if key in manifest:
            continue
        # Check hash dedup
        img_hash = compute_source_hash(path)
        if img_hash in manifest_hashes:
            continue
        result.orphaned_images.append(path)

    return result


# ---------------------------------------------------------------------------
# Conversion + manifest update
# ---------------------------------------------------------------------------


async def convert_and_update_manifest(
    files: list[Path],
    vault_path: Path,
    pageindex_path: Path,
    llm_client: LLMClient | None,
    current_model: str,
    max_file_size: int = 512_000,
    input_source: str = "internal",
    code_max_tokens: int = 150,
    code_temperature: float = 0.3,
    hash_cache: dict[str, str] | None = None,
) -> int:
    """Convert code files to markdown and update the manifest.

    Args:
        files: Code files to convert.
        vault_path: Root path of the vault.
        pageindex_path: Path to .pageindex/ directory.
        llm_client: Optional LLM client for descriptions.
        current_model: Current model name for manifest tracking.
        max_file_size: Max code file size.
        input_source: "internal" for scan, "external" for process.

    Returns:
        Count of files successfully converted.
    """
    manifest = load_code_manifest(pageindex_path)
    converted = 0

    for i, source in enumerate(files, 1):
        rel = source.relative_to(vault_path)
        output_name = f"{source.name}.md"
        output_path = source.parent / output_name

        console.print(f"  [{i}/{len(files)}] {rel} -> {rel.parent / output_name}")

        try:
            parsed = parse_code_file(source, max_file_size=max_file_size)
            content = await convert_code_to_markdown(
                parsed,
                llm_client,
                max_tokens=code_max_tokens,
                temperature=code_temperature,
            )

            # Build frontmatter (use cached hash if available)
            now = datetime.now().isoformat()
            cached = hash_cache.get(str(source)) if hash_cache else None
            source_hash = cached or compute_source_hash(source)
            fm_lines = [
                "---",
                f'source: "{rel}"',
                f'converted_at: "{now}"',
                f"original_format: {source.suffix.lstrip('.')}",
                f"language: {parsed.language}",
                f"source_hash: {source_hash}",
                f"input_source: {input_source}",
                "---",
            ]
            md = "\n".join(fm_lines) + f"\n\n# {source.stem}\n\n{content}\n"
            output_path.write_text(md, encoding="utf-8")

            # Update manifest
            key = str(rel)
            manifest[key] = {
                "generated_md": str(rel.parent / output_name),
                "source_mtime": source.stat().st_mtime,
                "source_size": source.stat().st_size,
                "source_hash": source_hash,
                "converted_at": now,
                "model": current_model,
                "input_source": input_source,
            }
            converted += 1

        except Exception as e:
            logger.warning("[scan] Failed to convert %s: %s", rel, e)
            console.print(f"    [yellow]Failed: {e}[/yellow]")

    save_code_manifest(pageindex_path, manifest)
    return converted


async def convert_images_to_md(
    images: list[Path],
    vault_path: Path,
    pageindex_path: Path,
    llm_client: LLMClient | None,
    current_model: str,
) -> int:
    """Convert orphaned images to companion .md notes with VLM descriptions.

    Args:
        images: Image files to convert.
        vault_path: Root path of the vault.
        pageindex_path: Path to .pageindex/ directory.
        llm_client: LLM client for VLM descriptions (required for images).
        current_model: Current model name for manifest tracking.

    Returns:
        Count of images successfully converted.
    """
    manifest = load_code_manifest(pageindex_path)
    converted = 0

    for i, source in enumerate(images, 1):
        rel = source.relative_to(vault_path)
        output_name = f"{source.name}.md"
        output_path = source.parent / output_name

        console.print(f"  [{i}/{len(images)}] {rel} -> {rel.parent / output_name}")

        try:
            # Generate VLM description
            description = ""
            if llm_client:
                try:
                    img_desc = await describe_image(llm_client, source)
                    description = img_desc.description
                except Exception as e:
                    logger.warning("[scan] VLM description failed for %s: %s", rel, e)
                    description = "(VLM description unavailable)"
            else:
                description = "(No LLM available for image description)"

            # Build markdown with embedded image reference
            now = datetime.now().isoformat()
            source_hash = compute_source_hash(source)
            fm_lines = [
                "---",
                f'source: "{rel}"',
                f'converted_at: "{now}"',
                f"original_format: {source.suffix.lstrip('.')}",
                "language: image",
                f"source_hash: {source_hash}",
                "input_source: internal",
                "---",
            ]
            content = f"![[{source.name}]]\n\n## Description\n\n{description}"
            md = "\n".join(fm_lines) + f"\n\n# {source.name}\n\n{content}\n"
            output_path.write_text(md, encoding="utf-8")

            # Update manifest
            key = str(rel)
            manifest[key] = {
                "generated_md": str(rel.parent / output_name),
                "source_mtime": source.stat().st_mtime,
                "source_size": source.stat().st_size,
                "source_hash": source_hash,
                "converted_at": now,
                "model": current_model,
                "input_source": "internal",
            }
            converted += 1

        except Exception as e:
            logger.warning("[scan] Failed to convert image %s: %s", rel, e)
            console.print(f"    [yellow]Failed: {e}[/yellow]")

    save_code_manifest(pageindex_path, manifest)
    return converted


async def handle_moved_files(
    moved: list[tuple[str, Path]],
    vault_path: Path,
    pageindex_path: Path,
    llm_client: LLMClient | None,
    current_model: str,
    max_file_size: int = 512_000,
    code_max_tokens: int = 150,
    code_temperature: float = 0.3,
) -> int:
    """Handle moved files: delete old .md, regenerate at new location."""
    manifest = load_code_manifest(pageindex_path)
    count = 0

    for old_key, new_path in moved:
        rel = new_path.relative_to(vault_path)
        console.print(f"  {old_key} -> {rel} (moved)")

        # Delete old generated .md
        if old_key in manifest:
            old_md_rel = str(manifest[old_key].get("generated_md", ""))
            old_md = _validate_manifest_path(vault_path, old_md_rel)
            if old_md and old_md.exists():
                old_md.unlink()
            del manifest[old_key]

        # Convert at new location
        save_code_manifest(pageindex_path, manifest)
        result = await convert_and_update_manifest(
            [new_path],
            vault_path,
            pageindex_path,
            llm_client,
            current_model,
            max_file_size,
            code_max_tokens=code_max_tokens,
            code_temperature=code_temperature,
        )
        count += result

    return count


def handle_deleted_files(
    deleted_keys: list[str],
    vault_path: Path,
    pageindex_path: Path,
) -> int:
    """Remove orphaned .md files and manifest entries for deleted sources."""
    manifest = load_code_manifest(pageindex_path)
    count = 0

    for key in deleted_keys:
        if key in manifest:
            md_rel = str(manifest[key].get("generated_md", ""))
            md_path = _validate_manifest_path(vault_path, md_rel)
            if md_path and md_path.exists():
                md_path.unlink()
                console.print(f"  Removed: {manifest[key].get('generated_md', key)}")
                count += 1
            del manifest[key]

    save_code_manifest(pageindex_path, manifest)
    return count


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


def add_scan_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the scan command to parser."""
    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan vault for code files and convert to markdown",
    )
    scan_parser.add_argument(
        "--vault",
        help="Specific vault to scan (default: all active vaults)",
    )
    scan_parser.add_argument(
        "--project",
        help="Project to scan (default: default project)",
    )
    scan_parser.add_argument(
        "--no-index",
        action="store_true",
        help="Skip indexing after conversion",
    )
    scan_parser.set_defaults(func=handle_scan)


async def handle_scan(args: argparse.Namespace, config: Config | None) -> int:
    """Scan vault for code files and convert to markdown."""
    if not config:
        return 1

    # Resolve target — accepts a vault name OR a project name via --vault
    vault_arg = getattr(args, "vault", None)
    project_arg = getattr(args, "project", None)

    vaults_to_scan = []

    if project_arg:
        # Explicit --project flag
        project = config.get_project(project_arg)
        if not project:
            console.print(f"[red]Project '{project_arg}' not found[/red]")
            console.print(f"Available: {', '.join(config.get_project_names())}")
            return 1
        vaults_to_scan = [v for v in project.vaults if v.active]
    elif vault_arg:
        # --vault: search all projects for a matching vault name,
        # then fall back to matching as a project name
        for project in config.projects:
            for vault in project.vaults:
                if vault.name == vault_arg:
                    vaults_to_scan.append(vault)
                    break
        if not vaults_to_scan:
            # Try as a project name
            project = config.get_project(vault_arg)
            if project:
                vaults_to_scan = [v for v in project.vaults if v.active]
            else:
                all_vaults = [v.name for p in config.projects for v in p.vaults]
                all_projects = config.get_project_names()
                console.print(f"[red]'{vault_arg}' not found as vault or project[/red]")
                console.print(f"Vaults: {', '.join(all_vaults)}")
                console.print(f"Projects: {', '.join(all_projects)}")
                return 1
    else:
        # Default project
        vaults_to_scan = [v for v in config.get_default_project_vaults() if v.active]

    if not vaults_to_scan:
        console.print("[yellow]No active vaults to scan[/yellow]")
        return 0

    current_model = config.llm.models.memory
    max_file_size = config.process.max_code_file_size
    code_ta = config.token_allocation.code_description

    total_converted = 0

    for vault_config in vaults_to_scan:
        vault_path = vault_config.path
        pageindex_path = vault_path / ".pageindex"

        if not vault_path.exists():
            console.print(f"[red]Vault path does not exist: {vault_path}[/red]")
            continue

        console.print(f"\n[bold]Scanning vault '{vault_config.name}'...[/bold]")

        max_image_size = config.images.max_image_size
        scan_result = scan_vault_for_code(
            vault_path,
            pageindex_path,
            current_model,
            max_file_size,
            max_image_size=max_image_size,
        )

        # Nothing to do
        if not scan_result.has_changes and not scan_result.model_changed:
            console.print("  [green]No changes detected[/green]")
            continue

        # Print summary
        _print_scan_summary(scan_result)

        # Prompt user
        code_files, image_files = _prompt_user_selection(scan_result, vault_path)

        if (
            not code_files
            and not image_files
            and not scan_result.moved
            and not scan_result.deleted
        ):
            continue

        # Init LLM if needed
        llm_client: LLMClient | None = None
        if code_files or image_files or scan_result.moved:
            if await check_lm_studio_available(config.llm.base_url):
                llm_client = LLMClient(config.llm)
                await llm_client.__aenter__()
            else:
                console.print(
                    "[yellow]LLM not available — proceeding without descriptions[/yellow]"
                )

        try:
            # Convert code files
            if code_files:
                console.print(
                    f"\n[dim]Converting {len(code_files)} code files...[/dim]"
                )
                count = await convert_and_update_manifest(
                    code_files,
                    vault_path,
                    pageindex_path,
                    llm_client,
                    current_model,
                    max_file_size,
                    code_max_tokens=code_ta.max_tokens,
                    code_temperature=code_ta.temperature,
                )
                total_converted += count

            # Convert orphaned images
            if image_files:
                console.print(f"\n[dim]Converting {len(image_files)} images...[/dim]")
                count = await convert_images_to_md(
                    image_files,
                    vault_path,
                    pageindex_path,
                    llm_client,
                    current_model,
                )
                total_converted += count

            # Handle moved files
            if scan_result.moved:
                count = await handle_moved_files(
                    scan_result.moved,
                    vault_path,
                    pageindex_path,
                    llm_client,
                    current_model,
                    max_file_size,
                )
                total_converted += count

            # Handle deleted (orphaned .md)
            if scan_result.deleted:
                confirm = Prompt.ask(
                    "Remove orphaned docs?",
                    choices=["y", "n"],
                    default="y",
                )
                if confirm == "y":
                    handle_deleted_files(
                        scan_result.deleted, vault_path, pageindex_path
                    )

        finally:
            if llm_client:
                await llm_client.__aexit__(None, None, None)

    if total_converted > 0:
        console.print(f"\n[green]Converted {total_converted} files[/green]")

        # Prompt to index
        if not getattr(args, "no_index", False):
            from archivyst.cli.index_cmd import handle_index_rebuild

            answer = Prompt.ask("Run index now?", choices=["y", "n"], default="y")
            if answer == "y":
                index_args = argparse.Namespace(
                    vault=None,
                    full=False,
                    no_summaries=False,
                    no_scan=True,  # Don't re-scan, we just did
                    format="md",
                    verbose=False,
                    config=getattr(args, "config", None),
                    project=getattr(args, "project", None),
                )
                return await handle_index_rebuild(index_args, config)

    return 0


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _print_scan_summary(scan_result: ScanResult) -> None:
    """Print a summary of scan findings."""
    # Count by extension
    ext_counts: dict[str, int] = {}
    all_files = (
        scan_result.new
        + scan_result.modified
        + scan_result.unchanged
        + scan_result.orphaned_images
    )
    for f in all_files:
        ext = f.suffix.lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    for _, new_path in scan_result.moved:
        ext = new_path.suffix.lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    if ext_counts:
        ext_summary = "  ".join(f"{c} {e}" for e, c in sorted(ext_counts.items()))
        console.print(f"  Found: {ext_summary}")

    console.print()
    if scan_result.new:
        console.print(f"  New files:         {len(scan_result.new)}")
    if scan_result.modified:
        console.print(f"  Modified:          {len(scan_result.modified)}")
    if scan_result.orphaned_images:
        console.print(f"  Orphaned images:   {len(scan_result.orphaned_images)}")
    if scan_result.moved:
        for old_key, new_path in scan_result.moved:
            console.print(f"  Moved:             {old_key} -> {new_path.name}")
    if scan_result.deleted:
        console.print(f"  Deleted source:    {len(scan_result.deleted)}")
    if scan_result.unchanged:
        console.print(f"  Unchanged:         {len(scan_result.unchanged)}")
    if scan_result.model_changed:
        console.print("  [yellow]Model changed since last scan[/yellow]")


def _prompt_user_selection(
    scan_result: ScanResult,
    vault_path: Path,
) -> tuple[list[Path], list[Path]]:
    """Prompt user to select which files to convert.

    Returns:
        Tuple of (code_files, image_files) to convert.
    """
    actionable_code = scan_result.new + scan_result.modified
    actionable_images = scan_result.orphaned_images
    all_actionable = actionable_code + actionable_images

    if not all_actionable:
        return [], []

    console.print()
    choice = Prompt.ask(
        "Include files?",
        choices=["all", "types", "files", "skip"],
        default="all",
    )

    if choice == "skip":
        return [], []

    if choice == "all":
        return actionable_code, actionable_images

    if choice == "types":
        return _select_by_type(actionable_code, actionable_images)

    # choice == "files"
    return _select_by_file(all_actionable, vault_path)


def _select_by_type(
    code_files: list[Path],
    image_files: list[Path],
) -> tuple[list[Path], list[Path]]:
    """Select files by extension type."""
    # Group code files by extension
    ext_groups: dict[str, list[Path]] = {}
    for f in code_files:
        ext = f.suffix.lower()
        if ext not in ext_groups:
            ext_groups[ext] = []
        ext_groups[ext].append(f)

    selected_code: list[Path] = []
    for ext, files in sorted(ext_groups.items()):
        answer = Prompt.ask(
            f"  Include {ext} ({len(files)} files)?",
            choices=["y", "n"],
            default="y",
        )
        if answer == "y":
            selected_code.extend(files)

    # Images as a group
    selected_images: list[Path] = []
    if image_files:
        answer = Prompt.ask(
            f"  Include orphaned images ({len(image_files)} files)?",
            choices=["y", "n"],
            default="y",
        )
        if answer == "y":
            selected_images = image_files

    return selected_code, selected_images


def _select_by_file(
    all_files: list[Path],
    vault_path: Path,
) -> tuple[list[Path], list[Path]]:
    """Select individual files with a toggle interface."""
    # Build numbered list, all selected by default
    selected = [True] * len(all_files)

    def _display_list() -> None:
        for idx, path in enumerate(all_files):
            rel = path.relative_to(vault_path)
            mark = "x" if selected[idx] else " "
            ext = path.suffix.lower().lstrip(".")
            label = str(rel)
            if ext in IMAGE_EXTENSIONS:
                label += "  (orphaned image)"
            console.print(f"  [{idx + 1:>3}] [{mark}] {label}")

    _display_list()

    while True:
        inp = Prompt.ask(
            "\nToggle by number (3), range (1-4), or 'done'",
            default="done",
        )
        inp = inp.strip().lower()

        if inp in ("done", "d"):
            break

        # Parse number or range
        try:
            if "-" in inp:
                start_s, end_s = inp.split("-", 1)
                start = int(start_s) - 1
                end = int(end_s)  # inclusive, so no -1
                for i in range(start, min(end, len(all_files))):
                    selected[i] = not selected[i]
            else:
                idx = int(inp) - 1
                if 0 <= idx < len(all_files):
                    selected[idx] = not selected[idx]
                else:
                    console.print(f"  [yellow]Invalid number: {inp}[/yellow]")
                    continue
        except ValueError:
            console.print(f"  [yellow]Invalid input: {inp}[/yellow]")
            continue

        _display_list()

    # Split back into code and image lists
    code_extensions = get_all_code_extensions()
    selected_code: list[Path] = []
    selected_images: list[Path] = []

    for idx, path in enumerate(all_files):
        if not selected[idx]:
            continue
        ext = path.suffix.lower().lstrip(".")
        if ext in IMAGE_EXTENSIONS:
            selected_images.append(path)
        elif ext in code_extensions:
            selected_code.append(path)

    return selected_code, selected_images
