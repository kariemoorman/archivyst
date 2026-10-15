"""
Markdown parsing for heading extraction, wikilinks, and structure analysis.

Extracts ToC structure without external markdown parsing dependencies.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from archivyst.core.models import FrontmatterValue, NoteToC, SectionNode

logger = logging.getLogger(__name__)

# Regex patterns
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
IMAGE_EMBED_PATTERN = re.compile(r"!\[\[([^\]]+)\]\]|!\[([^\]]*)\]\(([^)]+)\)")
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```|`[^`\n]+`")


@dataclass
class HeadingInfo:
    """Raw heading information extracted from markdown."""

    level: int
    text: str
    line_number: int
    start_pos: int
    end_pos: int


@dataclass
class ParsedNote:
    """Result of parsing a markdown note."""

    path: Path
    content: str
    frontmatter: dict[str, FrontmatterValue]
    headings: list[HeadingInfo]
    wikilinks: list[str]
    images: list[str]
    attachments: list[str]


MAX_FRONTMATTER_SIZE = 10_000  # 10KB limit to prevent YAML bombs


def parse_frontmatter(content: str) -> tuple[dict[str, FrontmatterValue], str]:
    """
    Extract YAML frontmatter from markdown content.

    Returns:
        Tuple of (frontmatter dict, content without frontmatter)
    """
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content

    frontmatter_text = match.group(1)

    # Protect against YAML bombs (deeply nested anchors/aliases)
    if len(frontmatter_text) > MAX_FRONTMATTER_SIZE:
        logger.warning(
            "[parsing] Frontmatter exceeds size limit (%d bytes), skipping",
            len(frontmatter_text),
        )
        return {}, content

    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        frontmatter = {}

    content_without_fm = content[match.end() :]
    return frontmatter, content_without_fm


def extract_headings(content: str) -> list[HeadingInfo]:
    """
    Extract all headings from markdown content.

    Ignores headings inside code blocks.
    """
    # Remove code blocks to avoid false matches
    content_no_code = CODE_BLOCK_PATTERN.sub(
        lambda m: "\n" * m.group(0).count("\n"), content  # Preserve line numbers
    )

    headings: list[HeadingInfo] = []
    lines = content_no_code.split("\n")

    for line_num, line in enumerate(lines, start=1):
        match = HEADING_PATTERN.match(line)
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            headings.append(
                HeadingInfo(
                    level=level,
                    text=text,
                    line_number=line_num,
                    start_pos=sum(len(line) + 1 for line in lines[: line_num - 1]),
                    end_pos=sum(len(line) + 1 for line in lines[:line_num]),
                )
            )

    return headings


def extract_wikilinks(content: str) -> list[str]:
    """
    Extract all [[wikilinks]] from content.

    Handles aliases: [[actual|display]] returns "actual".
    Ignores links inside code blocks.
    """
    content_no_code = CODE_BLOCK_PATTERN.sub("", content)
    return list(set(WIKILINK_PATTERN.findall(content_no_code)))


def extract_images(content: str) -> list[str]:
    """
    Extract all embedded images from content.

    Handles both ![[image.png]] and ![alt](path) syntax.
    """
    content_no_code = CODE_BLOCK_PATTERN.sub("", content)
    images: list[str] = []

    for match in IMAGE_EMBED_PATTERN.finditer(content_no_code):
        # ![[image]] syntax
        if match.group(1):
            images.append(match.group(1))
        # ![alt](path) syntax
        elif match.group(3):
            images.append(match.group(3))

    return list(set(images))


def extract_attachments(wikilinks: list[str], images: list[str]) -> list[str]:
    """
    Identify attachments (PDFs, etc.) from links.

    Returns links that point to non-markdown, non-image files.
    """
    attachment_extensions = {".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"}

    attachments: list[str] = []
    all_links = set(wikilinks) | set(images)

    for link in all_links:
        ext = Path(link).suffix.lower()
        if ext in attachment_extensions:
            attachments.append(link)

    return attachments


def build_toc_tree(headings: list[HeadingInfo], total_lines: int) -> list[SectionNode]:
    """
    Build hierarchical ToC tree from flat heading list.

    Handles arbitrary heading levels and nesting.
    """
    if not headings:
        return []

    # Calculate end lines for each heading
    heading_with_ends: list[tuple[HeadingInfo, int | None]] = []
    for i, heading in enumerate(headings):
        end_line = (
            headings[i + 1].line_number - 1 if i + 1 < len(headings) else total_lines
        )
        heading_with_ends.append((heading, end_line))

    def build_children(
        start_idx: int, parent_level: int, end_idx: int
    ) -> tuple[list[SectionNode], int]:
        """Recursively build children for a section."""
        children: list[SectionNode] = []
        i = start_idx

        while i < end_idx:
            heading, end_line = heading_with_ends[i]

            # If we hit a heading at or above parent level, we're done with children
            if heading.level <= parent_level:
                break

            # Find where this heading's section ends
            section_end_idx = i + 1
            while section_end_idx < end_idx:
                next_heading, _ = heading_with_ends[section_end_idx]
                if next_heading.level <= heading.level:
                    break
                section_end_idx += 1

            # Build this node
            node_children, _ = build_children(i + 1, heading.level, section_end_idx)

            node = SectionNode(
                heading=heading.text,
                level=heading.level,
                start_line=heading.line_number,
                end_line=end_line,
                children=node_children,
            )
            children.append(node)

            i = section_end_idx

        return children, i

    # Build from level 0 (treat all top-level headings as children)
    root_children, _ = build_children(0, 0, len(headings))
    return root_children


def parse_note(path: Path) -> ParsedNote:
    """
    Parse a markdown note file.

    Returns structured information about the note's content.
    """
    content = path.read_text(encoding="utf-8")
    frontmatter, content_without_fm = parse_frontmatter(content)

    headings = extract_headings(content)
    wikilinks = extract_wikilinks(content)
    images = extract_images(content)
    attachments = extract_attachments(wikilinks, images)

    return ParsedNote(
        path=path,
        content=content,
        frontmatter=frontmatter,
        headings=headings,
        wikilinks=wikilinks,
        images=images,
        attachments=attachments,
    )


def build_note_toc(parsed: ParsedNote, summary: str | None = None) -> NoteToC:
    """
    Build a NoteToC from a parsed note.

    Args:
        parsed: Parsed note data
        summary: Optional LLM-generated summary
    """
    total_lines = parsed.content.count("\n") + 1
    sections = build_toc_tree(parsed.headings, total_lines)

    # Determine title from first H1 or filename
    title = parsed.path.stem
    for heading in parsed.headings:
        if heading.level == 1:
            title = heading.text
            break

    # If there's content before the first heading, create a synthetic
    # intro section so that content isn't lost during retrieval.
    first_heading_line = (
        parsed.headings[0].line_number if parsed.headings else total_lines
    )
    if first_heading_line > 1:
        # Check there's actual content (not just frontmatter/blank lines)
        _, content_body = parse_frontmatter(parsed.content)
        intro_lines = content_body.split("\n")[: first_heading_line - 1]
        intro_text = "\n".join(intro_lines).strip()
        if intro_text:
            intro_section = SectionNode(
                heading=title,
                level=1,
                start_line=1,
                end_line=first_heading_line - 1,
            )
            sections.insert(0, intro_section)

    return NoteToC(
        path=parsed.path,
        title=title,
        summary=summary,
        sections=sections,
        wikilinks=parsed.wikilinks,
        images=parsed.images,
        attachments=parsed.attachments,
        frontmatter=parsed.frontmatter,
        mtime=datetime.fromtimestamp(parsed.path.stat().st_mtime),
    )


def get_section_content(
    content: str,
    section: SectionNode,
) -> str:
    """
    Extract the content of a specific section from markdown.

    Includes the heading and all content until the next sibling/parent heading.
    """
    lines = content.split("\n")
    start = section.start_line - 1  # Convert to 0-indexed
    end = section.end_line if section.end_line else len(lines)
    return "\n".join(lines[start:end])


def resolve_wikilink(
    link: str,
    vault_notes: dict[str, Path],
    resolution: str = "case_insensitive_with_aliases",
    note_aliases: dict[str, list[str]] | None = None,
) -> Path | None:
    """
    Resolve a [[wikilink]] to an actual note path.

    Args:
        link: The wikilink text (without brackets)
        vault_notes: Dict mapping note names/paths to actual paths
        resolution: Resolution strategy
        note_aliases: Dict mapping note paths to their aliases

    Returns:
        Resolved path or None if not found
    """
    # Remove any heading/block references
    link_name = link.split("#")[0].strip()

    if resolution == "strict":
        return vault_notes.get(link_name)

    # Case-insensitive matching
    link_lower = link_name.lower()
    for name, path in vault_notes.items():
        if name.lower() == link_lower:
            return path
        if Path(name).stem.lower() == link_lower:
            return path

    # Alias matching
    if resolution == "case_insensitive_with_aliases" and note_aliases:
        for note_name, aliases in note_aliases.items():
            for alias in aliases:
                if alias.lower() == link_lower:
                    return vault_notes.get(note_name, Path(note_name))

    return None
