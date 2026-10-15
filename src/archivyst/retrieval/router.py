"""
Multi-note routing for Archivyst retrieval.

Uses LLM to decide which notes are relevant to a query.
"""

import json
import re
from pathlib import Path

from archivyst.core.models import NoteToC, RoutingDecision
from archivyst.llm.client import LLMClient

ROUTING_PROMPT = """\
You are a document routing assistant. Given a user query and a list of \
available documents with their summaries, select the most relevant \
documents to answer the query.

User Query: {query}

Available Documents:
{documents}

Instructions:
1. Analyze the query to understand what information is needed
2. Select 1-5 documents most likely to contain the answer
3. Explain your reasoning briefly

Respond in JSON format:
{{
    "selected_documents": ["path1", "path2", ...],
    "reasoning": "Brief explanation of why these documents were selected",
    "confidence": 0.0-1.0
}}

JSON Response:"""


ROUTING_WITH_FOLDERS_PROMPT = """\
You are a document routing assistant. Given a user query and a folder \
structure with document summaries, select the most relevant documents.

User Query: {query}

Folder Structure:
{folder_structure}

Instructions:
1. First identify which folders are most relevant to the query
2. Then select 1-5 specific documents from those folders
3. Prioritize documents whose summaries directly relate to the query

Respond in JSON format:
{{
    "selected_documents": ["folder/path1.md", "folder/path2.md", ...],
    "relevant_folders": ["folder1", "folder2"],
    "reasoning": "Brief explanation",
    "confidence": 0.0-1.0
}}

JSON Response:"""


def format_documents_flat(tocs: list[NoteToC], vault_path: Path) -> str:
    """Format ToCs as a flat list for the routing prompt."""
    lines: list[str] = []
    for toc in tocs:
        rel_path = str(toc.path.relative_to(vault_path))
        summary = toc.summary or "(no summary)"
        lines.append(f"- {rel_path}: {summary}")
    return "\n".join(lines)


def format_documents_by_folder(
    tocs: list[NoteToC],
    vault_path: Path,
) -> str:
    """Format ToCs grouped by folder for the routing prompt."""
    # Group by folder
    folders: dict[str, list[tuple[str, str]]] = {}
    for toc in tocs:
        rel_path = toc.path.relative_to(vault_path)
        folder = str(rel_path.parent) if rel_path.parent != Path(".") else "(root)"
        filename = rel_path.name
        summary = toc.summary or "(no summary)"

        if folder not in folders:
            folders[folder] = []
        folders[folder].append((filename, summary))

    # Format
    lines: list[str] = []
    for folder, docs in sorted(folders.items()):
        lines.append(f"\n## {folder}/")
        for filename, summary in docs:
            lines.append(f"  - {filename}: {summary}")

    return "\n".join(lines)


def parse_routing_response(response: str) -> RoutingDecision:
    """Parse the LLM routing response into a RoutingDecision."""
    # Try to extract JSON from response
    try:
        # Handle markdown code blocks
        if "```json" in response:
            start = response.index("```json") + 7
            end = response.index("```", start)
            response = response[start:end]
        elif "```" in response:
            start = response.index("```") + 3
            end = response.index("```", start)
            response = response[start:end]

        data = json.loads(response.strip())

        selected = data.get("selected_documents", [])
        reasoning = data.get("reasoning", "")
        confidence = float(data.get("confidence", 0.5))

        return RoutingDecision(
            selected_notes=[Path(p) for p in selected],
            reasoning=reasoning,
            confidence=min(max(confidence, 0.0), 1.0),
        )

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        # Fallback: try to extract paths from text
        paths: list[Path] = []
        for line in response.split("\n"):
            if ".md" in line:
                # Extract path-like strings
                matches = re.findall(r"[\w/\-_.]+\.md", line)
                paths.extend(Path(m) for m in matches)

        return RoutingDecision(
            selected_notes=paths[:5],
            reasoning=f"Parsed from text (JSON parse failed: {e})",
            confidence=0.3,
        )


async def route_query(
    client: LLMClient,
    query: str,
    tocs: list[NoteToC],
    vault_path: Path,
    use_folder_structure: bool = True,
    max_documents: int = 50,
) -> RoutingDecision:
    """
    Route a query to relevant documents using LLM.

    Args:
        client: LLM client instance
        query: User query
        tocs: List of available note ToCs
        vault_path: Root path of the vault
        use_folder_structure: Whether to group documents by folder
        max_documents: Maximum documents to include in prompt

    Returns:
        RoutingDecision with selected documents
    """
    # Limit documents for prompt size
    limited_tocs = tocs[:max_documents]

    if use_folder_structure:
        docs_text = format_documents_by_folder(limited_tocs, vault_path)
        prompt = ROUTING_WITH_FOLDERS_PROMPT.format(
            query=query,
            folder_structure=docs_text,
        )
    else:
        docs_text = format_documents_flat(limited_tocs, vault_path)
        prompt = ROUTING_PROMPT.format(
            query=query,
            documents=docs_text,
        )

    response = await client.complete(
        prompt=prompt,
        task="routing",
        max_tokens=500,
        temperature=0.3,  # Lower temperature for more deterministic routing
    )

    return parse_routing_response(response)


async def route_query_multi_vault(
    client: LLMClient,
    query: str,
    vault_tocs: dict[str, list[NoteToC]],  # vault_name -> tocs
    vault_paths: dict[str, Path],  # vault_name -> path
) -> dict[str, RoutingDecision]:
    """
    Route a query across multiple vaults.

    Returns a RoutingDecision per vault.
    """
    results: dict[str, RoutingDecision] = {}

    for vault_name, tocs in vault_tocs.items():
        if not tocs:
            continue

        vault_path = vault_paths[vault_name]
        decision = await route_query(
            client,
            query,
            tocs,
            vault_path,
        )

        # Tag paths with vault name
        decision.selected_notes = [
            Path(f"{vault_name}:{p}") for p in decision.selected_notes
        ]
        results[vault_name] = decision

    return results


def merge_routing_decisions(
    decisions: dict[str, RoutingDecision],
    max_total: int = 5,
) -> RoutingDecision:
    """
    Merge routing decisions from multiple vaults.

    Selects top documents by confidence-weighted ranking.
    """
    all_notes: list[tuple[Path, float]] = []
    all_reasoning: list[str] = []

    for vault_name, decision in decisions.items():
        for note in decision.selected_notes:
            all_notes.append((note, decision.confidence))
        all_reasoning.append(f"[{vault_name}] {decision.reasoning}")

    # Sort by confidence and take top N
    all_notes.sort(key=lambda x: x[1], reverse=True)
    selected = [note for note, _ in all_notes[:max_total]]

    avg_confidence = (
        sum(d.confidence for d in decisions.values()) / len(decisions)
        if decisions
        else 0.0
    )

    return RoutingDecision(
        selected_notes=selected,
        reasoning=" | ".join(all_reasoning),
        confidence=avg_confidence,
    )
