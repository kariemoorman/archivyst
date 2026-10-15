"""
Vault index management.

Handles ToC tree storage, incremental updates, and manifest tracking.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

from archivyst.core.markdown import build_note_toc, parse_note, resolve_wikilink
from archivyst.core.media import describe_image
from archivyst.core.models import (
    Config,
    IndexStrategy,
    NoteToC,
    VaultConfig,
    VaultManifest,
)
from archivyst.core.summarizer import add_summaries_to_toc
from archivyst.llm.client import LLMClient

logger = logging.getLogger(__name__)


class VaultIndex:
    """
    Manages the Archivyst for a single vault.

    Handles ToC generation, storage, and retrieval.
    """

    def __init__(
        self,
        vault_config: VaultConfig,
        config: Config,
        llm_client: LLMClient | None = None,
    ):
        self.vault_config = vault_config
        self.config = config
        self.llm_client = llm_client

        self.vault_path = vault_config.path
        self.index_path = self.vault_path / ".pageindex"
        self.toc_path = self.index_path / "toc"
        self.images_path = self.index_path / "images"
        self.attachments_path = self.index_path / "attachments"
        self.manifest_path = self.index_path / "manifest.json"

        # In-memory cache
        self._toc_cache: dict[str, NoteToC] = {}
        self._all_tocs_cache: list[NoteToC] | None = None
        self._manifest: VaultManifest | None = None

    def ensure_index_dirs(self) -> None:
        """Create index directory structure if it doesn't exist."""
        self.index_path.mkdir(exist_ok=True)
        self.toc_path.mkdir(exist_ok=True)
        self.images_path.mkdir(exist_ok=True)
        self.attachments_path.mkdir(exist_ok=True)

    def load_manifest(self) -> VaultManifest:
        """Load or create the vault manifest."""
        if self._manifest:
            return self._manifest

        if self.manifest_path.exists():
            data = json.loads(self.manifest_path.read_text())
            self._manifest = VaultManifest.model_validate(data)
        else:
            self._manifest = VaultManifest(
                vault_name=self.vault_config.name,
                vault_path=self.vault_path,
                note_count=0,
                last_indexed=datetime.now(),
            )

        return self._manifest

    def save_manifest(self) -> None:
        """Save the vault manifest."""
        if self._manifest:
            self.manifest_path.write_text(self._manifest.model_dump_json(indent=2))

    def _note_to_toc_path(self, note_path: Path) -> Path:
        """Convert a note path to its ToC storage path."""
        relative = note_path.relative_to(self.vault_path)
        safe_name = str(relative).replace("/", "__").replace("\\", "__")
        return self.toc_path / f"{safe_name}.json"

    def _should_exclude(self, path: Path) -> bool:
        """Check if a path should be excluded from indexing."""
        relative = str(path.relative_to(self.vault_path))
        return any(
            fnmatch(relative, pattern)
            for pattern in self.config.indexing.exclude_patterns
        )

    def find_notes(self) -> list[Path]:
        """Find all markdown notes in the vault."""
        notes: list[Path] = []
        for md_file in self.vault_path.rglob("*.md"):
            if not self._should_exclude(md_file):
                notes.append(md_file)
        return notes

    def get_changed_notes(self) -> tuple[list[Path], list[Path]]:
        """
        Find notes that have changed since last index.

        Returns:
            Tuple of (new/modified notes, deleted notes)
        """
        manifest = self.load_manifest()
        current_notes = self.find_notes()
        current_paths = {str(p.relative_to(self.vault_path)) for p in current_notes}
        indexed_paths = set(manifest.notes.keys())

        # Find new/modified
        changed: list[Path] = []
        for note in current_notes:
            rel_path = str(note.relative_to(self.vault_path))
            mtime = datetime.fromtimestamp(note.stat().st_mtime)

            if rel_path not in indexed_paths or manifest.notes[rel_path] < mtime:
                changed.append(note)

        # Find deleted
        deleted: list[Path] = []
        for rel_path in indexed_paths:
            if rel_path not in current_paths:
                deleted.append(self.vault_path / rel_path)

        return changed, deleted

    def load_toc(self, note_path: Path) -> NoteToC | None:
        """Load a note's ToC from cache or disk."""
        rel_path = str(note_path.relative_to(self.vault_path))

        # Check cache
        if rel_path in self._toc_cache:
            return self._toc_cache[rel_path]

        # Load from disk
        toc_path = self._note_to_toc_path(note_path)
        if toc_path.exists():
            data = json.loads(toc_path.read_text())
            toc = NoteToC.model_validate(data)
            self._toc_cache[rel_path] = toc
            return toc

        return None

    def save_toc(self, toc: NoteToC) -> None:
        """Save a note's ToC to disk and invalidate cache."""
        rel_path = str(toc.path.relative_to(self.vault_path))
        toc_path = self._note_to_toc_path(toc.path)

        toc_path.write_text(toc.model_dump_json(indent=2))
        self._toc_cache[rel_path] = toc
        self._all_tocs_cache = None  # Invalidate full list cache

    def delete_toc(self, note_path: Path) -> None:
        """Delete a note's ToC from disk and invalidate cache."""
        rel_path = str(note_path.relative_to(self.vault_path))
        toc_path = self._note_to_toc_path(note_path)

        if toc_path.exists():
            toc_path.unlink()

        self._toc_cache.pop(rel_path, None)
        self._all_tocs_cache = None  # Invalidate full list cache

    async def index_note(
        self,
        note_path: Path,
        with_summaries: bool = True,
    ) -> NoteToC:
        """
        Index a single note, generating ToC and optionally summaries.

        Args:
            note_path: Path to the note
            with_summaries: Whether to generate LLM summaries
        """
        parsed = parse_note(note_path)
        toc = build_note_toc(parsed)

        if with_summaries and self.llm_client:
            sum_ta = self.config.token_allocation.summarization
            toc = await add_summaries_to_toc(
                self.llm_client,
                toc,
                parsed.content,
                summarize_sections=self.config.indexing.summarize_sections,
                max_section_chars=sum_ta.max_section_chars,
                max_note_chars=sum_ta.max_note_chars,
                max_section_tokens=sum_ta.max_section_tokens,
                max_note_tokens=sum_ta.max_note_tokens,
            )

        self.save_toc(toc)
        return toc

    async def _process_images(
        self,
        toc: NoteToC,
        note_path: Path,
        progress_callback: Callable[..., object] | None = None,
    ) -> int:
        """Process images referenced in a note using VLM.

        Returns the number of images processed.
        """
        if not self.llm_client:
            return 0

        note_dir = note_path.parent
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

        # Collect processable images
        vault_root = self.vault_path.resolve()
        to_process: list[tuple[str, Path]] = []
        for image_ref in toc.images:
            # Resolve image path relative to note or vault
            image_path = note_dir / image_ref
            if not image_path.exists():
                image_path = self.vault_path / image_ref
            if not image_path.exists():
                image_path = self.vault_path / "attachments" / image_ref
            if not image_path.exists():
                continue

            # Validate path stays within vault (prevent traversal)
            if not image_path.resolve().is_relative_to(vault_root):
                continue

            if image_path.suffix.lower() not in image_extensions:
                continue

            # Check if already processed
            safe_name = image_ref.replace("/", "-").replace(" ", "-")
            desc_path = self.images_path / f"{safe_name}.json"
            if desc_path.exists():
                continue

            to_process.append((image_ref, image_path))

        if not to_process:
            return 0

        processed = 0
        for i, (image_ref, image_path) in enumerate(to_process, 1):
            if progress_callback:
                progress_callback(i, len(to_process), image_ref)

            safe_name = image_ref.replace("/", "-").replace(" ", "-")
            desc_path = self.images_path / f"{safe_name}.json"

            try:
                description = await describe_image(self.llm_client, image_path)
                desc_path.write_text(
                    json.dumps(description.model_dump(), indent=2, default=str)
                )
                processed += 1
            except Exception as e:
                logger.warning(
                    "[indexing] Could not process image %s: %s", image_ref, e
                )

        return processed

    async def rebuild_index(
        self,
        strategy: IndexStrategy | None = None,
        progress_callback: Callable[..., object] | None = None,
    ) -> dict[str, int]:
        """
        Rebuild the vault index.

        Args:
            strategy: Override config strategy (incremental or full)
            progress_callback: Called with (current, total, note_path)

        Returns:
            Stats dict with counts
        """
        self.ensure_index_dirs()
        strategy = strategy or self.config.indexing.strategy

        if strategy == IndexStrategy.FULL:
            # Clear existing index (ToCs and image descriptions)
            for toc_file in self.toc_path.glob("*.json"):
                toc_file.unlink()
            for img_file in self.images_path.glob("*.json"):
                img_file.unlink()
            self._toc_cache.clear()
            self._all_tocs_cache = None
            notes_to_index = self.find_notes()
            deleted: list[Path] = []
        else:
            notes_to_index, deleted = self.get_changed_notes()

        # Delete removed notes
        for note_path in deleted:
            self.delete_toc(note_path)

        # Index notes
        total = len(notes_to_index)
        for i, note_path in enumerate(notes_to_index):
            if progress_callback:
                progress_callback(i + 1, total, note_path)

            await self.index_note(
                note_path,
                with_summaries=self.config.indexing.summarize_sections,
            )

        # Update manifest (reuse find_notes result to avoid redundant rglob)
        all_notes = self.find_notes()
        manifest = self.load_manifest()
        manifest.note_count = len(all_notes)
        manifest.last_indexed = datetime.now()

        for note in all_notes:
            rel_path = str(note.relative_to(self.vault_path))
            manifest.notes[rel_path] = datetime.fromtimestamp(note.stat().st_mtime)

        for note_path in deleted:
            rel_path = str(note_path.relative_to(self.vault_path))
            manifest.notes.pop(rel_path, None)

        self.save_manifest()

        return {
            "indexed": len(notes_to_index),
            "deleted": len(deleted),
            "total": manifest.note_count,
        }

    async def process_all_images(
        self,
        progress_callback: Callable[..., object] | None = None,
    ) -> int:
        """Process all unprocessed images across all indexed notes.

        Returns the total number of images processed.
        """
        if not self.config.images.vlm_descriptions or not self.llm_client:
            return 0

        total_processed = 0
        for note_toc in self.get_all_tocs():
            if note_toc.images:
                count = await self._process_images(
                    note_toc, note_toc.path, progress_callback
                )
                total_processed += count
        return total_processed

    def get_all_tocs(self) -> list[NoteToC]:
        """Load all ToCs from the index (cached after first call)."""
        if self._all_tocs_cache is not None:
            return self._all_tocs_cache
        tocs: list[NoteToC] = []
        for toc_file in self.toc_path.glob("*.json"):
            data = json.loads(toc_file.read_text())
            tocs.append(NoteToC.model_validate(data))
        self._all_tocs_cache = tocs
        return tocs

    def get_note_summaries(self) -> list[tuple[str, str]]:
        """Get (title, summary) pairs for all indexed notes."""
        summaries: list[tuple[str, str]] = []
        for toc in self.get_all_tocs():
            if toc.summary:
                summaries.append((toc.title, toc.summary))
        return summaries

    def build_wikilink_graph(self) -> dict[str, list[str]]:
        """
        Build a graph of wikilink connections.

        Returns dict mapping note paths to list of linked note paths.
        """
        tocs = self.get_all_tocs()

        # Build lookup for link resolution
        note_names: dict[str, Path] = {}
        note_aliases: dict[str, list[str]] = {}

        for toc in tocs:
            rel_path = str(toc.path.relative_to(self.vault_path))
            note_names[toc.path.stem] = toc.path
            note_names[rel_path] = toc.path
            note_aliases[rel_path] = toc.aliases

        # Build graph
        graph: dict[str, list[str]] = {}
        for toc in tocs:
            rel_path = str(toc.path.relative_to(self.vault_path))
            linked: list[str] = []

            for link in toc.wikilinks:
                resolved = resolve_wikilink(
                    link,
                    note_names,
                    self.config.indexing.wikilink_resolution.value,
                    note_aliases,
                )
                if resolved:
                    linked.append(str(resolved.relative_to(self.vault_path)))

            graph[rel_path] = linked

        return graph

    def get_stats(self) -> dict[str, object]:
        """Get index statistics."""
        manifest = self.load_manifest()
        tocs = self.get_all_tocs()

        total_sections = sum(
            len(toc.sections) + sum(len(s.flatten()) - 1 for s in toc.sections)
            for toc in tocs
        )
        total_links = sum(len(toc.wikilinks) for toc in tocs)
        total_images = sum(len(toc.images) for toc in tocs)

        return {
            "vault_name": manifest.vault_name,
            "vault_path": str(manifest.vault_path),
            "note_count": manifest.note_count,
            "last_indexed": manifest.last_indexed.isoformat(),
            "total_sections": total_sections,
            "total_wikilinks": total_links,
            "total_images": total_images,
        }


class VaultManager:
    """
    Manages multiple vaults (knowledge + memory).

    Provides unified interface for querying across vaults.
    """

    def __init__(
        self,
        config: Config,
        llm_client: LLMClient | None = None,
        project_names: list[str] | None = None,
    ):
        self.config = config
        self.llm_client = llm_client

        # Initialize knowledge vaults from specified projects (or default)
        self.knowledge_vaults: dict[str, VaultIndex] = {}
        if project_names:
            for name in project_names:
                project = config.get_project(name)
                if project:
                    for vault_config in project.get_active_vaults():
                        self.knowledge_vaults[vault_config.name] = VaultIndex(
                            vault_config, config, llm_client
                        )
        else:
            for vault_config in config.get_default_project_vaults():
                self.knowledge_vaults[vault_config.name] = VaultIndex(
                    vault_config, config, llm_client
                )

        # Initialize memory vault
        memory_config = VaultConfig(
            name="memory",
            path=config.memory.path,
            active=True,
        )
        self.memory = VaultIndex(memory_config, config, llm_client)

    def get_active_vaults(self) -> list[VaultIndex]:
        """Get list of active knowledge vaults."""
        return [
            vault
            for name, vault in self.knowledge_vaults.items()
            if vault.vault_config.active
        ]

    def get_vault(self, name: str) -> VaultIndex | None:
        """Get a specific vault by name."""
        if name == "memory":
            return self.memory
        return self.knowledge_vaults.get(name)

    def list_vaults(self) -> list[dict[str, object]]:
        """List all vaults with status."""
        vaults: list[dict[str, object]] = []

        for name, vault in self.knowledge_vaults.items():
            vaults.append(
                {
                    "name": name,
                    "path": str(vault.vault_path),
                    "active": vault.vault_config.active,
                    "type": "knowledge",
                }
            )

        vaults.append(
            {
                "name": "memory",
                "path": str(self.memory.vault_path),
                "active": True,
                "type": "memory",
            }
        )

        return vaults

    def search_notes(
        self,
        query: str,
        scope: str = "all",  # "all", "knowledge", "memory", or vault name
    ) -> list[NoteToC]:
        """
        Search for notes matching a query.

        This is a simple text search; LLM-based routing is in retrieval module.
        """
        results: list[NoteToC] = []
        query_lower = query.lower()

        vaults_to_search: list[VaultIndex] = []

        if scope == "all":
            vaults_to_search = self.get_active_vaults() + [self.memory]
        elif scope == "knowledge":
            vaults_to_search = self.get_active_vaults()
        elif scope == "memory":
            vaults_to_search = [self.memory]
        else:
            vault = self.get_vault(scope)
            if vault:
                vaults_to_search = [vault]

        for vault in vaults_to_search:
            for toc in vault.get_all_tocs():
                # Search in title, summary, and headings
                searchable = [toc.title.lower()]
                if toc.summary:
                    searchable.append(toc.summary.lower())
                for section in toc.sections:
                    for node in section.flatten():
                        searchable.append(node.heading.lower())
                        if node.summary:
                            searchable.append(node.summary.lower())

                if any(query_lower in text for text in searchable):
                    results.append(toc)

        return results
