"""
Tests for token allocation configuration and response mode.

Covers TokenAllocationConfig defaults, config loading,
response mode selection, and prompt switching.
"""

from pathlib import Path

from archivyst.core.models import (
    Config,
    LLMConfig,
    MemoryConfig,
    ProjectConfig,
    RetrievalTokenEntry,
    SummarizationTokenEntry,
    TokenAllocationConfig,
    TokenEntry,
    VaultConfig,
)

# ---------------------------------------------------------------------------
# Token allocation defaults
# ---------------------------------------------------------------------------


class TestTokenAllocationDefaults:
    """Verify all token entries have sensible defaults."""

    def test_synthesis_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.synthesis.max_tokens == 4000
        assert ta.synthesis.temperature == 0.7

    def test_routing_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.routing.max_tokens == 300
        assert ta.routing.temperature == 0.3

    def test_briefing_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.briefing.max_tokens == 50
        assert ta.briefing.temperature == 0.3

    def test_session_extraction_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.session_extraction.max_tokens == 500

    def test_entity_extraction_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.entity_extraction.max_tokens == 500

    def test_memory_extraction_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.memory_extraction.max_tokens == 500

    def test_organize_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.organize.max_tokens == 4000

    def test_lobotomy_scan_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.lobotomy_scan.max_tokens == 1000

    def test_code_description_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.code_description.max_tokens == 150

    def test_contradiction_check_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.contradiction_check.max_tokens == 500

    def test_contradiction_resolve_defaults(self) -> None:
        ta = TokenAllocationConfig()
        assert ta.contradiction_resolve.max_tokens == 1000


class TestTokenAllocationOverrides:
    """Verify config values override defaults."""

    def test_override_synthesis_tokens(self) -> None:
        ta = TokenAllocationConfig(
            synthesis=TokenEntry(max_tokens=8000, temperature=0.9)
        )
        assert ta.synthesis.max_tokens == 8000
        assert ta.synthesis.temperature == 0.9

    def test_override_routing_tokens(self) -> None:
        ta = TokenAllocationConfig(routing=TokenEntry(max_tokens=600))
        assert ta.routing.max_tokens == 600
        # Temperature should still be default
        assert ta.routing.temperature == 0.3


class TestSummarizationTokenEntry:
    """Verify summarization entry has section-specific fields."""

    def test_has_section_fields(self) -> None:
        s = SummarizationTokenEntry()
        assert s.max_section_tokens == 150
        assert s.max_note_tokens == 200
        assert s.max_section_chars == 2000
        assert s.max_note_chars == 3000
        assert s.temperature == 0.3

    def test_override_section_fields(self) -> None:
        s = SummarizationTokenEntry(
            max_section_tokens=300,
            max_note_tokens=400,
            max_section_chars=4000,
        )
        assert s.max_section_tokens == 300
        assert s.max_note_tokens == 400
        assert s.max_section_chars == 4000


class TestRetrievalTokenEntry:
    """Verify retrieval entry has threshold fields."""

    def test_has_threshold_fields(self) -> None:
        r = RetrievalTokenEntry()
        assert r.max_context_chars == 8000
        assert r.max_briefing_chars == 24000
        assert r.short_file_chars == 4000
        assert r.max_query_length == 10000
        assert r.heading_match_threshold == 0.5
        assert r.content_match_threshold == 0.4

    def test_override_thresholds(self) -> None:
        r = RetrievalTokenEntry(
            heading_match_threshold=0.7,
            content_match_threshold=0.6,
        )
        assert r.heading_match_threshold == 0.7
        assert r.content_match_threshold == 0.6


# ---------------------------------------------------------------------------
# Response mode
# ---------------------------------------------------------------------------


class TestResponseMode:
    """Tests for strict/lax response mode configuration."""

    def test_project_default_is_strict(self) -> None:
        project = ProjectConfig(
            name="test",
            vaults=[VaultConfig(name="v", path=Path("/tmp/v"))],
        )
        assert project.response_mode == "strict"

    def test_project_can_set_lax(self) -> None:
        project = ProjectConfig(
            name="test",
            vaults=[VaultConfig(name="v", path=Path("/tmp/v"))],
            response_mode="lax",
        )
        assert project.response_mode == "lax"

    def test_strict_prompt_is_different_from_lax(self) -> None:
        from archivyst.agent.prompts import SYNTHESIS_PROMPT, SYNTHESIS_PROMPT_LAX

        assert SYNTHESIS_PROMPT != SYNTHESIS_PROMPT_LAX
        # Strict should mention answering from context
        assert "retrieved" in SYNTHESIS_PROMPT.lower()
        # Lax should mention own knowledge
        assert "own knowledge" in SYNTHESIS_PROMPT_LAX.lower()

    def test_config_loads_response_mode(self) -> None:
        """Config with response_mode should parse correctly."""
        config = Config(
            memory=MemoryConfig(path=Path("/tmp/mem")),
            llm=LLMConfig(),
            projects=[
                ProjectConfig(
                    name="test",
                    vaults=[VaultConfig(name="v", path=Path("/tmp/v"))],
                    response_mode="lax",
                )
            ],
        )
        project = config.get_project("test")
        assert project is not None
        assert project.response_mode == "lax"
