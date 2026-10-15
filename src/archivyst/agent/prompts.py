"""
Prompt templates for the Archivyst agent.
"""

from collections.abc import Sequence

from archivyst.core.models import ConversationTurn, RetrievalResult

BRIEFING_PROMPT = """\
You are Archivyst, an intelligent archivist and analyst for Obsidian vaults. \
You help users navigate, understand, and learn from their knowledge base.

When the user says "the vault", "the docs", or "the knowledge base", \
they mean their Obsidian notes — not internal system directories.

Here is your current memory state for this session. Review it and respond \
with "ready" to confirm you've received the context.

{memory_context}"""


QUERY_ANALYSIS_PROMPT = """\
Analyze the following user query to determine the best retrieval strategy.

Important: "The vault", "the docs", or "the knowledge base" refers to \
the user's Obsidian notes -- the markdown files in their project vaults. \
These are NOT internal system directories or memory stores.

Query: {query}

Active Vaults: {active_vaults}

User Context:
{user_context}

Determine:
1. Query type: Is this factual (looking for specific info), \
exploratory (browsing/learning), procedural (how-to), \
or conversational (about past interactions)?
2. Scope hint: Which vault(s) are most likely to contain relevant \
information?
3. Needs memory: Should we check episodic memory (past conversations) \
or semantic memory (learned facts)?

Respond in JSON:
{{
    "query_type": "factual|exploratory|procedural|conversational",
    "scope_hint": ["vault1", "vault2"],
    "needs_memory": "true or false",
    "reasoning": "Brief explanation"
}}

JSON Response:"""


SYNTHESIS_PROMPT = """\
The user is asking a question. Answer based primarily on the retrieved \
context below and your session memory. Cite sources by note title. \
If the question is not covered by the retrieved context, state that clearly. \
If you need to infer or extrapolate, say so explicitly. \
Be concise but complete.

User Query: {query}

Retrieved Context:
{context}

Answer:"""


SYNTHESIS_PROMPT_LAX = """\
The user is asking a question. You have access to retrieved context from \
their knowledge vault and session memory. Use this context if it is relevant \
to the query, but also draw on your own knowledge to provide a complete answer. \
When citing vault sources, reference them by note title. When answering from \
your own knowledge beyond what the vault contains, note that explicitly. \
Be concise but complete.

User Query: {query}

Retrieved Context:
{context}

Answer:"""


MEMORY_EXTRACTION_PROMPT = """\
Analyze this conversation and extract any information worth \
remembering for future sessions. Only extract items with lasting \
value — skip ephemeral questions, greetings, or obvious information.

Conversation:
{conversation}

Extract:
1. User preferences mentioned (how they like things done)
2. Facts about the user (role, interests, projects)
3. Corrections or clarifications the user made
4. Patterns in how the user asks questions

Respond in JSON:
{{
    "preferences": ["preference1", "preference2"],
    "facts": ["fact1", "fact2"],
    "corrections": ["correction1"],
    "patterns": ["pattern1"]
}}

JSON Response:"""


ENTITY_EXTRACTION_PROMPT = """\
Extract notable entities from this text that might be worth tracking.

Text:
{text}

Source: {source}

Identify:
- People mentioned (with roles/relationships if stated)
- Projects or initiatives
- Key concepts or terms specific to this knowledge base

Respond in JSON:
{{
    "people": [
        {{"name": "...", "role": "...", "context": "..."}}
    ],
    "projects": [
        {{"name": "...", "description": "...", "status": "..."}}
    ],
    "concepts": [
        {{"term": "...", "definition": "..."}}
    ]
}}

JSON Response:"""


FOLLOW_UP_PROMPT = """\
The user is asking a follow-up question in an ongoing conversation.

Previous context:
{previous_context}

New question: {query}

Retrieved additional context:
{new_context}

Provide a response that:
1. Acknowledges the conversational context
2. Integrates new information with what was discussed before
3. Answers the follow-up question directly

Response:"""


def format_context_for_synthesis(retrieval: RetrievalResult) -> str:
    """
    Format retrieval results as context for synthesis prompt.

    Groups content by source note for readability.
    """
    if not retrieval.content:
        return "(No relevant content found)"

    lines: list[str] = []

    # Group by note
    notes: dict[str, list[tuple[str, str]]] = {}
    for key, content in retrieval.content.items():
        parts = key.split(":", 2)
        if len(parts) >= 2:
            vault_note = f"{parts[0]}:{parts[1]}"
            section = parts[2] if len(parts) > 2 else "content"
        else:
            vault_note = key
            section = "content"

        if vault_note not in notes:
            notes[vault_note] = []
        notes[vault_note].append((section, content))

    # Format
    for vault_note, sections in notes.items():
        lines.append(f"### Source: {vault_note}")
        lines.append("")

        for section, content in sections:
            if section != "content" and section != "summary":
                lines.append(f"**{section}**")
            lines.append(content.strip())
            lines.append("")

    # Add reasoning trace summary
    if retrieval.reasoning_trace:
        lines.append("---")
        lines.append(
            "*Retrieval path: " + " → ".join(retrieval.reasoning_trace[-3:]) + "*"
        )

    return "\n".join(lines)


ConversationItem = ConversationTurn | dict[str, str]


def format_conversation_for_memory(
    conversation: Sequence[ConversationItem],
) -> str:
    """Format a conversation for memory extraction.

    Accepts list[dict[str, str]] or list[ConversationTurn].
    """
    lines: list[str] = []

    for turn in conversation:
        if isinstance(turn, ConversationTurn):
            role = turn.role.capitalize()
            content = turn.content
        else:
            role = turn.get("role", "unknown").capitalize()
            content = turn.get("content", "")
        lines.append(f"{role}: {content}")
        lines.append("")

    return "\n".join(lines)


PROMOTION_SUGGESTION_PROMPT = """\
Analyze these session notes and suggest what should be promoted to long-term memory.

Session Notes:
{session_content}

For each item worth remembering, specify:
- target: preference, entity_people, entity_project, entity_concept, fact, pattern, or correction
- key: a short name for the item
- value: the content to store

Only suggest items with lasting value. Skip ephemeral questions, greetings, or obvious information.

Respond in JSON:
{{
    "suggestions": [
        {{"target": "...", "key": "...", "value": "..."}}
    ]
}}

JSON Response:"""


MEMORY_ORGANIZE_PROMPT = """\
Review these memory entries for quality and organization.

Entries:
{entries}

Known entities (use these exact names in relationships):
{entity_names}

Identify:
1. Duplicate entries (same entity or fact in different files)
2. Conflicting information between entries
3. Entries that appear outdated or stale
4. Related entries that should be merged
5. Relationships between known entities listed above

For each issue found (items 1-4), specify an action:
- merge: combine entries (specify which files)
- delete: remove redundant entry (specify file)
- update: entry needs refreshing (specify file and what changed)
- flag: needs human review (specify file and concern)

For relationships found (item 5), use ONLY the exact entity names \
listed above in wikilink syntax:
- [[entity-name-a]] relationship [[entity-name-b]]
Do NOT invent new entity names or rephrase existing ones.

Respond in JSON:
{{
    "actions": [
        {{"action": "merge|delete|update|flag", "files": ["..."], "reason": "..."}}
    ],
    "relationships": [
        "[[Entity A]] leads [[Entity B]]",
        "[[Entity C]] depends on [[Entity D]]"
    ]
}}

JSON Response:"""


REMEMBER_CLASSIFY_PROMPT = """\
The user wants to remember the following information. Classify it \
into the most appropriate memory category.

Text: {text}

Categories:
- preference: how the user likes things done (style, format, workflow choices)
- fact: information about a person, project, concept, or domain
- pattern: a recurring behavior, workflow, or approach

Respond in JSON:
{{
    "type": "preference|fact|pattern",
    "key": "short descriptive name",
    "value": "the full content to store"
}}

JSON Response:"""


STRUCTURE_PARA_INSTRUCTION = """\
Classify each file into one of these four PARA categories:
- projects: active work with deadlines or deliverables
- areas: ongoing responsibilities maintained continuously
- resources: reference material, topics of interest
- archive: completed or inactive items"""

STRUCTURE_TOPIC_INSTRUCTION = """\
Group these files by topic or theme. Create 3-8 topic folders \
that best organize the content. Use clear, descriptive folder names."""

STRUCTURE_FILES_PROMPT = """\
You are organizing files into a vault folder structure.

Files:
{files}

{strategy_instruction}

Respond in JSON:
{{
    "folders": {{
        "folder-name": ["file1.md", "file2.md"],
        "another-folder": ["file3.md"]
    }}
}}

JSON Response:"""


LOBOTOMY_SCAN_PROMPT = """\
The user wants to remove all memory related to a specific topic \
from the agent's memory.

Topic to excise: {topic}

Below are the memory files and their contents. For each file, \
determine if it contains information related to the topic. \
Include files that:
- Directly mention the topic
- Reference entities, facts, or patterns related to the topic
- Contain conversations or events about the topic

Memory files:
{files}

Respond in JSON:
{{
    "files_to_delete": [
        {{"path": "relative/path/to/file", "reason": "why this file relates to the topic"}}
    ],
    "files_to_edit": [
        {{"path": "relative/path/to/file", \
"content_to_remove": "description of what to remove", \
"reason": "why"}}
    ]
}}

JSON Response:"""


# ---------------------------------------------------------------------------
# Code processing prompts
# ---------------------------------------------------------------------------

CODE_DESCRIPTION_PROMPT = """\
Describe this {language} code element in 1-2 sentences of plain English. \
Focus on what it does, its inputs/outputs, and whether it is a public API \
or internal helper.

{kind}: `{name}`
Signature: `{signature}`

```{language}
{code}
```

Description:"""


CODE_FILE_SUMMARY_PROMPT = """\
Summarize the purpose of this {language} source file in one sentence. \
The file contains these elements: {elements}

File: {filename}

Summary:"""


# ---------------------------------------------------------------------------
# Contradiction detection prompts
# ---------------------------------------------------------------------------

CONTRADICTION_CHECK_PROMPT = """\
You are checking for contradictions between new information and \
existing memory entries. A contradiction is when new information \
directly conflicts with, reverses, or invalidates existing information. \
Do NOT flag additions, elaborations, or complementary details as \
contradictions.

New information:
{new_content}

Existing memory entries that may conflict:
{existing_entries}

Identify any contradictions. For each, specify the conflicting texts \
and classify the type.

Return JSON:
[
  {{
    "new_text": "the conflicting part of the new information",
    "existing_source": "path/to/existing/file.md",
    "existing_text": "the conflicting part from existing memory",
    "type": "factual|preference|pattern|entity",
    "explanation": "brief description of the conflict"
  }}
]

If no contradictions found, return: []

JSON:"""


CONTRADICTION_RESOLVE_PROMPT = """\
The user wants to resolve a contradiction in their knowledge base.

Contradiction:
  Source A ({source_a}): {text_a}
  Source B ({source_b}): {text_b}
  Type: {conflict_type}

User's input: {user_message}

Based on the user's input, determine how to resolve this contradiction. \
Provide the updated content for the affected files and a brief summary \
of the resolution decision.

Return JSON:
{{
  "updates": [
    {{"path": "path/to/file.md", "old_text": "text to replace", \
"new_text": "replacement text"}}
  ],
  "summary": "brief description of what was resolved"
}}

JSON:"""
