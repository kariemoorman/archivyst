"""
Tests for agent module: JSON parsing, extraction, memory, prompt builder.
"""

import tempfile
from pathlib import Path

import pytest

from archivyst.agent.extraction import save_extracted_entities, save_extracted_memory
from archivyst.agent.memory import MemoryAccessError, MemoryStore
from archivyst.agent.prompt_builder import PromptBuilder
from archivyst.core.models import (
    Config,
    EntityExtractionResponse,
    GlobalAccess,
    IndexingConfig,
    LLMConfig,
    LLMModelsConfig,
    MemoryConfig,
    MemoryExtractionResponse,
    ProjectConfig,
    ProjectMemoryConfig,
    RetrievalTokenEntry,
    TokenAllocationConfig,
    VaultConfig,
)
from archivyst.llm.utils import parse_json_response


@pytest.fixture
def secure_temp_dir():
    """Create a resolved temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir).resolve()


@pytest.fixture
def memory_store(secure_temp_dir: Path) -> MemoryStore:
    """Create a MemoryStore for testing."""
    memory_path = secure_temp_dir / "agent-memory"
    memory_path.mkdir()
    (memory_path / "global").mkdir()
    (memory_path / "projects").mkdir()

    vault_path = secure_temp_dir / "vault"
    vault_path.mkdir()

    config = Config(
        memory=MemoryConfig(path=memory_path),
        projects=[
            ProjectConfig(
                name="test-project",
                vaults=[VaultConfig(name="test-vault", path=vault_path, active=True)],
            )
        ],
        default_project="test-project",
        llm=LLMConfig(
            models=LLMModelsConfig(
                summarization="test",
                routing="test",
                synthesis="test",
                vision="test",
                memory="test",
            ),
        ),
        indexing=IndexingConfig(summarize_sections=False),
    )

    scope = config.build_project_scope("test-project")
    return MemoryStore(config, scope)


class TestParseJsonResponse:
    """Tests for parse_json_response utility."""

    def test_parses_plain_json(self) -> None:
        """Plain JSON string should parse correctly."""
        result = parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parses_json_in_code_block(self) -> None:
        """JSON wrapped in ```json ... ``` should parse."""
        response = '```json\n{"key": "value"}\n```'
        result = parse_json_response(response)
        assert result == {"key": "value"}

    def test_parses_json_in_plain_code_block(self) -> None:
        """JSON wrapped in ``` ... ``` (no json tag) should parse."""
        response = '```\n{"key": "value"}\n```'
        result = parse_json_response(response)
        assert result == {"key": "value"}

    def test_parses_json_with_surrounding_text(self) -> None:
        """JSON in code block with surrounding text should parse."""
        response = 'Here is the result:\n```json\n{"key": "value"}\n```\nDone.'
        result = parse_json_response(response)
        assert result == {"key": "value"}

    def test_raises_on_empty_string(self) -> None:
        """Empty string should raise."""
        import json

        with pytest.raises((json.JSONDecodeError, ValueError)):
            parse_json_response("")

    def test_raises_on_invalid_json(self) -> None:
        """Invalid JSON should raise JSONDecodeError."""
        import json

        with pytest.raises(json.JSONDecodeError):
            parse_json_response("not json at all")

    def test_parses_nested_json(self) -> None:
        """Nested JSON structures should parse."""
        response = '{"people": [{"name": "Alice", "role": "engineer"}]}'
        result = parse_json_response(response)
        assert result["people"][0]["name"] == "Alice"

    def test_handles_whitespace(self) -> None:
        """JSON with leading/trailing whitespace should parse."""
        result = parse_json_response('  \n  {"key": "value"}  \n  ')
        assert result == {"key": "value"}


class TestPydanticResponseModels:
    """Tests for EntityExtractionResponse and MemoryExtractionResponse."""

    def test_entity_response_valid(self) -> None:
        """Valid entity extraction response should validate."""
        data = {
            "people": [{"name": "Alice", "role": "engineer"}],
            "projects": [{"name": "Archivyst", "description": "RAG tool"}],
            "concepts": [{"term": "RAG", "definition": "Retrieval-augmented gen"}],
        }
        result = EntityExtractionResponse.model_validate(data)
        assert len(result.people) == 1
        assert result.people[0]["name"] == "Alice"

    def test_entity_response_missing_fields_defaults(self) -> None:
        """Missing fields should default to empty lists."""
        result = EntityExtractionResponse.model_validate({})
        assert result.people == []
        assert result.projects == []
        assert result.concepts == []

    def test_entity_response_extra_fields_ignored(self) -> None:
        """Extra fields should be accepted (not strict)."""
        data = {"people": [], "projects": [], "concepts": [], "extra": "ignored"}
        result = EntityExtractionResponse.model_validate(data)
        assert result.people == []

    def test_memory_response_valid(self) -> None:
        """Valid memory extraction response should validate."""
        data = {
            "preferences": ["dark mode"],
            "facts": ["user is an engineer"],
            "corrections": ["use markdown not JSON"],
            "patterns": ["asks about architecture"],
        }
        result = MemoryExtractionResponse.model_validate(data)
        assert len(result.preferences) == 1
        assert result.facts[0] == "user is an engineer"

    def test_memory_response_missing_fields_defaults(self) -> None:
        """Missing fields should default to empty lists."""
        result = MemoryExtractionResponse.model_validate({})
        assert result.preferences == []
        assert result.facts == []


class TestSaveExtractedEntities:
    """Tests for save_extracted_entities with Pydantic validation."""

    def test_saves_valid_entities(self, memory_store: MemoryStore) -> None:
        """Valid entity data should be saved to memory with correct content."""
        data = {
            "people": [{"name": "Alice", "role": "engineer"}],
            "projects": [{"name": "TestProject", "description": "A test"}],
            "concepts": [{"term": "RAG", "definition": "Retrieval stuff"}],
        }
        count = save_extracted_entities(memory_store, data)
        assert count == 3

        # Verify content was saved correctly
        alice = memory_store.load_entity("people", "Alice")
        assert alice is not None
        assert "Alice" in str(alice)
        assert "engineer" in str(alice)

        project = memory_store.load_entity("projects", "TestProject")
        assert project is not None
        assert "A test" in str(project)

        concept = memory_store.load_entity("concepts", "RAG")
        assert concept is not None
        assert "Retrieval" in str(concept)

    def test_skips_empty_names(self, memory_store: MemoryStore) -> None:
        """Entities with empty names should be skipped."""
        data = {
            "people": [{"name": "", "role": "nobody"}],
            "projects": [],
            "concepts": [],
        }
        count = save_extracted_entities(memory_store, data)
        assert count == 0

    def test_handles_invalid_structure(self, memory_store: MemoryStore) -> None:
        """Invalid structure should return 0 (Pydantic validation fails)."""
        data = {"people": "not a list"}
        count = save_extracted_entities(memory_store, data)
        assert count == 0


class TestSaveExtractedMemory:
    """Tests for save_extracted_memory with Pydantic validation."""

    def test_saves_valid_memory(self, memory_store: MemoryStore) -> None:
        """Valid memory data should be saved."""
        data = {
            "preferences": ["use markdown"],
            "facts": ["user likes coffee"],
            "corrections": [],
            "patterns": ["asks short questions"],
        }
        count = save_extracted_memory(memory_store, data)
        assert count == 3  # 1 pref + 1 fact + 1 pattern

    def test_skips_empty_strings(self, memory_store: MemoryStore) -> None:
        """Empty strings should be skipped."""
        data = {
            "preferences": ["", "  "],
            "facts": [],
            "corrections": [],
            "patterns": [],
        }
        count = save_extracted_memory(memory_store, data)
        assert count == 0

    def test_handles_invalid_structure(self, memory_store: MemoryStore) -> None:
        """Invalid structure should return 0."""
        data = {"preferences": 42}
        count = save_extracted_memory(memory_store, data)
        assert count == 0


class TestMemoryStorePromoteToGlobal:
    """Tests for promote_to_global access control."""

    def test_promote_with_full_access(self, secure_temp_dir: Path) -> None:
        """Promotion should work with FULL global access."""
        memory_path = secure_temp_dir / "memory"
        memory_path.mkdir()
        (memory_path / "global").mkdir()
        (memory_path / "projects").mkdir()

        config = Config(
            memory=MemoryConfig(path=memory_path),
            projects=[
                ProjectConfig(
                    name="test",
                    vaults=[
                        VaultConfig(
                            name="v",
                            path=secure_temp_dir / "vault",
                            active=True,
                        )
                    ],
                )
            ],
            default_project="test",
            llm=LLMConfig(),
            indexing=IndexingConfig(summarize_sections=False),
        )

        (secure_temp_dir / "vault").mkdir(exist_ok=True)
        scope = config.build_project_scope("test")
        assert scope.global_access == GlobalAccess.FULL

        memory = MemoryStore(config, scope)
        memory.write("semantic/facts/test-fact.md", "A test fact")

        result = memory.promote_to_global(
            "semantic/facts/test-fact.md", reason="testing"
        )
        assert result is not None

    def test_promote_blocked_with_none_access(self, secure_temp_dir: Path) -> None:
        """Promotion should fail with NONE global access."""
        memory_path = secure_temp_dir / "memory"
        memory_path.mkdir()
        (memory_path / "global").mkdir()
        (memory_path / "projects").mkdir()

        config = Config(
            memory=MemoryConfig(path=memory_path),
            projects=[
                ProjectConfig(
                    name="isolated",
                    vaults=[
                        VaultConfig(
                            name="v",
                            path=secure_temp_dir / "vault",
                            active=True,
                        )
                    ],
                    memory=ProjectMemoryConfig(global_access=GlobalAccess.NONE),
                )
            ],
            default_project="isolated",
            llm=LLMConfig(),
            indexing=IndexingConfig(summarize_sections=False),
        )

        (secure_temp_dir / "vault").mkdir(exist_ok=True)
        scope = config.build_project_scope("isolated")
        assert scope.global_access == GlobalAccess.NONE

        memory = MemoryStore(config, scope)
        memory.write("semantic/facts/test-fact.md", "A test fact")

        with pytest.raises(MemoryAccessError):
            memory.promote_to_global(
                "semantic/facts/test-fact.md", reason="should fail"
            )

    def test_promote_blocked_with_read_access(self, secure_temp_dir: Path) -> None:
        """Promotion should fail with READ global access."""
        memory_path = secure_temp_dir / "memory"
        memory_path.mkdir()
        (memory_path / "global").mkdir()
        (memory_path / "projects").mkdir()

        config = Config(
            memory=MemoryConfig(path=memory_path),
            projects=[
                ProjectConfig(
                    name="readonly",
                    vaults=[
                        VaultConfig(
                            name="v",
                            path=secure_temp_dir / "vault",
                            active=True,
                        )
                    ],
                    memory=ProjectMemoryConfig(global_access=GlobalAccess.READ),
                )
            ],
            default_project="readonly",
            llm=LLMConfig(),
            indexing=IndexingConfig(summarize_sections=False),
        )

        (secure_temp_dir / "vault").mkdir(exist_ok=True)
        scope = config.build_project_scope("readonly")
        assert scope.global_access == GlobalAccess.READ

        memory = MemoryStore(config, scope)
        memory.write("semantic/facts/test-fact.md", "A test fact")

        with pytest.raises(MemoryAccessError):
            memory.promote_to_global(
                "semantic/facts/test-fact.md", reason="should fail"
            )

    def test_promote_copies_content_correctly(self, secure_temp_dir: Path) -> None:
        """Promoted content should appear in global memory."""
        memory_path = secure_temp_dir / "memory"
        memory_path.mkdir()
        (memory_path / "global").mkdir()
        (memory_path / "projects").mkdir()

        config = Config(
            memory=MemoryConfig(path=memory_path),
            projects=[
                ProjectConfig(
                    name="test",
                    vaults=[
                        VaultConfig(
                            name="v",
                            path=secure_temp_dir / "vault",
                            active=True,
                        )
                    ],
                )
            ],
            default_project="test",
            llm=LLMConfig(),
            indexing=IndexingConfig(summarize_sections=False),
        )

        (secure_temp_dir / "vault").mkdir(exist_ok=True)
        scope = config.build_project_scope("test")
        memory = MemoryStore(config, scope)

        memory.write("semantic/facts/test-fact.md", "Important fact")
        memory.promote_to_global("semantic/facts/test-fact.md", reason="testing")

        # Verify content exists in global
        global_content = memory.read("semantic/facts/test-fact.md", "global")
        assert global_content is not None
        assert "Important fact" in global_content
        assert "Promoted from project" in global_content


class TestPromptBuilder:
    """Tests for PromptBuilder."""

    def test_basic_build(self) -> None:
        """Basic prompt with system, query, and suffix."""
        prompt = (
            PromptBuilder()
            .system("You are a test assistant.")
            .query("What is 2+2?")
            .suffix("Answer:")
            .build()
        )
        assert "You are a test assistant." in prompt
        assert "What is 2+2?" in prompt
        assert "Answer:" in prompt

    def test_context_truncation(self) -> None:
        """Context should be truncated to exactly max_context_chars."""
        config = Config(
            memory=MemoryConfig(path=Path("/tmp/test")),
            token_allocation=TokenAllocationConfig(
                retrieval=RetrievalTokenEntry(max_context_chars=50),
            ),
            llm=LLMConfig(),
        )
        long_context = "abcdefghij" * 20  # 200 chars
        prompt = PromptBuilder(config).context(long_context).build()
        assert "[...context truncated...]" in prompt
        # The context portion should start with the first 50 chars
        assert "abcdefghij" * 5 in prompt
        # Full 200-char input should NOT be present
        assert long_context not in prompt

    def test_query_truncation(self) -> None:
        """Query should be truncated to exactly max_query_length."""
        config = Config(
            memory=MemoryConfig(path=Path("/tmp/test")),
            token_allocation=TokenAllocationConfig(
                retrieval=RetrievalTokenEntry(max_query_length=20),
            ),
            llm=LLMConfig(),
        )
        long_query = "a" * 100
        prompt = PromptBuilder(config).query(long_query).build()
        assert "User Query:" in prompt
        # Full 100-char query should NOT be present
        assert long_query not in prompt
        # Only first 20 chars should appear
        assert "a" * 20 in prompt

    def test_instructions(self) -> None:
        """Multiple instructions should be numbered."""
        prompt = (
            PromptBuilder()
            .instruction("Be concise")
            .instruction("Cite sources")
            .build()
        )
        assert "1. Be concise" in prompt
        assert "2. Cite sources" in prompt

    def test_sections(self) -> None:
        """Named sections should appear in output."""
        prompt = (
            PromptBuilder()
            .section("Memory", "User likes dark mode")
            .section("Facts", "User is an engineer")
            .build()
        )
        assert "Memory:\nUser likes dark mode" in prompt
        assert "Facts:\nUser is an engineer" in prompt

    def test_empty_build(self) -> None:
        """Empty builder should produce empty string."""
        prompt = PromptBuilder().build()
        assert prompt == ""

    def test_max_briefing_chars_from_config(self) -> None:
        """max_briefing_chars should come from config."""
        config = Config(
            memory=MemoryConfig(path=Path("/tmp/test")),
            token_allocation=TokenAllocationConfig(
                retrieval=RetrievalTokenEntry(max_briefing_chars=16000),
            ),
            llm=LLMConfig(),
        )
        builder = PromptBuilder(config)
        assert builder.max_briefing_chars == 16000
