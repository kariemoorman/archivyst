"""
ToC tree traversal for Archivyst retrieval.

Uses LLM to navigate ToC trees and select relevant sections.
"""

import json

from archivyst.core.markdown import get_section_content
from archivyst.core.models import NoteToC, SectionNode, TraversalStep
from archivyst.llm.client import LLMClient

TRAVERSAL_PROMPT = """\
You are navigating a document's table of contents to find information relevant to a query.

Query: {query}

Document: {document_title}
Document Summary: {document_summary}

Current Location: {current_location}

Available Sections:
{sections}

For each section, decide one of:
- "expand": This section likely contains relevant information, explore its subsections
- "select": This section directly answers the query, retrieve its content
- "skip": This section is not relevant

Respond in JSON format:
{{
    "decisions": [
        {{"section": "Section Name", "action": "expand|select|skip", "reason": "brief reason"}}
    ]
}}

JSON Response:"""


SECTION_RELEVANCE_PROMPT = """\
Given a query and a document section, determine if this section \
contains information relevant to answering the query.

Query: {query}

Section Heading: {heading}
Section Content:
{content}

Respond with JSON:
{{
    "relevant": true/false,
    "relevance_score": 0.0-1.0,
    "key_information": "brief summary of relevant info, or null if not relevant"
}}

JSON Response:"""


def format_sections_for_prompt(
    sections: list[SectionNode],
    indent: int = 0,
) -> str:
    """Format sections as an indented list for the traversal prompt."""
    lines: list[str] = []
    prefix = "  " * indent

    for section in sections:
        summary = f" - {section.summary}" if section.summary else ""
        has_children = " [has subsections]" if section.children else ""
        lines.append(f"{prefix}- {section.heading}{summary}{has_children}")

    return "\n".join(lines)


def parse_traversal_response(
    response: str,
    sections: list[SectionNode],
) -> list[tuple[SectionNode, str, str]]:
    """
    Parse traversal response into (section, action, reason) tuples.

    Returns:
        List of (SectionNode, action, reason) tuples
    """
    try:
        # Extract JSON
        if "```json" in response:
            start = response.index("```json") + 7
            end = response.index("```", start)
            response = response[start:end]
        elif "```" in response:
            start = response.index("```") + 3
            end = response.index("```", start)
            response = response[start:end]

        data = json.loads(response.strip())
        decisions = data.get("decisions", [])

        # Map decisions to sections
        section_map = {s.heading.lower(): s for s in sections}
        results: list[tuple[SectionNode, str, str]] = []

        for decision in decisions:
            section_name = decision.get("section", "").lower()
            action = decision.get("action", "skip")
            reason = decision.get("reason", "")

            # Find matching section
            if section_name in section_map:
                results.append((section_map[section_name], action, reason))
            else:
                # Fuzzy match
                for name, section in section_map.items():
                    if section_name in name or name in section_name:
                        results.append((section, action, reason))
                        break

        return results

    except (json.JSONDecodeError, ValueError):
        # Fallback: select all sections
        return [(s, "select", "fallback") for s in sections]


async def traverse_toc(
    client: LLMClient,
    query: str,
    toc: NoteToC,
    content: str,
    max_depth: int = 3,
) -> list[TraversalStep]:
    """
    Traverse a note's ToC tree to find relevant sections.

    Args:
        client: LLM client instance
        query: User query
        toc: Note's table of contents
        content: Full note content
        max_depth: Maximum traversal depth

    Returns:
        List of traversal steps taken
    """
    steps: list[TraversalStep] = []
    selected_sections: list[SectionNode] = []

    async def traverse_level(
        sections: list[SectionNode],
        path: list[str],
        depth: int,
    ) -> None:
        if depth > max_depth or not sections:
            return

        # Build prompt
        current_location = " > ".join(path) if path else "(document root)"
        sections_text = format_sections_for_prompt(sections)

        prompt = TRAVERSAL_PROMPT.format(
            query=query,
            document_title=toc.title,
            document_summary=toc.summary or "(no summary)",
            current_location=current_location,
            sections=sections_text,
        )

        response = await client.complete(
            prompt=prompt,
            task="routing",
            max_tokens=500,
            temperature=0.3,
        )

        decisions = parse_traversal_response(response, sections)

        for section, action, reason in decisions:
            step = TraversalStep(
                note_path=toc.path,
                section_path=path + [section.heading],
                action=action,
                reasoning=reason,
            )
            steps.append(step)

            if action == "select":
                selected_sections.append(section)
            elif action == "expand" and section.children:
                await traverse_level(
                    section.children,
                    path + [section.heading],
                    depth + 1,
                )

    # Start traversal from root
    if toc.sections:
        await traverse_level(toc.sections, [], 0)

    return steps


async def evaluate_section_relevance(
    client: LLMClient,
    query: str,
    section: SectionNode,
    content: str,
) -> tuple[bool, float, str | None]:
    """
    Evaluate if a section is relevant to a query.

    Args:
        client: LLM client
        query: User query
        section: Section to evaluate
        content: Full note content

    Returns:
        Tuple of (is_relevant, score, key_information)
    """
    section_content = get_section_content(content, section)

    # Truncate for prompt
    max_content = 2000
    truncated = section_content[:max_content]
    if len(section_content) > max_content:
        truncated += "\n[...truncated...]"

    prompt = SECTION_RELEVANCE_PROMPT.format(
        query=query,
        heading=section.heading,
        content=truncated,
    )

    response = await client.complete(
        prompt=prompt,
        task="routing",
        max_tokens=200,
        temperature=0.2,
    )

    try:
        # Extract JSON
        if "```" in response:
            start = response.index("```") + 3
            if response[start : start + 4] == "json":
                start += 4
            end = response.index("```", start)
            response = response[start:end]

        data = json.loads(response.strip())

        return (
            data.get("relevant", False),
            float(data.get("relevance_score", 0.0)),
            data.get("key_information"),
        )
    except (json.JSONDecodeError, ValueError):
        # Fallback: assume relevant if section summary matches query words
        query_words = set(query.lower().split())
        section_words = set(section.heading.lower().split())
        if section.summary:
            section_words.update(section.summary.lower().split())

        overlap = len(query_words & section_words)
        score = min(overlap / max(len(query_words), 1), 1.0)

        return score > 0.2, score, None


async def extract_relevant_content(
    client: LLMClient,
    query: str,
    toc: NoteToC,
    content: str,
    steps: list[TraversalStep],
) -> dict[str, str]:
    """
    Extract content from sections marked as 'select' in traversal.

    Returns:
        Dict mapping section paths to content
    """
    extracted: dict[str, str] = {}

    # Build section lookup
    all_sections: dict[str, SectionNode] = {}

    def collect_sections(sections: list[SectionNode], path: list[str]) -> None:
        for section in sections:
            current_path = path + [section.heading]
            key = " > ".join(current_path)
            all_sections[key] = section
            collect_sections(section.children, current_path)

    collect_sections(toc.sections, [])

    # Extract content for selected sections
    for step in steps:
        if step.action == "select":
            key = " > ".join(step.section_path)
            if key in all_sections:
                section = all_sections[key]
                section_content = get_section_content(content, section)
                extracted[key] = section_content

    return extracted
