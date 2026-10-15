"""
Shared entity and memory extraction helpers.

Contains functions for saving LLM-extracted entities and memory items
to a MemoryStore, used by the orchestrator and CLI commands.
Includes contradiction detection against existing memory.
"""

import logging
from typing import Any

from pydantic import ValidationError

from archivyst.agent.memory import MemoryStore
from archivyst.agent.prompts import CONTRADICTION_CHECK_PROMPT
from archivyst.core.models import EntityExtractionResponse, MemoryExtractionResponse

logger = logging.getLogger(__name__)


def save_extracted_entities(memory: MemoryStore, data: dict[str, Any]) -> int:
    """Save extracted entities from validated LLM response data. Returns count saved."""
    try:
        validated = EntityExtractionResponse.model_validate(data)
    except ValidationError as e:
        logger.warning("[extraction] Invalid entity extraction response: %s", e)
        return 0

    count = 0
    for person in validated.people:
        name = person.get("name", "")
        if name.strip():
            memory.save_entity("people", name, person)
            count += 1
    for project in validated.projects:
        name = project.get("name", "")
        if name.strip():
            memory.save_entity("projects", name, project)
            count += 1
    for concept in validated.concepts:
        term = concept.get("term", "")
        if term.strip():
            memory.save_entity("concepts", term, concept)
            count += 1
    return count


def mark_session_promoted(
    memory: MemoryStore,
    session_id: str,
) -> None:
    """Mark a working session as promoted in the manifest.

    Per-turn extraction already saved entities/facts/patterns to project
    memory during the session. This function only marks the session so it
    won't be re-processed during prune or query-time search.

    Args:
        memory: MemoryStore instance
        session_id: ID of the session to mark
    """
    memory.mark_sessions_promoted([session_id])


def save_extracted_memory(memory: MemoryStore, data: dict[str, Any]) -> int:
    """Save extracted preferences/facts/corrections/patterns. Returns count saved."""
    try:
        validated = MemoryExtractionResponse.model_validate(data)
    except ValidationError as e:
        logger.warning("[extraction] Invalid memory extraction response: %s", e)
        return 0

    count = 0
    for pref in validated.preferences:
        if pref.strip():
            memory.save_user_preference(pref, "")
            count += 1
    for fact in validated.facts:
        if fact.strip():
            safe_key = fact[:50].lower().replace(" ", "-")
            memory.save_fact(safe_key, fact)
            count += 1
    for correction in validated.corrections:
        if correction.strip():
            memory.save_correction(correction, "auto-extracted")
            count += 1
    for pattern in validated.patterns:
        if pattern.strip():
            safe_key = pattern[:50].lower().replace(" ", "-")
            memory.save_pattern(safe_key, pattern)
            count += 1
    return count


async def check_contradictions(
    memory: MemoryStore,
    new_content: str,
    llm_client: Any,
    context: str = "",
    max_tokens: int = 500,
    temperature: float = 0.3,
) -> list[str]:
    """Check new content for contradictions against existing memory.

    Finds related existing entries, sends them to the LLM along with
    the new content, and catalogs any contradictions found.

    Args:
        memory: MemoryStore instance.
        new_content: The newly extracted text to check.
        llm_client: LLM client with .complete() method.
        context: Description of where this was detected (e.g., "session turn 3").
        max_tokens: Max response tokens for LLM.
        temperature: LLM temperature.

    Returns:
        List of contradiction IDs created.
    """
    from archivyst.llm.utils import parse_json_response

    # Find related existing entries
    related = memory.get_related_entries(new_content)

    # Always include relationships.md regardless of keyword overlap
    relationships = memory.read("semantic/relationships.md")
    if relationships and relationships.strip():
        # Check if already included by get_related_entries
        rel_paths = {path for path, _ in related}
        if "semantic/relationships.md" not in rel_paths:
            preview = relationships[:300].replace("\n", " ")
            related.append(("semantic/relationships.md", preview))

    if not related:
        return []

    existing_text = "\n\n".join(
        f"[{path}]: {preview}" for path, preview in related[:10]
    )

    prompt = CONTRADICTION_CHECK_PROMPT.format(
        new_content=new_content,
        existing_entries=existing_text,
    )

    try:
        response: str = await llm_client.complete(
            prompt=prompt,
            task="memory",
            max_tokens=max_tokens,
            temperature=temperature,
        )
        matches = parse_json_response(response)
    except Exception as e:
        logger.warning("[extraction] Contradiction check failed: %s", e)
        return []

    if not isinstance(matches, list):
        return []

    contradiction_ids: list[str] = []
    for match in matches:
        if not isinstance(match, dict):
            continue

        new_text = match.get("new_text", "")
        existing_source = match.get("existing_source", "")
        existing_text_match = match.get("existing_text", "")
        conflict_type = match.get("type", "factual")

        if not new_text or not existing_source:
            continue

        # Determine scope from the existing source path
        scope = "semantic"
        if existing_source.startswith("procedural"):
            scope = "procedural"
        elif existing_source.startswith("user"):
            scope = "user"

        c_id = memory.save_contradiction(
            scope=scope,
            source_a_path=existing_source,
            source_a_text=existing_text_match,
            source_b_path="(new extraction)",
            source_b_text=new_text,
            conflict_type=conflict_type,
            context=context,
        )
        contradiction_ids.append(c_id)

    return contradiction_ids
