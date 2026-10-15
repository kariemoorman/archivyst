"""
Archivyst agent module.

Provides the main orchestration layer for query processing.
"""

from archivyst.agent.memory import MemoryStore
from archivyst.agent.orchestrator import ArchivystAgent
from archivyst.agent.prompts import (
    BRIEFING_PROMPT,
    ENTITY_EXTRACTION_PROMPT,
    FOLLOW_UP_PROMPT,
    MEMORY_EXTRACTION_PROMPT,
    QUERY_ANALYSIS_PROMPT,
    SYNTHESIS_PROMPT,
    format_context_for_synthesis,
    format_conversation_for_memory,
)

__all__ = [
    # Orchestrator
    "ArchivystAgent",
    # Memory
    "MemoryStore",
    # Prompts
    "BRIEFING_PROMPT",
    "ENTITY_EXTRACTION_PROMPT",
    "FOLLOW_UP_PROMPT",  # Reserved for future multi-turn use
    "MEMORY_EXTRACTION_PROMPT",
    "QUERY_ANALYSIS_PROMPT",
    "SYNTHESIS_PROMPT",
    "format_context_for_synthesis",
    "format_conversation_for_memory",
]
