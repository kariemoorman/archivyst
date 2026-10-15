"""
Archivyst retrieval module.

Provides LLM-based document routing, ToC traversal, and wikilink expansion.
"""

from archivyst.retrieval.linker import (
    WikilinkGraph,
    expand_with_links,
    score_by_centrality,
)
from archivyst.retrieval.router import (
    format_documents_by_folder,
    format_documents_flat,
    merge_routing_decisions,
    route_query,
    route_query_multi_vault,
)
from archivyst.retrieval.traverser import (
    evaluate_section_relevance,
    extract_relevant_content,
    traverse_toc,
)

__all__ = [
    # Linker
    "WikilinkGraph",
    "expand_with_links",
    "score_by_centrality",
    # Router
    "format_documents_by_folder",
    "format_documents_flat",
    "merge_routing_decisions",
    "route_query",
    "route_query_multi_vault",
    # Traverser
    "evaluate_section_relevance",
    "extract_relevant_content",
    "traverse_toc",
]
