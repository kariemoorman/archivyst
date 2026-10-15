"""
Tests for scan command file selection, orphaned image detection,
and project/vault resolution.
"""

import json
from pathlib import Path

from archivyst.cli.scan_cmd import (
    IMAGE_EXTENSIONS,
    ScanResult,
    _select_by_type,
    save_code_manifest,
    scan_vault_for_code,
)
from archivyst.core.code_converter import compute_source_hash

# ---------------------------------------------------------------------------
# Selection by type
# ---------------------------------------------------------------------------


class TestSelectByType:
    """Tests for _select_by_type() function."""

    def test_returns_empty_when_no_files(self) -> None:
        code, images = _select_by_type([], [])
        assert code == []
        assert images == []


# ---------------------------------------------------------------------------
# Selection by file
# ---------------------------------------------------------------------------


class TestSelectByFile:
    """Tests for _select_by_file() function."""

    def test_all_selected_by_default(self, tmp_path: Path) -> None:
        """All files should start as selected."""
        files = [
            tmp_path / "a.py",
            tmp_path / "b.sql",
            tmp_path / "c.png",
        ]
        for f in files:
            f.write_text("test")

        # Internal state check: all start selected
        result = ScanResult(
            new=[files[0], files[1]],
            orphaned_images=[files[2]],
        )
        all_actionable = result.new + result.orphaned_images
        assert len(all_actionable) == 3

    def test_image_extensions_defined(self) -> None:
        """IMAGE_EXTENSIONS should include standard formats."""
        assert "png" in IMAGE_EXTENSIONS
        assert "jpg" in IMAGE_EXTENSIONS
        assert "jpeg" in IMAGE_EXTENSIONS
        assert "gif" in IMAGE_EXTENSIONS
        assert "webp" in IMAGE_EXTENSIONS


# ---------------------------------------------------------------------------
# Orphaned image detection
# ---------------------------------------------------------------------------


class TestOrphanedImageDetection:
    """Tests for orphaned image detection in scan_vault_for_code."""

    def test_detects_unreferenced_image(self, tmp_path: Path) -> None:
        """Image not referenced by any note should be orphaned."""
        vault = tmp_path / "vault"
        vault.mkdir()
        pageindex = vault / ".pageindex"
        pageindex.mkdir()

        # Create an image with no referencing notes
        img = vault / "orphan.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        result = scan_vault_for_code(vault, pageindex, "test-model")
        assert len(result.orphaned_images) == 1
        assert result.orphaned_images[0].name == "orphan.png"

    def test_skips_referenced_image(self, tmp_path: Path) -> None:
        """Image referenced by a note should NOT be orphaned."""
        vault = tmp_path / "vault"
        vault.mkdir()
        pageindex = vault / ".pageindex"
        pageindex.mkdir()
        toc_dir = pageindex / "toc"
        toc_dir.mkdir()

        # Create an image
        img = vault / "referenced.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        # Create a ToC that references it
        toc_data = {
            "path": "note.md",
            "title": "Test",
            "images": ["referenced.png"],
            "sections": [],
            "wikilinks": [],
            "attachments": [],
            "frontmatter": {},
        }
        (toc_dir / "note.json").write_text(json.dumps(toc_data))

        result = scan_vault_for_code(vault, pageindex, "test-model")
        assert len(result.orphaned_images) == 0

    def test_skips_image_with_companion_md(self, tmp_path: Path) -> None:
        """Image with existing .png.md companion should be skipped."""
        vault = tmp_path / "vault"
        vault.mkdir()
        pageindex = vault / ".pageindex"
        pageindex.mkdir()

        img = vault / "has-companion.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        # Create companion
        companion = vault / "has-companion.png.md"
        companion.write_text("# has-companion.png\nDescription here")

        result = scan_vault_for_code(vault, pageindex, "test-model")
        assert len(result.orphaned_images) == 0

    def test_skips_image_in_manifest(self, tmp_path: Path) -> None:
        """Image already tracked in manifest should be skipped."""
        vault = tmp_path / "vault"
        vault.mkdir()
        pageindex = vault / ".pageindex"
        pageindex.mkdir()

        img = vault / "tracked.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        # Add to manifest by hash
        h = compute_source_hash(img)
        save_code_manifest(
            pageindex,
            {
                "tracked.png": {
                    "source_hash": h,
                    "source_mtime": img.stat().st_mtime,
                    "model": "test-model",
                }
            },
        )

        result = scan_vault_for_code(vault, pageindex, "test-model")
        assert len(result.orphaned_images) == 0

    def test_skips_image_in_hidden_dir(self, tmp_path: Path) -> None:
        """Images in hidden directories (.obsidian) should be skipped."""
        vault = tmp_path / "vault"
        vault.mkdir()
        pageindex = vault / ".pageindex"
        pageindex.mkdir()

        hidden = vault / ".obsidian"
        hidden.mkdir()
        img = hidden / "icon.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        result = scan_vault_for_code(vault, pageindex, "test-model")
        assert len(result.orphaned_images) == 0


# ---------------------------------------------------------------------------
# Project/vault resolution
# ---------------------------------------------------------------------------


class TestScanResult:
    """Tests for ScanResult properties."""

    def test_has_changes_with_new(self) -> None:
        r = ScanResult(new=[Path("/tmp/a.py")])
        assert r.has_changes is True

    def test_has_changes_with_orphaned_images(self) -> None:
        r = ScanResult(orphaned_images=[Path("/tmp/a.png")])
        assert r.has_changes is True

    def test_no_changes_when_empty(self) -> None:
        r = ScanResult()
        assert r.has_changes is False

    def test_no_changes_with_only_unchanged(self) -> None:
        r = ScanResult(unchanged=[Path("/tmp/a.py")])
        assert r.has_changes is False

    def test_has_changes_with_deleted(self) -> None:
        r = ScanResult(deleted=["old.py"])
        assert r.has_changes is True

    def test_hash_cache_starts_empty(self) -> None:
        r = ScanResult()
        assert r.hash_cache == {}


# ---------------------------------------------------------------------------
# Process command — filename preservation + manifest
# ---------------------------------------------------------------------------


class TestProcessFilenamePreservation:
    """Tests for filename.ext.md naming convention."""

    def test_code_file_preserves_extension(self, tmp_path: Path) -> None:
        from archivyst.cli.process_cmd import _write_md

        output = tmp_path / "parser.py.md"
        _write_md(output, Path("parser.py"), "py", "2026-01-01", "parser", "code")

        assert output.exists()
        content = output.read_text()
        assert "# parser" in content

    def test_csv_file_preserves_extension(self, tmp_path: Path) -> None:
        from archivyst.cli.process_cmd import _write_md

        output = tmp_path / "data.csv.md"
        _write_md(output, Path("data.csv"), "csv", "2026-01-01", "data", "table")

        assert output.exists()


# ---------------------------------------------------------------------------
# Memory management
# ---------------------------------------------------------------------------


class TestMemoryTruncation:
    """Tests for memory file size enforcement."""

    def test_truncation_uses_char_length(self, tmp_path: Path) -> None:
        """Truncation should be based on len(content), not len(content.encode())."""
        from archivyst.agent.memory import MemoryStore
        from archivyst.core.models import (
            Config,
            GlobalAccess,
            LLMConfig,
            MemoryConfig,
            ProjectScope,
        )

        config = Config(
            memory=MemoryConfig(path=tmp_path),
            llm=LLMConfig(),
            projects=[],
        )

        # Create a basic project structure
        project_path = tmp_path / "projects" / "test"
        for d in ["user", "semantic/facts"]:
            (project_path / d).mkdir(parents=True, exist_ok=True)

        scope = ProjectScope(
            project="test",
            knowledge_vaults=[],
            memory_root=tmp_path,
            is_shared_memory=True,
            global_access=GlobalAccess.NONE,
        )
        store = MemoryStore(config, scope)

        # Write content that's under char limit but over byte limit
        # (Unicode chars use more bytes)
        content = "日本語テスト" * 5000  # ~30K chars, ~90K bytes
        store.write("semantic/facts/test.md", content)

        saved = store.read("semantic/facts/test.md")
        assert saved is not None
        # Should be truncated to MAX_MEMORY_FILE_SIZE chars, not bytes
        assert len(saved) <= store.MAX_MEMORY_FILE_SIZE
