"""
Tests for contradiction detection, cataloging, and resolution.

Covers the full contradiction lifecycle: save, load, resolve,
check against existing memory, and relationship inclusion.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from archivyst.agent.memory import MemoryStore
from archivyst.core.models import (
    Config,
    GlobalAccess,
    LLMConfig,
    MemoryConfig,
    ProjectConfig,
    ProjectMemoryConfig,
    VaultConfig,
)


@pytest.fixture
def memory_store(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore with project memory structure."""
    project_path = tmp_path / "projects" / "test"
    project_path.mkdir(parents=True)

    # Create required directories
    for d in [
        "user",
        "semantic/entities/people",
        "semantic/entities/projects",
        "semantic/entities/concepts",
        "semantic/facts",
        "procedural/patterns",
        "procedural/corrections",
        "episodic/conversations",
        "episodic/events",
        "working/sessions",
    ]:
        (project_path / d).mkdir(parents=True, exist_ok=True)

    config = Config(
        memory=MemoryConfig(path=tmp_path),
        llm=LLMConfig(),
        projects=[
            ProjectConfig(
                name="test",
                vaults=[VaultConfig(name="v", path=tmp_path / "vault")],
                memory=ProjectMemoryConfig(global_access=GlobalAccess.NONE),
            )
        ],
    )
    scope = config.build_project_scope("test")
    return MemoryStore(config, scope)


# ---------------------------------------------------------------------------
# Save contradiction
# ---------------------------------------------------------------------------


class TestSaveContradiction:
    """Tests for saving contradictions to contradictions.md."""

    def test_creates_file_if_missing(self, memory_store: MemoryStore) -> None:
        c_id = memory_store.save_contradiction(
            scope="semantic",
            source_a_path="semantic/facts/team.md",
            source_a_text="Alice leads Project X",
            source_b_path="(new extraction)",
            source_b_text="Bob leads Project X",
            conflict_type="factual",
            context="session test",
        )

        content = memory_store.read("semantic/contradictions.md")
        assert content is not None
        assert "## Unresolved" in content
        assert c_id in content
        assert "Alice leads Project X" in content
        assert "Bob leads Project X" in content

    def test_returns_valid_id(self, memory_store: MemoryStore) -> None:
        c_id = memory_store.save_contradiction(
            scope="semantic",
            source_a_path="a.md",
            source_a_text="old",
            source_b_path="b.md",
            source_b_text="new",
            conflict_type="factual",
        )

        # ID format: C-YYYY-MM-DD-NNN
        assert c_id.startswith("C-")
        parts = c_id.split("-")
        assert len(parts) == 5
        assert parts[-1].isdigit()

    def test_sequential_ids(self, memory_store: MemoryStore) -> None:
        id1 = memory_store.save_contradiction(
            scope="semantic",
            source_a_path="a.md",
            source_a_text="x",
            source_b_path="b.md",
            source_b_text="y",
            conflict_type="factual",
        )
        id2 = memory_store.save_contradiction(
            scope="semantic",
            source_a_path="c.md",
            source_a_text="p",
            source_b_path="d.md",
            source_b_text="q",
            conflict_type="factual",
        )

        # Second ID should have higher sequence number
        seq1 = int(id1.split("-")[-1])
        seq2 = int(id2.split("-")[-1])
        assert seq2 == seq1 + 1

    def test_saves_to_correct_scope(self, memory_store: MemoryStore) -> None:
        memory_store.save_contradiction(
            scope="procedural",
            source_a_path="procedural/patterns/a.md",
            source_a_text="old pattern",
            source_b_path="(new)",
            source_b_text="new pattern",
            conflict_type="pattern",
        )

        # Should be in procedural, not semantic
        assert memory_store.read("procedural/contradictions.md") is not None
        assert memory_store.read("semantic/contradictions.md") is None


# ---------------------------------------------------------------------------
# Load contradictions
# ---------------------------------------------------------------------------


class TestLoadContradictions:
    """Tests for loading contradictions from contradictions.md."""

    def test_load_unresolved_only(self, memory_store: MemoryStore) -> None:
        memory_store.save_contradiction(
            scope="semantic",
            source_a_path="a.md",
            source_a_text="old",
            source_b_path="b.md",
            source_b_text="new",
            conflict_type="factual",
        )

        results = memory_store.load_contradictions(unresolved_only=True)
        assert len(results) == 1
        assert results[0]["type"] == "factual"

    def test_load_empty(self, memory_store: MemoryStore) -> None:
        results = memory_store.load_contradictions()
        assert results == []

    def test_load_from_specific_scope(self, memory_store: MemoryStore) -> None:
        memory_store.save_contradiction(
            scope="semantic",
            source_a_path="a.md",
            source_a_text="x",
            source_b_path="b.md",
            source_b_text="y",
            conflict_type="factual",
        )
        memory_store.save_contradiction(
            scope="procedural",
            source_a_path="c.md",
            source_a_text="p",
            source_b_path="d.md",
            source_b_text="q",
            conflict_type="pattern",
        )

        # Should only return semantic ones
        results = memory_store.load_contradictions(scope="semantic")
        assert len(results) == 1
        assert results[0]["type"] == "factual"


# ---------------------------------------------------------------------------
# Resolve contradiction
# ---------------------------------------------------------------------------


class TestResolveContradiction:
    """Tests for resolving contradictions."""

    def test_moves_to_resolved(self, memory_store: MemoryStore) -> None:
        c_id = memory_store.save_contradiction(
            scope="semantic",
            source_a_path="a.md",
            source_a_text="old",
            source_b_path="b.md",
            source_b_text="new",
            conflict_type="factual",
        )

        result = memory_store.resolve_contradiction(
            scope="semantic",
            contradiction_id=c_id,
            resolution="Kept source A",
            resolved_by="user",
        )
        assert result is True

        content = memory_store.read("semantic/contradictions.md")
        assert content is not None
        assert "## Resolved" in content
        assert "Kept source A" in content
        assert "Resolved by**: user" in content

    def test_unresolved_decreases(self, memory_store: MemoryStore) -> None:
        c_id = memory_store.save_contradiction(
            scope="semantic",
            source_a_path="a.md",
            source_a_text="old",
            source_b_path="b.md",
            source_b_text="new",
            conflict_type="factual",
        )

        # Before resolve
        assert len(memory_store.load_contradictions(unresolved_only=True)) == 1

        memory_store.resolve_contradiction(
            scope="semantic",
            contradiction_id=c_id,
            resolution="Fixed",
        )

        # After resolve — should be empty
        assert len(memory_store.load_contradictions(unresolved_only=True)) == 0

    def test_resolve_nonexistent_returns_false(self, memory_store: MemoryStore) -> None:
        result = memory_store.resolve_contradiction(
            scope="semantic",
            contradiction_id="C-2099-01-01-999",
            resolution="n/a",
        )
        assert result is False


# ---------------------------------------------------------------------------
# Check contradictions (with LLM mock)
# ---------------------------------------------------------------------------


class TestCheckContradictions:
    """Tests for check_contradictions() in extraction.py."""

    @pytest.mark.asyncio
    async def test_finds_conflict(self, memory_store: MemoryStore) -> None:
        from archivyst.agent.extraction import check_contradictions

        # Set up existing memory
        memory_store.save_fact("team-lead", "Alice leads Project X")

        # Mock LLM that returns a contradiction
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(
            return_value='[{"new_text": "Bob leads Project X", '
            '"existing_source": "semantic/facts/team-lead.md", '
            '"existing_text": "Alice leads Project X", '
            '"type": "factual", '
            '"explanation": "Leadership conflict"}]'
        )

        ids = await check_contradictions(
            memory_store,
            "Bob leads Project X now",
            mock_llm,
            context="test",
        )

        assert len(ids) == 1
        assert ids[0].startswith("C-")

        # Verify it was saved
        contradictions = memory_store.load_contradictions()
        assert len(contradictions) == 1

    @pytest.mark.asyncio
    async def test_no_conflict(self, memory_store: MemoryStore) -> None:
        from archivyst.agent.extraction import check_contradictions

        memory_store.save_fact("team-lead", "Alice leads Project X")

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value="[]")

        ids = await check_contradictions(
            memory_store,
            "Alice is great at leading",
            mock_llm,
        )

        assert len(ids) == 0

    @pytest.mark.asyncio
    async def test_includes_relationships(self, memory_store: MemoryStore) -> None:
        from archivyst.agent.extraction import check_contradictions

        # Write a relationship
        memory_store.write(
            "semantic/relationships.md",
            "# Entity Relationships\n- [[alice]] leads [[project-x]]\n",
        )

        # Write a fact that shares keywords
        memory_store.save_fact("project-x", "Project X is active")

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value="[]")

        await check_contradictions(
            memory_store,
            "Project X leadership update",
            mock_llm,
        )

        # Verify relationships.md was included in the prompt
        call_args = mock_llm.complete.call_args
        prompt = call_args.kwargs.get(
            "prompt", call_args.args[0] if call_args.args else ""
        )
        assert "relationships.md" in prompt or "alice" in prompt.lower()


# ---------------------------------------------------------------------------
# Get related entries
# ---------------------------------------------------------------------------


class TestGetRelatedEntries:
    """Tests for memory.get_related_entries()."""

    def test_caps_at_10_results(self, memory_store: MemoryStore) -> None:
        # Create 15 facts with overlapping keywords
        for i in range(15):
            memory_store.save_fact(
                f"fact-{i}",
                f"Python programming language fact number {i} about coding",
            )

        related = memory_store.get_related_entries("Python programming language coding")
        assert len(related) <= 10

    def test_returns_empty_for_no_matches(self, memory_store: MemoryStore) -> None:
        memory_store.save_fact("unrelated", "The sky is blue today")

        related = memory_store.get_related_entries(
            "quantum chromodynamics fermion interaction"
        )
        assert len(related) == 0

    def test_includes_relationships_when_matched(
        self, memory_store: MemoryStore
    ) -> None:
        memory_store.write(
            "semantic/relationships.md",
            "# Relationships\n- [[alice]] works with [[bob]]\n",
        )

        related = memory_store.get_related_entries("alice works with bob today")
        paths = [path for path, _ in related]
        assert "semantic/relationships.md" in paths
