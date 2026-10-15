"""
LLM-based summarization for ToC generation.

Generates summaries for notes and sections using the configured LLM.
"""

from archivyst.core.markdown import get_section_content
from archivyst.core.models import NoteToC, SectionNode
from archivyst.llm.client import LLMClient

SECTION_SUMMARY_PROMPT = """\
Summarize the following markdown section in 1-2 sentences. \
Focus on the key concepts, facts, or ideas. Be specific and informative.

Section heading: {heading}
Content:
{content}

Summary:"""


NOTE_SUMMARY_PROMPT = """\
Summarize the following markdown note in 2-3 sentences. \
Capture the main topic, key points, and purpose of the note.

Title: {title}
Content preview:
{content}

Summary:"""


async def summarize_section(
    client: LLMClient,
    heading: str,
    content: str,
    max_content_length: int = 2000,
    max_tokens: int = 150,
) -> str:
    """
    Generate a summary for a single section.

    Args:
        client: LLM client instance
        heading: Section heading text
        content: Section content
        max_content_length: Maximum content length to send to LLM
        max_tokens: Maximum tokens for the summary response
    """
    truncated = content[:max_content_length]
    if len(content) > max_content_length:
        truncated += "\n[...truncated...]"

    prompt = SECTION_SUMMARY_PROMPT.format(
        heading=heading,
        content=truncated,
    )

    response = await client.complete(
        prompt=prompt,
        task="summarization",
        max_tokens=max_tokens,
    )

    return response.strip()


async def summarize_note(
    client: LLMClient,
    title: str,
    content: str,
    max_content_length: int = 3000,
    max_tokens: int = 200,
) -> str:
    """
    Generate a summary for an entire note.

    Args:
        client: LLM client instance
        title: Note title
        content: Full note content
        max_content_length: Maximum content length to send to LLM
        max_tokens: Maximum tokens for the summary response
    """
    truncated = content[:max_content_length]
    if len(content) > max_content_length:
        truncated += "\n[...truncated...]"

    prompt = NOTE_SUMMARY_PROMPT.format(
        title=title,
        content=truncated,
    )

    response = await client.complete(
        prompt=prompt,
        task="summarization",
        max_tokens=max_tokens,
    )

    return response.strip()


async def add_summaries_to_toc(
    client: LLMClient,
    toc: NoteToC,
    content: str,
    summarize_sections: bool = True,
    max_section_chars: int = 2000,
    max_note_chars: int = 3000,
    max_section_tokens: int = 150,
    max_note_tokens: int = 200,
) -> NoteToC:
    """
    Add LLM-generated summaries to a NoteToC.

    Args:
        client: LLM client instance
        toc: NoteToC to enhance with summaries
        content: Full note content
        summarize_sections: Whether to summarize individual sections
        max_section_chars: Max content chars per section
        max_note_chars: Max content chars for note summary
        max_section_tokens: Max response tokens for section summary
        max_note_tokens: Max response tokens for note summary
    """
    # Generate note-level summary
    note_summary = await summarize_note(
        client,
        toc.title,
        content,
        max_content_length=max_note_chars,
        max_tokens=max_note_tokens,
    )
    toc.summary = note_summary

    if not summarize_sections:
        return toc

    # Collect all sections via flatten
    all_sections: list[SectionNode] = []
    for section in toc.sections:
        all_sections.extend(section.flatten())

    if not all_sections:
        return toc

    # Summarize each section individually for reliability
    for section in all_sections:
        section_content = get_section_content(content, section)
        if section_content.strip():
            section.summary = await summarize_section(
                client,
                section.heading,
                section_content,
                max_content_length=max_section_chars,
                max_tokens=max_section_tokens,
            )
        else:
            section.summary = ""

    return toc


async def generate_vault_meta_summary(
    client: LLMClient,
    vault_name: str,
    note_summaries: list[tuple[str, str]],  # (title, summary) pairs
    max_notes: int = 50,
) -> str:
    """
    Generate a meta-summary describing a vault's contents.

    Used for the semantic/vaults/{vault}.md file in agent memory.

    Args:
        client: LLM client instance
        vault_name: Name of the vault
        note_summaries: List of (note title, note summary) pairs
        max_notes: Maximum number of notes to include in prompt
    """
    truncated = note_summaries[:max_notes]

    notes_text = "\n".join(f"- {title}: {summary}" for title, summary in truncated)

    prompt = f"""\
Analyze the following collection of notes from a knowledge vault called "{vault_name}".

Identify:
1. Main topics and themes (3-5)
2. Types of content (e.g., reference, journal, project notes)
3. Notable patterns or structure
4. Key concepts that appear frequently

Notes:
{notes_text}

Provide a structured overview of this vault:"""

    response = await client.complete(
        prompt=prompt,
        task="synthesis",
        max_tokens=500,
    )

    return response.strip()
