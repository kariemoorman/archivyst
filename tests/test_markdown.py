"""
Tests for markdown parsing functionality.
"""

from pathlib import Path

from archivyst.core.markdown import (
    build_toc_tree,
    extract_headings,
    extract_images,
    extract_wikilinks,
    parse_frontmatter,
    parse_note,
    resolve_wikilink,
)


class TestParseFrontmatter:
    """Tests for frontmatter extraction."""

    def test_extracts_yaml_frontmatter(self):
        content = """\
---
title: Test Note
tags:
  - one
  - two
---

# Content here
"""
        frontmatter, body = parse_frontmatter(content)

        assert frontmatter["title"] == "Test Note"
        assert frontmatter["tags"] == ["one", "two"]
        assert body.strip().startswith("# Content here")

    def test_handles_missing_frontmatter(self):
        content = "# No Frontmatter\n\nJust content."
        frontmatter, body = parse_frontmatter(content)

        assert frontmatter == {}
        assert body == content

    def test_handles_empty_frontmatter(self):
        content = """\
---
---

# Content
"""
        frontmatter, body = parse_frontmatter(content)

        assert frontmatter == {}
        assert "# Content" in body

    def test_handles_invalid_yaml(self):
        content = """\
---
invalid: [unclosed
---

# Content
"""
        frontmatter, body = parse_frontmatter(content)

        # Should return empty dict on parse error
        assert frontmatter == {}


class TestExtractHeadings:
    """Tests for heading extraction."""

    def test_extracts_all_heading_levels(self):
        content = """\
# H1 Title
## H2 Section
### H3 Subsection
#### H4 Deep
##### H5 Deeper
###### H6 Deepest
"""
        headings = extract_headings(content)

        assert len(headings) == 6
        assert headings[0].level == 1
        assert headings[0].text == "H1 Title"
        assert headings[5].level == 6
        assert headings[5].text == "H6 Deepest"

    def test_ignores_headings_in_code_blocks(self):
        content = """\
# Real Heading

```python
# This is a comment, not a heading
def foo():
    pass
```

## Another Real Heading
"""
        headings = extract_headings(content)

        assert len(headings) == 2
        assert headings[0].text == "Real Heading"
        assert headings[1].text == "Another Real Heading"

    def test_ignores_headings_in_inline_code(self):
        content = """\
# Real Heading

Use `# not a heading` in your code.

## Another Heading
"""
        headings = extract_headings(content)

        assert len(headings) == 2

    def test_tracks_line_numbers(self):
        content = """\
# First

Some content.

## Second

More content.

### Third
"""
        headings = extract_headings(content)

        assert headings[0].line_number == 1
        assert headings[1].line_number == 5
        assert headings[2].line_number == 9


class TestExtractWikilinks:
    """Tests for wikilink extraction."""

    def test_extracts_simple_wikilinks(self):
        content = "See [[note-one]] and [[note-two]] for details."
        links = extract_wikilinks(content)

        assert set(links) == {"note-one", "note-two"}

    def test_extracts_links_with_aliases(self):
        content = "See [[actual-note|Display Name]] for info."
        links = extract_wikilinks(content)

        assert links == ["actual-note"]

    def test_handles_links_with_headings(self):
        content = "Jump to [[note#section]] or [[other#heading|link]]."
        links = extract_wikilinks(content)

        assert "note#section" in links
        assert "other#heading" in links

    def test_ignores_links_in_code_blocks(self):
        content = """\
Real [[link]] here.

```
[[not-a-link]]
```

Another [[real-link]].
"""
        links = extract_wikilinks(content)

        assert set(links) == {"link", "real-link"}

    def test_deduplicates_links(self):
        content = "[[same]] appears [[same]] twice."
        links = extract_wikilinks(content)

        assert links == ["same"]


class TestExtractImages:
    """Tests for image extraction."""

    def test_extracts_wikilink_images(self):
        content = "Here is ![[image.png]] embedded."
        images = extract_images(content)

        assert images == ["image.png"]

    def test_extracts_markdown_images(self):
        content = "Here is ![alt text](path/to/image.jpg)."
        images = extract_images(content)

        assert images == ["path/to/image.jpg"]

    def test_extracts_mixed_formats(self):
        content = """\
![[wiki-image.png]]
![markdown](md-image.jpg)
"""
        images = extract_images(content)

        assert set(images) == {"wiki-image.png", "md-image.jpg"}


class TestBuildTocTree:
    """Tests for ToC tree construction."""

    def test_builds_flat_structure(self):
        from archivyst.core.markdown import HeadingInfo

        headings = [
            HeadingInfo(level=2, text="First", line_number=1, start_pos=0, end_pos=10),
            HeadingInfo(
                level=2, text="Second", line_number=5, start_pos=50, end_pos=60
            ),
            HeadingInfo(
                level=2, text="Third", line_number=10, start_pos=100, end_pos=110
            ),
        ]

        tree = build_toc_tree(headings, total_lines=15)

        assert len(tree) == 3
        assert all(node.children == [] for node in tree)

    def test_builds_nested_structure(self):
        from archivyst.core.markdown import HeadingInfo

        headings = [
            HeadingInfo(level=1, text="Title", line_number=1, start_pos=0, end_pos=10),
            HeadingInfo(
                level=2, text="Section", line_number=3, start_pos=20, end_pos=30
            ),
            HeadingInfo(
                level=3, text="Subsection", line_number=5, start_pos=40, end_pos=50
            ),
            HeadingInfo(
                level=2, text="Another Section", line_number=8, start_pos=70, end_pos=80
            ),
        ]

        tree = build_toc_tree(headings, total_lines=10)

        assert len(tree) == 1  # One H1
        assert tree[0].heading == "Title"
        assert len(tree[0].children) == 2  # Two H2s
        assert tree[0].children[0].heading == "Section"
        assert len(tree[0].children[0].children) == 1  # One H3
        assert tree[0].children[0].children[0].heading == "Subsection"

    def test_handles_empty_headings(self):
        tree = build_toc_tree([], total_lines=10)
        assert tree == []

    def test_calculates_end_lines(self):
        from archivyst.core.markdown import HeadingInfo

        headings = [
            HeadingInfo(level=2, text="First", line_number=1, start_pos=0, end_pos=10),
            HeadingInfo(
                level=2, text="Second", line_number=10, start_pos=50, end_pos=60
            ),
        ]

        tree = build_toc_tree(headings, total_lines=20)

        assert tree[0].end_line == 9  # Ends before next heading
        assert tree[1].end_line == 20  # Ends at document end


class TestResolveWikilink:
    """Tests for wikilink resolution."""

    def test_strict_resolution(self):
        vault_notes = {
            "note-one": Path("/vault/note-one.md"),
            "Note-Two": Path("/vault/Note-Two.md"),
        }

        result = resolve_wikilink("note-one", vault_notes, resolution="strict")
        assert result == Path("/vault/note-one.md")

        # Case mismatch fails in strict mode
        result = resolve_wikilink("Note-One", vault_notes, resolution="strict")
        assert result is None

    def test_case_insensitive_resolution(self):
        vault_notes = {
            "Note-One": Path("/vault/Note-One.md"),
        }

        result = resolve_wikilink(
            "note-one", vault_notes, resolution="case_insensitive"
        )
        assert result == Path("/vault/Note-One.md")

    def test_alias_resolution(self):
        vault_notes = {
            "project-alpha.md": Path("/vault/project-alpha.md"),
        }
        note_aliases = {
            "project-alpha.md": ["Alpha", "Project A"],
        }

        result = resolve_wikilink(
            "Alpha",
            vault_notes,
            resolution="case_insensitive_with_aliases",
            note_aliases=note_aliases,
        )
        assert result == Path("/vault/project-alpha.md")

    def test_strips_heading_references(self):
        vault_notes = {
            "note": Path("/vault/note.md"),
        }

        result = resolve_wikilink(
            "note#section", vault_notes, resolution="case_insensitive"
        )
        assert result == Path("/vault/note.md")


class TestParseNote:
    """Tests for full note parsing."""

    def test_parses_complete_note(self, temp_dir: Path):
        note_path = temp_dir / "test.md"
        note_path.write_text("""\
---
title: Test
aliases:
  - Testing
---

# Test Note

## Section One

Content with [[link-one]] here.

![[image.png]]

## Section Two

More [[link-two]] content.
""")

        parsed = parse_note(note_path)

        assert parsed.path == note_path
        assert parsed.frontmatter["title"] == "Test"
        assert parsed.frontmatter["aliases"] == ["Testing"]
        assert len(parsed.headings) == 3
        assert {"link-one", "link-two"}.issubset(set(parsed.wikilinks))
        assert "image.png" in parsed.images
