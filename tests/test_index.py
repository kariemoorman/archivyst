"""
Tests for vault index functionality.
"""

from datetime import datetime
from pathlib import Path

import pytest

from archivyst.core.index import VaultIndex, VaultManager
from archivyst.core.models import Config


class TestVaultIndex:
    """Tests for VaultIndex class."""

    def test_creates_index_directories(self, test_config: Config, sample_vault: Path):
        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)

        index.ensure_index_dirs()

        assert (sample_vault / ".pageindex").exists()
        assert (sample_vault / ".pageindex" / "toc").exists()
        assert (sample_vault / ".pageindex" / "images").exists()
        assert (sample_vault / ".pageindex" / "attachments").exists()

    def test_finds_markdown_notes(self, test_config: Config, sample_vault: Path):
        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)

        notes = index.find_notes()

        # Should find: index.md, project-alpha.md, meeting-notes.md, archive/old-note.md
        assert len(notes) == 4
        note_names = {n.name for n in notes}
        assert "index.md" in note_names
        assert "project-alpha.md" in note_names
        assert "old-note.md" in note_names

    def test_excludes_patterns(self, test_config: Config, sample_vault: Path):
        # Add an excalidraw file that should be excluded
        (sample_vault / "drawing.excalidraw.md").write_text("excalidraw data")

        # Add .obsidian directory
        obsidian_dir = sample_vault / ".obsidian"
        obsidian_dir.mkdir()
        (obsidian_dir / "config.md").write_text("config")

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)

        notes = index.find_notes()
        note_names = {n.name for n in notes}

        assert "drawing.excalidraw.md" not in note_names
        assert "config.md" not in note_names

    def test_manifest_lifecycle(self, test_config: Config, sample_vault: Path):
        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # Load creates new manifest if missing
        manifest = index.load_manifest()
        assert manifest.vault_name == "test-vault"
        assert manifest.note_count == 0

        # Modify and save
        manifest.note_count = 5
        manifest.notes["test.md"] = datetime.now()
        index.save_manifest()

        # Reload and verify
        index._manifest = None  # Clear cache
        manifest = index.load_manifest()
        assert manifest.note_count == 5
        assert "test.md" in manifest.notes

    def test_toc_save_and_load(self, test_config: Config, sample_vault: Path):
        from archivyst.core.markdown import build_note_toc, parse_note

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # Parse and build ToC for a note
        note_path = sample_vault / "project-alpha.md"
        parsed = parse_note(note_path)
        toc = build_note_toc(parsed)

        # Save
        index.save_toc(toc)

        # Clear cache and reload
        index._toc_cache.clear()
        loaded = index.load_toc(note_path)

        assert loaded is not None
        assert loaded.title == toc.title
        assert len(loaded.sections) == len(toc.sections)
        assert loaded.wikilinks == toc.wikilinks

    def test_detects_changed_notes(self, test_config: Config, sample_vault: Path):
        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # First call - all notes are new
        changed, deleted = index.get_changed_notes()
        assert len(changed) == 4  # All notes
        assert len(deleted) == 0

        # Update manifest to mark notes as indexed
        manifest = index.load_manifest()
        for note in index.find_notes():
            rel_path = str(note.relative_to(sample_vault))
            manifest.notes[rel_path] = datetime.fromtimestamp(note.stat().st_mtime)
        index.save_manifest()

        # No changes
        changed, deleted = index.get_changed_notes()
        assert len(changed) == 0
        assert len(deleted) == 0

    def test_builds_wikilink_graph(self, test_config: Config, sample_vault: Path):
        from archivyst.core.markdown import build_note_toc, parse_note

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # Index all notes
        for note_path in index.find_notes():
            parsed = parse_note(note_path)
            toc = build_note_toc(parsed)
            index.save_toc(toc)

        # Build graph
        graph = index.build_wikilink_graph()

        # index.md links to project-alpha and meeting-notes
        assert "index.md" in graph
        # project-alpha.md links to meeting-notes
        assert "project-alpha.md" in graph

    def test_get_stats(self, test_config: Config, sample_vault: Path):
        from archivyst.core.markdown import build_note_toc, parse_note

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # Index all notes
        for note_path in index.find_notes():
            parsed = parse_note(note_path)
            toc = build_note_toc(parsed)
            index.save_toc(toc)

        # Update manifest
        manifest = index.load_manifest()
        manifest.note_count = len(index.find_notes())
        index.save_manifest()

        stats = index.get_stats()

        assert stats["vault_name"] == "test-vault"
        assert stats["note_count"] == 4
        assert stats["total_sections"] > 0
        assert stats["total_wikilinks"] > 0


class TestVaultManager:
    """Tests for VaultManager class."""

    def test_initializes_vaults(self, test_config: Config):
        manager = VaultManager(test_config)

        assert "test-vault" in manager.knowledge_vaults
        assert manager.memory is not None

    def test_get_active_vaults(self, test_config: Config):
        manager = VaultManager(test_config)

        active = manager.get_active_vaults()

        assert len(active) == 1
        assert active[0].vault_config.name == "test-vault"

    def test_get_vault_by_name(self, test_config: Config):
        manager = VaultManager(test_config)

        vault = manager.get_vault("test-vault")
        assert vault is not None
        assert vault.vault_config.name == "test-vault"

        memory = manager.get_vault("memory")
        assert memory is not None

        missing = manager.get_vault("nonexistent")
        assert missing is None

    def test_list_vaults(self, test_config: Config):
        manager = VaultManager(test_config)

        vaults = manager.list_vaults()

        assert len(vaults) == 2  # test-vault + memory

        names = {v["name"] for v in vaults}
        assert "test-vault" in names
        assert "memory" in names

    def test_search_notes(self, test_config: Config, sample_vault: Path):
        from archivyst.core.markdown import build_note_toc, parse_note

        manager = VaultManager(test_config)

        # Index notes first
        vault = manager.get_vault("test-vault")
        vault.ensure_index_dirs()

        for note_path in vault.find_notes():
            parsed = parse_note(note_path)
            toc = build_note_toc(parsed)
            toc.summary = f"Summary for {toc.title}"
            vault.save_toc(toc)

        # Search
        results = manager.search_notes("Alpha")

        assert len(results) > 0
        titles = {r.title for r in results}
        assert "Project Alpha" in titles


@pytest.mark.asyncio
class TestAsyncIndexing:
    """Tests for async indexing operations."""

    async def test_index_note_without_summaries(
        self,
        test_config: Config,
        sample_vault: Path,
    ):
        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config, llm_client=None)
        index.ensure_index_dirs()

        note_path = sample_vault / "project-alpha.md"
        toc = await index.index_note(note_path, with_summaries=False)

        assert toc.title == "Project Alpha"
        assert toc.summary is None  # No LLM, no summary
        assert len(toc.sections) > 0

    async def test_rebuild_index_incremental(
        self,
        test_config: Config,
        sample_vault: Path,
    ):
        from archivyst.core.models import IndexStrategy

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config, llm_client=None)

        progress_calls = []

        def track_progress(current, total, path):
            progress_calls.append((current, total, path))

        stats = await index.rebuild_index(
            strategy=IndexStrategy.FULL,
            progress_callback=track_progress,
        )

        assert stats["indexed"] == 4
        assert stats["total"] == 4
        assert len(progress_calls) == 4

    async def test_rebuild_detects_deletions(
        self,
        test_config: Config,
        sample_vault: Path,
    ):
        from archivyst.core.models import IndexStrategy

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config, llm_client=None)

        # Initial index
        await index.rebuild_index(strategy=IndexStrategy.FULL)

        # Delete a note
        (sample_vault / "archive" / "old-note.md").unlink()

        # Incremental rebuild
        stats = await index.rebuild_index(strategy=IndexStrategy.INCREMENTAL)

        assert stats["deleted"] == 1
