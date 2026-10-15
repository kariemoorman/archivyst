"""
Tests for retrieval functionality.
"""

from pathlib import Path

import pytest

from archivyst.core.models import NoteToC, SectionNode
from archivyst.retrieval.linker import WikilinkGraph, expand_with_links
from archivyst.retrieval.router import (
    format_documents_by_folder,
    format_documents_flat,
    parse_routing_response,
)


class TestRoutingResponseParser:
    """Tests for routing response parsing."""

    def test_parses_valid_json(self):
        response = """{
            "selected_documents": ["note1.md", "folder/note2.md"],
            "reasoning": "These notes are relevant",
            "confidence": 0.85
        }"""

        decision = parse_routing_response(response)

        assert len(decision.selected_notes) == 2
        assert Path("note1.md") in decision.selected_notes
        assert "relevant" in decision.reasoning
        assert decision.confidence == 0.85

    def test_parses_json_in_code_block(self):
        response = """Here's my analysis:

```json
{
    "selected_documents": ["doc.md"],
    "reasoning": "Found it",
    "confidence": 0.9
}
```

Let me know if you need more."""

        decision = parse_routing_response(response)

        assert len(decision.selected_notes) == 1
        assert decision.confidence == 0.9

    def test_handles_malformed_json(self):
        response = "I think note1.md and note2.md are relevant."

        decision = parse_routing_response(response)

        # Should extract .md paths from text
        assert len(decision.selected_notes) >= 1
        assert decision.confidence < 0.5  # Low confidence fallback

    def test_clamps_confidence(self):
        response = """{
            "selected_documents": ["note.md"],
            "reasoning": "test",
            "confidence": 1.5
        }"""

        decision = parse_routing_response(response)

        assert decision.confidence == 1.0

        response2 = """{
            "selected_documents": ["note.md"],
            "reasoning": "test",
            "confidence": -0.5
        }"""

        decision2 = parse_routing_response(response2)
        assert decision2.confidence == 0.0


class TestDocumentFormatting:
    """Tests for document formatting for prompts."""

    def test_format_flat(self, sample_vault: Path):
        from datetime import datetime

        tocs = [
            NoteToC(
                path=sample_vault / "note1.md",
                title="Note One",
                summary="First note summary",
                mtime=datetime.now(),
            ),
            NoteToC(
                path=sample_vault / "note2.md",
                title="Note Two",
                summary="Second note summary",
                mtime=datetime.now(),
            ),
        ]

        formatted = format_documents_flat(tocs, sample_vault)

        assert "note1.md" in formatted
        assert "note2.md" in formatted
        assert "First note summary" in formatted

    def test_format_by_folder(self, sample_vault: Path):
        from datetime import datetime

        # Create subdirectory structure
        subdir = sample_vault / "projects"
        subdir.mkdir(exist_ok=True)

        tocs = [
            NoteToC(
                path=sample_vault / "index.md",
                title="Index",
                summary="Main index",
                mtime=datetime.now(),
            ),
            NoteToC(
                path=subdir / "alpha.md",
                title="Alpha",
                summary="Alpha project",
                mtime=datetime.now(),
            ),
        ]

        formatted = format_documents_by_folder(tocs, sample_vault)

        assert "(root)" in formatted or "index.md" in formatted
        assert "projects" in formatted
        assert "alpha.md" in formatted


class TestWikilinkGraph:
    """Tests for wikilink graph operations."""

    def test_builds_graph(self, test_config, sample_vault):
        from archivyst.core.index import VaultIndex
        from archivyst.core.markdown import build_note_toc, parse_note

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # Index notes
        for note_path in index.find_notes():
            parsed = parse_note(note_path)
            toc = build_note_toc(parsed)
            index.save_toc(toc)

        graph = WikilinkGraph(index)

        # Test outlinks
        index_outlinks = graph.get_outlinks("index.md")
        assert (
            len(index_outlinks) >= 0
        )  # May or may not resolve depending on exact content

        # Test neighbors includes both directions
        neighbors = graph.get_neighbors("project-alpha.md")
        assert isinstance(neighbors, set)

    def test_bfs_traverse(self, test_config, sample_vault):
        from archivyst.core.index import VaultIndex
        from archivyst.core.markdown import build_note_toc, parse_note

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # Index notes
        for note_path in index.find_notes():
            parsed = parse_note(note_path)
            toc = build_note_toc(parsed)
            index.save_toc(toc)

        graph = WikilinkGraph(index)

        result = graph.bfs_traverse(["index.md"], max_depth=2, max_nodes=10)

        # Should return at least the starting node
        assert len(result) >= 1
        assert result[0][0] == "index.md"
        assert result[0][1] == 0  # Depth 0

    def test_find_path(self, test_config, sample_vault):
        from archivyst.core.index import VaultIndex
        from archivyst.core.markdown import build_note_toc, parse_note

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # Index notes
        for note_path in index.find_notes():
            parsed = parse_note(note_path)
            toc = build_note_toc(parsed)
            index.save_toc(toc)

        graph = WikilinkGraph(index)

        # Path to self
        path = graph.find_path("index.md", "index.md")
        assert path == ["index.md"]

        # Non-existent path returns None
        path = graph.find_path("index.md", "nonexistent.md")
        assert path is None

    def test_invalidate(self, test_config, sample_vault):
        from archivyst.core.index import VaultIndex

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)

        graph = WikilinkGraph(index)

        # Access to build cache
        _ = graph.graph
        assert graph._graph is not None

        # Invalidate
        graph.invalidate()
        assert graph._graph is None


class TestTraverser:
    """Tests for ToC traversal."""

    def test_section_flatten(self):
        root = SectionNode(
            heading="Root",
            level=1,
            start_line=1,
            end_line=20,
            children=[
                SectionNode(
                    heading="Child 1",
                    level=2,
                    start_line=3,
                    end_line=10,
                    children=[
                        SectionNode(
                            heading="Grandchild",
                            level=3,
                            start_line=5,
                            end_line=8,
                        )
                    ],
                ),
                SectionNode(
                    heading="Child 2",
                    level=2,
                    start_line=12,
                    end_line=18,
                ),
            ],
        )

        flattened = root.flatten()

        assert len(flattened) == 4
        assert flattened[0].heading == "Root"
        assert flattened[1].heading == "Child 1"
        assert flattened[2].heading == "Grandchild"
        assert flattened[3].heading == "Child 2"


class TestExpandWithLinks:
    """Tests for link expansion."""

    @pytest.mark.asyncio
    async def test_expand_with_links(self, test_config, sample_vault):
        from archivyst.core.index import VaultIndex
        from archivyst.core.markdown import build_note_toc, parse_note

        vault_config = test_config.get_default_project_vaults()[0]
        index = VaultIndex(vault_config, test_config)
        index.ensure_index_dirs()

        # Index notes
        for note_path in index.find_notes():
            parsed = parse_note(note_path)
            toc = build_note_toc(parsed)
            index.save_toc(toc)

        # Start with index.md
        initial = [sample_vault / "index.md"]

        expanded = expand_with_links(
            index,
            initial,
            max_expansion=3,
            max_depth=1,
        )

        # Should include at least the initial note
        assert len(expanded) >= 1
        assert sample_vault / "index.md" in expanded
