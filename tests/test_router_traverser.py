"""
Tests for router and traverser JSON fallback paths.
"""

from pathlib import Path

from archivyst.core.models import SectionNode
from archivyst.retrieval.router import parse_routing_response
from archivyst.retrieval.traverser import parse_traversal_response


class TestParseRoutingResponse:
    """Tests for parse_routing_response JSON and fallback parsing."""

    def test_parses_valid_json(self) -> None:
        """Valid JSON response should parse correctly."""
        response = """{
            "selected_documents": ["notes/alpha.md", "notes/beta.md"],
            "reasoning": "Both relevant",
            "confidence": 0.9
        }"""
        result = parse_routing_response(response)
        assert len(result.selected_notes) == 2
        assert result.selected_notes[0] == Path("notes/alpha.md")
        assert result.confidence == 0.9

    def test_parses_json_in_code_block(self) -> None:
        """JSON in code block should parse."""
        response = """```json
{
    "selected_documents": ["alpha.md"],
    "reasoning": "Relevant",
    "confidence": 0.8
}
```"""
        result = parse_routing_response(response)
        assert len(result.selected_notes) == 1

    def test_fallback_extracts_paths_from_text(self) -> None:
        """Non-JSON response with .md paths should extract them."""
        response = """I think the relevant documents are:
- notes/alpha.md (contains info about topic)
- beta.md (related discussion)
"""
        result = parse_routing_response(response)
        assert len(result.selected_notes) >= 1
        paths = [str(p) for p in result.selected_notes]
        assert any("alpha.md" in p for p in paths)

    def test_fallback_empty_response(self) -> None:
        """Empty non-JSON response should return empty selection."""
        result = parse_routing_response("I don't know which documents to select.")
        assert len(result.selected_notes) == 0

    def test_missing_fields_use_defaults(self) -> None:
        """Missing JSON fields should use defaults."""
        response = '{"selected_documents": ["test.md"]}'
        result = parse_routing_response(response)
        assert result.reasoning == ""
        assert result.confidence == 0.5


class TestParseTraversalResponse:
    """Tests for parse_traversal_response JSON and fallback parsing."""

    def _make_sections(self) -> list[SectionNode]:
        """Create test sections."""
        return [
            SectionNode(
                heading="Introduction",
                level=2,
                start_line=1,
                end_line=10,
            ),
            SectionNode(
                heading="Architecture",
                level=2,
                start_line=11,
                end_line=30,
            ),
            SectionNode(
                heading="Conclusion",
                level=2,
                start_line=31,
                end_line=40,
            ),
        ]

    def test_parses_valid_json(self) -> None:
        """Valid JSON decisions should parse correctly."""
        sections = self._make_sections()
        response = """{
            "decisions": [
                {"section": "Introduction", "action": "skip", "reason": "not relevant"},
                {"section": "Architecture", "action": "select", "reason": "directly relevant"},
                {"section": "Conclusion", "action": "skip", "reason": "too general"}
            ]
        }"""
        results = parse_traversal_response(response, sections)
        assert len(results) >= 1
        # Find the Architecture decision
        arch_decisions = [
            (s, a, r) for s, a, r in results if s.heading == "Architecture"
        ]
        assert len(arch_decisions) == 1
        assert arch_decisions[0][1] == "select"

    def test_parses_json_in_code_block(self) -> None:
        """JSON in code block should parse."""
        sections = self._make_sections()
        response = """```json
{
    "decisions": [
        {"section": "Architecture", "action": "expand", "reason": "has subsections"}
    ]
}
```"""
        results = parse_traversal_response(response, sections)
        assert len(results) >= 1

    def test_fallback_selects_all(self) -> None:
        """Invalid JSON should fallback to selecting all sections."""
        sections = self._make_sections()
        response = "This is not valid JSON at all."
        results = parse_traversal_response(response, sections)
        # Fallback: all sections selected
        assert len(results) == len(sections)
        for _, action, reason in results:
            assert action == "select"
            assert reason == "fallback"

    def test_fuzzy_section_matching(self) -> None:
        """Section names that partially match should still resolve."""
        sections = self._make_sections()
        response = """{
            "decisions": [
                {"section": "architecture", "action": "select", "reason": "match"}
            ]
        }"""
        results = parse_traversal_response(response, sections)
        # Should fuzzy-match "architecture" to "Architecture"
        assert len(results) >= 1
        assert results[0][0].heading == "Architecture"

    def test_empty_decisions(self) -> None:
        """Empty decisions list should return empty results."""
        sections = self._make_sections()
        response = '{"decisions": []}'
        results = parse_traversal_response(response, sections)
        assert len(results) == 0
