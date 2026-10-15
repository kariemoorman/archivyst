"""
Code-to-markdown converter.

Assembles structured markdown from parsed code files, optionally using
an LLM to generate natural language descriptions for each symbol.
Shared by both `archivyst process` (external import) and `archivyst scan`
(in-vault discovery).
"""

import hashlib
import logging
from pathlib import Path
from typing import Any

from archivyst.agent.prompts import CODE_DESCRIPTION_PROMPT, CODE_FILE_SUMMARY_PROMPT
from archivyst.core.code_parser import CodeSymbol, ParsedCodeFile

logger = logging.getLogger(__name__)

# Maximum source code chars sent to LLM per symbol
MAX_SYMBOL_CHARS = 2000

# Maximum symbols to send to LLM for descriptions (rest get signature-only)
MAX_LLM_SYMBOLS = 30


async def convert_code_to_markdown(
    parsed: ParsedCodeFile,
    llm_client: Any | None = None,
    max_tokens: int = 150,
    temperature: float = 0.3,
) -> str:
    """Convert a ParsedCodeFile into structured markdown.

    Args:
        parsed: The parsed code file with extracted symbols.
        llm_client: Optional LLMClient for generating descriptions.
            If None, falls back to docstrings or signature-only output.
        max_tokens: Max tokens for each LLM description call.
        temperature: Temperature for LLM description calls.

    Returns:
        Markdown string ready to be written as a note.
    """
    # Raw fallback — no structure extracted
    if parsed.parse_method == "raw":
        return _render_raw(parsed)

    sections: list[str] = []
    llm_call_count = 0

    # File-level summary
    file_summary = await _generate_file_summary(
        parsed, llm_client, max_tokens=max_tokens, temperature=temperature
    )
    if file_summary:
        sections.append(file_summary)

    # Imports section (wrapped in code block to prevent wikilink insertion)
    if parsed.imports:
        imports_list = "\n".join(parsed.imports)
        sections.append(f"## Imports\n\n```\n{imports_list}\n```")

    # Symbols
    for symbol in parsed.symbols:
        heading_level = "##"
        heading = f"{heading_level} {_format_kind(symbol.kind)}: `{symbol.signature}`"

        # Description
        description = ""
        if llm_client and llm_call_count < MAX_LLM_SYMBOLS:
            description = await _describe_symbol(
                parsed.language, symbol, llm_client, max_tokens, temperature
            )
            llm_call_count += 1
        elif symbol.docstring:
            description = symbol.docstring.strip()

        section = heading
        if description:
            section += f"\n\n{description}"

        # Child symbols (methods inside a class)
        for child in symbol.children:
            child_heading = f"### `{child.signature}`"
            child_desc = ""
            if llm_client and llm_call_count < MAX_LLM_SYMBOLS:
                child_desc = await _describe_symbol(
                    parsed.language, child, llm_client, max_tokens, temperature
                )
                llm_call_count += 1
            elif child.docstring:
                child_desc = child.docstring.strip()

            section += f"\n\n{child_heading}"
            if child_desc:
                section += f"\n\n{child_desc}"

        sections.append(section)

    return "\n\n".join(sections)


def compute_source_hash(source: Path) -> str:
    """Compute SHA-256 hash of a file's contents.

    Returns the full hex digest (no truncation).
    """
    hasher = hashlib.sha256()
    try:
        with open(source, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
    except OSError as e:
        logger.warning("[code_converter] Cannot hash %s: %s", source, e)
        return ""
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_raw(parsed: ParsedCodeFile) -> str:
    """Render a raw (unparsed) code file as a fenced code block."""
    try:
        content = parsed.filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        content = "(unable to read file)"

    return f"```{parsed.language}\n{content}\n```"


def _format_kind(kind: str) -> str:
    """Capitalize a symbol kind for use in headings."""
    return kind.capitalize()


async def _generate_file_summary(
    parsed: ParsedCodeFile,
    llm_client: Any | None,
    max_tokens: int = 150,
    temperature: float = 0.3,
) -> str:
    """Generate a one-sentence file summary."""
    # Try LLM first
    if llm_client and parsed.symbols:
        elements = ", ".join(f"{s.kind} `{s.name}`" for s in parsed.symbols[:20])
        prompt = CODE_FILE_SUMMARY_PROMPT.format(
            language=parsed.language,
            elements=elements,
            filename=parsed.filepath.name,
        )
        try:
            summary: str = await llm_client.complete(
                prompt=prompt,
                task="memory",
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return summary.strip()
        except Exception as e:
            logger.warning(
                "[code_converter] File summary generation failed for %s: %s",
                parsed.filepath.name,
                e,
            )

    # Fallback to file docstring
    if parsed.file_docstring:
        return parsed.file_docstring.strip()

    return ""


async def _describe_symbol(
    language: str,
    symbol: CodeSymbol,
    llm_client: Any,
    max_tokens: int = 150,
    temperature: float = 0.3,
) -> str:
    """Generate an LLM description for a single code symbol."""
    code = symbol.source_code
    if len(code) > MAX_SYMBOL_CHARS:
        code = code[:MAX_SYMBOL_CHARS] + "\n# ... (truncated)"

    prompt = CODE_DESCRIPTION_PROMPT.format(
        language=language,
        kind=symbol.kind,
        name=symbol.name,
        signature=symbol.signature,
        code=code,
    )

    try:
        description: str = await llm_client.complete(
            prompt=prompt,
            task="memory",
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return description.strip()
    except Exception as e:
        logger.warning(
            "[code_converter] Description failed for %s %s: %s",
            symbol.kind,
            symbol.name,
            e,
        )
        # Fall back to docstring
        if symbol.docstring:
            return symbol.docstring.strip()
        return ""
