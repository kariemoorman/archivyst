"""
Prompt builder for composing LLM prompts with automatic
context truncation and section management.
"""

from archivyst.core.models import Config, RetrievalTokenEntry


class PromptBuilder:
    """Compose LLM prompts with context budget management.

    Reads limits from Config.token_allocation.retrieval and automatically
    truncates content to fit within the model's context window.

    Example:
        prompt = (
            PromptBuilder(config)
            .system("You are Archivyst...")
            .context(retrieval_context)
            .query(user_query)
            .instruction("Cite sources by note title.")
            .build()
        )
    """

    def __init__(self, config: Config | None = None):
        if config:
            self._retrieval = config.token_allocation.retrieval
        else:
            self._retrieval = RetrievalTokenEntry()

        self._system_text: str | None = None
        self._context_text: str | None = None
        self._query_text: str | None = None
        self._instructions: list[str] = []
        self._sections: list[tuple[str, str]] = []
        self._suffix: str | None = None

    def system(self, text: str) -> "PromptBuilder":
        """Set the system/persona instruction."""
        self._system_text = text
        return self

    def context(self, text: str, max_chars: int | None = None) -> "PromptBuilder":
        """Add retrieved context with automatic truncation.

        Uses config.retrieval.max_context_chars if max_chars not specified.
        """
        limit = max_chars or self._retrieval.max_context_chars
        if len(text) > limit:
            text = text[:limit] + "\n\n[...context truncated...]"
        self._context_text = text
        return self

    def query(self, text: str, max_chars: int | None = None) -> "PromptBuilder":
        """Add the user query with automatic truncation.

        Uses config.retrieval.max_query_length if max_chars not specified.
        """
        limit = max_chars or self._retrieval.max_query_length
        if len(text) > limit:
            text = text[:limit]
        self._query_text = text
        return self

    def instruction(self, text: str) -> "PromptBuilder":
        """Add an instruction line."""
        self._instructions.append(text)
        return self

    def section(self, heading: str, content: str) -> "PromptBuilder":
        """Add a named section."""
        self._sections.append((heading, content))
        return self

    def suffix(self, text: str) -> "PromptBuilder":
        """Set the prompt suffix (e.g., 'Answer:', 'JSON Response:')."""
        self._suffix = text
        return self

    def build(self) -> str:
        """Assemble the final prompt string."""
        parts: list[str] = []

        if self._system_text:
            parts.append(self._system_text)

        for heading, content in self._sections:
            parts.append(f"{heading}:\n{content}")

        if self._query_text:
            parts.append(f"User Query: {self._query_text}")

        if self._context_text:
            parts.append(f"Retrieved Context:\n{self._context_text}")

        if self._instructions:
            parts.append(
                "Instructions:\n"
                + "\n".join(
                    f"{i}. {inst}" for i, inst in enumerate(self._instructions, 1)
                )
            )

        if self._suffix:
            parts.append(self._suffix)

        return "\n\n".join(parts)

    @property
    def max_briefing_chars(self) -> int:
        """Maximum characters for memory briefing."""
        return self._retrieval.max_briefing_chars
