"""
Archivyst core module.

Provides vault indexing, markdown parsing, and ToC generation.
"""

from archivyst.core.index import VaultIndex, VaultManager
from archivyst.core.markdown import (
    build_note_toc,
    build_toc_tree,
    extract_headings,
    extract_images,
    extract_wikilinks,
    get_section_content,
    parse_frontmatter,
    parse_note,
    resolve_wikilink,
)
from archivyst.core.models import (
    AttachmentSummary,
    Config,
    ImageDescription,
    IndexStrategy,
    MemoryFormat,
    NoteToC,
    RetrievalResult,
    RetrievalSource,
    RoutingDecision,
    SectionNode,
    SessionState,
    TraversalStep,
    UserContext,
    VaultConfig,
    VaultManifest,
    WikilinkResolution,
)
from archivyst.core.summarizer import (
    add_summaries_to_toc,
    generate_vault_meta_summary,
    summarize_note,
    summarize_section,
)

__all__ = [
    # Index
    "VaultIndex",
    "VaultManager",
    # Markdown
    "build_note_toc",
    "build_toc_tree",
    "extract_headings",
    "extract_images",
    "extract_wikilinks",
    "get_section_content",
    "parse_frontmatter",
    "parse_note",
    "resolve_wikilink",
    # Models
    "AttachmentSummary",
    "Config",
    "ImageDescription",
    "IndexStrategy",
    "MemoryFormat",
    "NoteToC",
    "RetrievalResult",
    "RetrievalSource",
    "RoutingDecision",
    "SectionNode",
    "SessionState",
    "TraversalStep",
    "UserContext",
    "VaultConfig",
    "VaultManifest",
    "WikilinkResolution",
    # Summarizer
    "add_summaries_to_toc",
    "generate_vault_meta_summary",
    "summarize_note",
    "summarize_section",
]
