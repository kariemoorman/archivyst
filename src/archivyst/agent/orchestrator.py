"""
Archivyst agent orchestrator.

Implements the main reasoning loop for query processing.
"""

import json
import logging
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from archivyst.agent.extraction import (
    check_contradictions,
    save_extracted_entities,
    save_extracted_memory,
)
from archivyst.agent.memory import MemoryStore
from archivyst.agent.prompts import (
    BRIEFING_PROMPT,
    ENTITY_EXTRACTION_PROMPT,
    MEMORY_EXTRACTION_PROMPT,
    QUERY_ANALYSIS_PROMPT,
    SYNTHESIS_PROMPT,
    SYNTHESIS_PROMPT_LAX,
    format_context_for_synthesis,
    format_conversation_for_memory,
)
from archivyst.core.index import VaultManager
from archivyst.core.markdown import get_section_content
from archivyst.core.models import (
    Config,
    ConversationTurn,
    RetrievalResult,
    RetrievalSource,
    SessionState,
    UserContext,
)
from archivyst.llm.client import LLMClient
from archivyst.llm.utils import parse_json_response
from archivyst.retrieval.linker import expand_with_links
from archivyst.retrieval.router import route_query
from archivyst.retrieval.traverser import extract_relevant_content, traverse_toc

logger = logging.getLogger(__name__)


class ArchivystAgent:
    """
    Main Archivyst agent orchestrating the RAG pipeline.

    Handles query analysis, retrieval, and synthesis.
    """

    def __init__(
        self,
        config: Config,
        llm_client: LLMClient,
        vault_manager: VaultManager,
        memory_store: MemoryStore,
    ):
        self.config = config
        self.llm = llm_client
        self.vaults = vault_manager
        self.memory = memory_store
        self.response_mode: str = "strict"
        self.ta = config.token_allocation

        self._session: SessionState | None = None
        self._user_context: UserContext | None = None
        self._turn_index: int = 0
        self._last_promotion_check: datetime | None = None
        self._pending_promotions: list[dict[str, str]] | None = None
        self._last_retrieval: RetrievalResult | None = None
        self._session_messages: list[dict[str, str]] = []

    async def start_session(self, active_vaults: list[str] | None = None) -> str:
        """
        Start a new session.

        Args:
            active_vaults: Optional list of vault names to activate

        Returns:
            Session ID
        """
        session_id = str(uuid.uuid4())[:16]

        # Load user context (includes user/, procedural/, semantic/ memory)
        self._user_context = self.memory.load_user_context()

        # Build session scope from default project
        project = self.config.get_default_project()
        if not project:
            raise RuntimeError("No project configured.")
        project_names = [project.name]
        scope = self.config.build_session_scope(project_names)

        self._session = SessionState(
            session_id=session_id,
            projects=project_names,
            scope=scope,
        )

        self._turn_index = 0
        self._last_promotion_check = datetime.now()
        self._pending_promotions = None

        # Send memory briefing to LLM to establish session context
        await self._send_memory_briefing()

        return session_id

    async def _send_memory_briefing(self) -> None:
        """Send memory context to the LLM as a session briefing."""
        memory_context = self._format_user_context()
        briefing = BRIEFING_PROMPT.format(memory_context=memory_context)

        self._session_messages = [
            {"role": "user", "content": briefing},
        ]

        ta = self.ta
        response = await self.llm.complete_chat(
            messages=self._session_messages,
            task="synthesis",
            max_tokens=ta.briefing.max_tokens,
            temperature=ta.briefing.temperature,
        )

        self._session_messages.append(
            {"role": "assistant", "content": response.strip()}
        )

    @property
    def session(self) -> SessionState:
        if self._session is None:
            raise RuntimeError("No active session. Call start_session() first.")
        return self._session

    async def analyze_query(self, query: str) -> dict[str, object]:
        """
        Analyze a query to determine retrieval strategy.

        Returns analysis including:
        - query_type: factual, exploratory, procedural, etc.
        - scope_hint: which vaults/topics to search
        - needs_memory: whether to check episodic/semantic memory
        """
        ta = self.ta
        prompt = QUERY_ANALYSIS_PROMPT.format(
            query=query,
            active_vaults=", ".join(self.session.scope.all_knowledge_vaults),
            user_context="(context loaded via session briefing)",
        )

        response = await self.llm.complete(
            prompt=prompt,
            task="routing",
            max_tokens=ta.routing.max_tokens,
            temperature=ta.routing.temperature,
        )

        # Parse response (expecting JSON-like structure)

        try:
            result: dict[str, object] = parse_json_response(response)
            return result
        except (json.JSONDecodeError, ValueError):
            return {
                "query_type": "general",
                "scope_hint": list(self.session.scope.all_knowledge_vaults),
                "needs_memory": False,
            }

    def _format_user_context(self) -> str:
        """Format user context for prompts with size budget.

        Prioritizes sections by importance: preferences > vault meta >
        entities > profile > facts > patterns > corrections.
        Truncates lower-priority sections if the total exceeds budget.
        """
        if not self._user_context:
            return "(no user context loaded)"

        # Ordered by priority (highest first)
        candidates: list[tuple[str, str | None]] = [
            ("Preferences", self._user_context.preferences),
            ("Vault Knowledge", self._user_context.vault_meta),
            ("Known Entities", self._user_context.entities),
            ("Profile", self._user_context.profile),
            ("Learned Facts", self._user_context.facts),
            ("Learned Patterns", self._user_context.patterns),
            ("Past Corrections", self._user_context.corrections),
        ]

        sections: list[str] = []
        total_chars = 0

        for heading, content in candidates:
            if not content:
                continue
            section = f"## {heading}\n{content}"
            max_briefing = self.config.token_allocation.retrieval.max_briefing_chars
            if total_chars + len(section) > max_briefing:
                # Truncate this section to fit remaining budget
                remaining = max_briefing - total_chars
                if remaining > 200:  # Only include if meaningful
                    section = section[:remaining] + "\n[...truncated...]"
                    sections.append(section)
                break
            sections.append(section)
            total_chars += len(section)

        return "\n\n".join(sections) if sections else "(no user context)"

    def _search_episodic_memory(self, query: str) -> dict[str, str]:
        """Search past conversations and unpromoted working sessions."""
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 3]
        hits: dict[str, str] = {}

        # Search saved conversations
        for fname in self.memory.list_files("episodic/conversations"):
            if not fname.endswith(".md"):
                continue
            try:
                content = self.memory.read(f"episodic/conversations/{fname}")
                if not content:
                    continue
                content_lower = content.lower()
                if any(w in content_lower for w in query_words):
                    hits[fname] = content[:1000]
            except Exception as e:
                logger.warning(
                    "[retrieval] Failed to search conversation %s: %s", fname, e
                )
                self.memory.save_event(
                    "error",
                    f"Failed to search conversation {fname}: {e}",
                )

        # Search unpromoted working sessions (promoted ones are in semantic memory)
        for session_id in self.memory.list_unpromoted_sessions():
            turns = self.memory.load_session_turns(session_id)
            for turn in turns:
                turn_text = f"{turn.get('query', '')} {turn.get('response', '')}"
                if any(w in turn_text.lower() for w in query_words):
                    hits[f"session:{session_id}:{turn.get('file', '')}"] = turn_text[
                        :1000
                    ]
                    break  # One hit per session is enough

        return hits

    def _search_semantic_memory(self, query: str) -> dict[str, str]:
        """Search semantic entities and facts for content relevant to the query."""
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 3]
        hits: dict[str, str] = {}

        # Search entities
        for entity_type in ["people", "projects", "concepts"]:
            for name in self.memory.list_entities(entity_type):
                name_lower = name.lower()
                if any(w in name_lower for w in query_words):
                    entity = self.memory.load_entity(entity_type, name)
                    if entity:
                        if isinstance(entity, dict):
                            hits[f"{entity_type}/{name}"] = json.dumps(entity)
                        else:
                            hits[f"{entity_type}/{name}"] = str(entity)

        # Search facts
        for fname in self.memory.list_files("semantic/facts"):
            if not fname.endswith((".md", ".json")):
                continue
            try:
                content = self.memory.read(f"semantic/facts/{fname}")
                if not content:
                    continue
                content_lower = content.lower()
                if any(w in content_lower for w in query_words):
                    hits[f"facts/{fname}"] = content
            except Exception as e:
                logger.warning("[retrieval] Failed to search fact %s: %s", fname, e)
                self.memory.save_event(
                    "error",
                    f"Failed to search fact {fname}: {e}",
                )

        # Search relationships
        try:
            relationships = self.memory.read("semantic/relationships.md")
            if relationships:
                rel_lower = relationships.lower()
                if any(w in rel_lower for w in query_words):
                    hits["relationships"] = relationships
        except Exception:
            pass

        return hits

    async def _route_to_notes(
        self,
        query: str,
        vault: Any,
        vault_name: str,
        tocs: list[Any],
        reasoning_trace: list[str],
    ) -> list[Path]:
        """Route a query to relevant notes via keyword match + LLM routing + wikilink expansion.

        Args:
            query: User query
            vault: VaultIndex instance
            vault_name: Name of the vault
            tocs: All ToC entries for the vault
            reasoning_trace: Trace list to append reasoning steps to

        Returns:
            List of expanded note paths to process
        """
        # Pre-routing: always include notes whose titles match the query
        query_lower = query.lower()
        query_words_note = [w for w in query_lower.split() if len(w) > 3]
        keyword_note_paths: list[Path] = []
        for toc in tocs:
            title_lower = toc.title.lower()
            if any(w in title_lower for w in query_words_note):
                keyword_note_paths.append(toc.path)

        # Route query to relevant notes via LLM
        reasoning_trace.append(f"Routing query in vault '{vault_name}'...")
        routing = await route_query(
            self.llm,
            query,
            tocs,
            vault.vault_path,
        )

        reasoning_trace.append(
            f"Selected {len(routing.selected_notes)} notes: {routing.reasoning}"
        )

        # Merge LLM-routed notes with keyword-matched notes
        selected_paths = [
            vault.vault_path / str(p)
            for p in routing.selected_notes
            if (vault.vault_path / str(p)).exists()
        ]
        for kp in keyword_note_paths:
            if kp not in selected_paths:
                selected_paths.append(kp)
                reasoning_trace.append(f"Added '{kp.name}' via title keyword match")

        if selected_paths:
            expanded = expand_with_links(
                vault,
                selected_paths,
                max_expansion=2,
                max_depth=1,
            )
            reasoning_trace.append(f"Expanded to {len(expanded)} notes via wikilinks")
        else:
            expanded = []

        return expanded

    def _match_sections_fuzzy(
        self,
        query: str,
        note_toc: Any,
        parsed_content: str,
        query_lower: str,
        query_words: set[str],
        long_words: list[str],
    ) -> dict[str, str]:
        """Fuzzy-match sections whose headings or summaries match query terms.

        Args:
            query: Original query string
            note_toc: Note's table of contents
            parsed_content: Full note content
            query_lower: Lowercased query
            query_words: Set of all query words (lowercased)
            long_words: List of query words with length > 3

        Returns:
            Dict mapping section heading to section content for matched sections
        """
        heading_threshold = (
            self.config.token_allocation.retrieval.heading_match_threshold
        )
        content_threshold = (
            self.config.token_allocation.retrieval.content_match_threshold
        )

        has_long_words = len(long_words) > 0
        keyword_matched: dict[str, str] = {}
        all_flat_sections = []
        for s in note_toc.sections:
            all_flat_sections.extend(s.flatten())

        for section in all_flat_sections:
            # Skip the document title heading if it has child sections —
            # the doc is already selected by routing, and matching the
            # title would pull in every section. But keep it if it's a
            # standalone intro section with no children (e.g., files
            # without a secondary header).
            if section.level == 1 and section.children:
                continue

            heading_lower = section.heading.lower()
            summary_lower = (section.summary or "").lower()
            searchable = f"{heading_lower} {summary_lower}"

            # Fast path: exact substring match on heading
            if heading_lower in query_lower or any(
                w in heading_lower for w in long_words
            ):
                keyword_matched[section.heading] = get_section_content(
                    parsed_content,
                    section,
                )
                continue

            if not has_long_words:
                continue

            # Fuzzy match against heading (early exit on first match)
            heading_score = 0.0
            for w in long_words:
                s = SequenceMatcher(None, heading_lower, w).ratio()
                if s >= heading_threshold:
                    heading_score = s
                    break
                heading_score = max(heading_score, s)

            if heading_score >= heading_threshold:
                keyword_matched[section.heading] = get_section_content(
                    parsed_content,
                    section,
                )
                continue

            # Fuzzy match against combined heading + summary (early exit)
            content_score = 0.0
            for w in long_words:
                s = SequenceMatcher(None, searchable, w).ratio()
                if s >= content_threshold:
                    content_score = s
                    break
                content_score = max(content_score, s)

            # Also check if query words appear as substrings in summary
            summary_substring = any(
                w in summary_lower for w in query_words if len(w) > 4
            )

            if content_score >= content_threshold or summary_substring:
                keyword_matched[section.heading] = get_section_content(
                    parsed_content,
                    section,
                )

        return keyword_matched

    async def _traverse_and_extract(
        self,
        query: str,
        note_toc: Any,
        parsed_content: str,
        keyword_matched: dict[str, str],
        vault_name: str,
        rel_path: str,
        note_path: Path,
        project_name: str,
    ) -> tuple[dict[str, str], list[RetrievalSource]]:
        """Run LLM traversal, extract content, merge with keyword matches, and build sources.

        Args:
            query: User query
            note_toc: Note's table of contents
            parsed_content: Full note content
            keyword_matched: Dict of sections already matched by fuzzy matching
            vault_name: Name of the vault
            rel_path: Relative path of the note within the vault
            note_path: Absolute path to the note
            project_name: Name of the project owning this vault

        Returns:
            Tuple of (content dict, sources list) for this note
        """
        content: dict[str, str] = {}
        sources: list[RetrievalSource] = []

        # LLM traversal for additional sections
        steps = await traverse_toc(
            self.llm,
            query,
            note_toc,
            parsed_content,
        )

        # Extract content from selected sections
        extracted = await extract_relevant_content(
            self.llm,
            query,
            note_toc,
            parsed_content,
            steps,
        )

        # Merge keyword matches with LLM-selected sections
        # (keyword matches take priority)
        merged = {**extracted, **keyword_matched}

        # Record sources
        for section_path, section_content in merged.items():
            content_key = f"{vault_name}:{rel_path}:{section_path}"

            reason = (
                "Heading matched query"
                if section_path in keyword_matched
                else "Selected via ToC traversal"
            )
            sources.append(
                RetrievalSource(
                    project=project_name,
                    vault=vault_name,
                    note_path=note_path,
                    section_heading=section_path,
                    relevance_reason=reason,
                )
            )
            content[content_key] = section_content

        # Include ToC outline so synthesis knows what sections exist
        toc_outline = "\n".join(
            f"  {'  ' * (s.level - 1)}- {s.heading}"
            for s in note_toc.sections
            for s in s.flatten()
        )
        if toc_outline:
            content[f"{vault_name}:{rel_path}:_toc"] = (
                f"Document outline for {note_toc.title}:\n{toc_outline}"
            )

        # If no sections selected, include note summary
        if not extracted and note_toc.summary:
            content_key = f"{vault_name}:{rel_path}:summary"

            sources.append(
                RetrievalSource(
                    project=project_name,
                    vault=vault_name,
                    note_path=note_path,
                    section_heading=None,
                    relevance_reason="Note-level summary",
                )
            )
            content[content_key] = f"Summary: {note_toc.summary}"

        return content, sources

    async def retrieve(
        self,
        query: str,
        scope: list[str] | None = None,
        max_sources: int = 5,
    ) -> RetrievalResult:
        """
        Retrieve relevant content for a query.

        Args:
            query: User query
            scope: Optional list of vault names to search
            max_sources: Maximum sources to return

        Returns:
            RetrievalResult with sources and content
        """
        scope = scope or list(self.session.scope.all_knowledge_vaults)
        reasoning_trace: list[str] = []
        sources: list[RetrievalSource] = []
        content: dict[str, str] = {}

        # Short-file threshold: pass full content directly instead of
        # traversing sections, so nothing gets lost in routing.
        short_file_chars = self.config.token_allocation.retrieval.short_file_chars

        for vault_name in scope:
            vault = self.vaults.get_vault(vault_name)
            if not vault:
                reasoning_trace.append(f"Vault '{vault_name}' not found, skipping")
                continue

            # Determine which project owns this vault
            project_name = self.session.projects[0]  # default
            for ps in self.session.scope.projects:
                if vault_name in ps.knowledge_vaults:
                    project_name = ps.project
                    break

            # Get all ToCs for routing
            tocs = vault.get_all_tocs()
            if not tocs:
                reasoning_trace.append(f"Vault '{vault_name}' has no indexed notes")
                continue

            # Route query to relevant notes
            expanded = await self._route_to_notes(
                query,
                vault,
                vault_name,
                tocs,
                reasoning_trace,
            )

            # Process each selected note
            for note_path in expanded[:max_sources]:
                note_toc = vault.load_toc(note_path)
                if note_toc is None:
                    continue

                # Read note content (simple read, no full parse needed)
                note_content = note_path.read_text(encoding="utf-8")
                rel_path = str(note_path.relative_to(vault.vault_path))

                if len(note_content) <= short_file_chars:
                    # Small file -- include full content directly
                    content_key = f"{vault_name}:{rel_path}:full"
                    sources.append(
                        RetrievalSource(
                            project=project_name,
                            vault=vault_name,
                            note_path=note_path,
                            section_heading=None,
                            relevance_reason="Full content (short file)",
                        )
                    )
                    content[content_key] = note_content
                    continue

                # Compute shared query terms for fuzzy matching
                query_lower = query.lower()
                query_words = set(query_lower.split())
                long_words = [w for w in query_words if len(w) > 3]

                # Fuzzy-match sections by heading/summary
                keyword_matched = self._match_sections_fuzzy(
                    query,
                    note_toc,
                    note_content,
                    query_lower,
                    query_words,
                    long_words,
                )

                # LLM traversal + content extraction
                note_content_dict, note_sources = await self._traverse_and_extract(
                    query,
                    note_toc,
                    note_content,
                    keyword_matched,
                    vault_name,
                    rel_path,
                    note_path,
                    project_name,
                )
                content.update(note_content_dict)
                sources.extend(note_sources)

        # Search episodic memory (past conversations) for relevant context
        reasoning_trace.append("Searching episodic memory...")
        episodic_hits = self._search_episodic_memory(query)
        if episodic_hits:
            reasoning_trace.append(
                f"Found {len(episodic_hits)} relevant episodic memories"
            )
            for key, val in episodic_hits.items():
                content[f"memory:episodic:{key}"] = val

        # Search semantic memory (entities, facts) for relevant context
        reasoning_trace.append("Searching semantic memory...")
        semantic_hits = self._search_semantic_memory(query)
        if semantic_hits:
            reasoning_trace.append(
                f"Found {len(semantic_hits)} relevant semantic memories"
            )
            for key, val in semantic_hits.items():
                content[f"memory:semantic:{key}"] = val

        reasoning_trace.append(f"Retrieved {len(sources)} sources total")

        return RetrievalResult(
            query=query,
            projects=self.session.projects,
            sources=sources,
            reasoning_trace=reasoning_trace,
            content=content,
        )

    async def synthesize(
        self,
        query: str,
        retrieval: RetrievalResult,
    ) -> str:
        """
        Synthesize an answer from retrieved content.

        Uses the session conversation thread so the LLM has the
        memory briefing in context without repeating it.
        """
        context = format_context_for_synthesis(retrieval)

        # Budget the context to avoid exceeding the model's context window.
        # Estimate: briefing ~4K chars, we want room for response (~4K tokens).
        # Leave ~8K chars for retrieved context.
        max_ctx = self.config.token_allocation.retrieval.max_context_chars
        if len(context) > max_ctx:
            context = context[:max_ctx] + "\n\n[...context truncated...]"

        prompt_template = (
            SYNTHESIS_PROMPT_LAX if self.response_mode == "lax" else SYNTHESIS_PROMPT
        )
        synthesis_prompt = prompt_template.format(query=query, context=context)

        # Append to session conversation thread
        messages = self._session_messages + [
            {"role": "user", "content": synthesis_prompt},
        ]

        ta = self.ta
        response = await self.llm.complete_chat(
            messages=messages,
            task="synthesis",
            max_tokens=ta.synthesis.max_tokens,
            temperature=ta.synthesis.temperature,
        )

        answer = response.strip()

        # Update session messages with this exchange
        self._session_messages.append({"role": "user", "content": f"[Query: {query}]"})
        self._session_messages.append({"role": "assistant", "content": answer})

        return answer

    async def query(self, query: str) -> str:
        """
        Process a query end-to-end.

        This is the main entry point for single-turn queries.
        """
        # Limit query length to prevent context overflow
        max_query = self.config.token_allocation.retrieval.max_query_length
        if len(query) > max_query:
            logger.warning(
                "[query] Query truncated from %d to %d chars",
                len(query),
                max_query,
            )
            query = query[:max_query]

        # Record user turn
        self.session.conversation.append(
            ConversationTurn(
                role="user",
                content=query,
            )
        )

        # Analyze query
        analysis = await self.analyze_query(query)

        # Retrieve
        scope_hint = analysis.get("scope_hint")
        retrieval = await self.retrieve(
            query,
            scope=list(scope_hint) if isinstance(scope_hint, list) else None,
        )
        self._last_retrieval = retrieval

        # Synthesize
        answer = await self.synthesize(query, retrieval)

        # Record assistant turn
        self.session.conversation.append(
            ConversationTurn(
                role="assistant",
                content=answer,
            )
        )

        # Extract and store to memory (fire-and-forget)
        self._turn_index += 1
        try:
            await self._extract_and_store(query, answer)
        except Exception as e:
            logger.warning("[extraction] Post-query extraction failed: %s", e)
            self.memory.save_event(
                "error",
                f"Post-query extraction failed: {e}",
                {"turn": self._turn_index},
            )

        # Check if auto-promotion is due
        try:
            await self._check_promotion()
        except Exception as e:
            logger.warning("[promotion] Auto-promotion check failed: %s", e)
            self.memory.save_event(
                "error",
                f"Auto-promotion check failed: {e}",
            )

        return answer

    async def end_session(self, save_conversation: bool = True) -> None:
        """
        End the current session.

        Saves conversation and runs end-of-session extraction/promotion.
        """
        if self._session:
            if save_conversation:
                self.memory.save_conversation(self._session)

            # End-of-session quick organize: extract from full conversation
            try:
                if self._session.conversation:
                    await self._end_session_extract()
            except Exception as e:
                logger.warning("[session] End-of-session extraction failed: %s", e)
                self.memory.save_event(
                    "error",
                    f"End-of-session extraction failed: {e}",
                )

            # Batch commit all per-turn extractions for this session
            self.memory.versioning.auto_commit(
                f"Session {self._session.session_id} ({self._turn_index} turns)"
            )

        self._session = None
        self._user_context = None

    async def _extract_and_store(self, query: str, answer: str) -> None:
        """Extract entities and facts from a Q/A turn and store to memory."""
        session_id = self.session.session_id

        # 1. Save turn to working memory
        self.memory.save_session_turn(
            session_id,
            self._turn_index,
            query,
            answer,
        )

        # 2. Entity extraction
        ta = self.ta
        try:
            entity_response = await self.llm.complete(
                prompt=ENTITY_EXTRACTION_PROMPT.format(
                    text=answer,
                    source=f"session:{session_id}:turn:{self._turn_index}",
                ),
                task="memory",
                max_tokens=ta.session_extraction.max_tokens,
                temperature=ta.session_extraction.temperature,
            )
            self._save_extracted_entities(entity_response)
        except Exception as e:
            logger.warning(
                "[extraction] Entity extraction failed on turn %d: %s",
                self._turn_index,
                e,
            )
            self.memory.save_event(
                "error",
                f"Entity extraction failed on turn {self._turn_index}: {e}",
                {"session": session_id, "turn": self._turn_index},
            )

        # 3. Memory extraction (preferences, facts, corrections, patterns)
        try:
            turn_text = f"User: {query}\n\nAssistant: {answer}"
            memory_response = await self.llm.complete(
                prompt=MEMORY_EXTRACTION_PROMPT.format(conversation=turn_text),
                task="memory",
                max_tokens=ta.session_extraction.max_tokens,
                temperature=ta.session_extraction.temperature,
            )
            self._save_extracted_memory(memory_response)
        except Exception as e:
            logger.warning(
                "[extraction] Memory extraction failed on turn %d: %s",
                self._turn_index,
                e,
            )
            self.memory.save_event(
                "error",
                f"Memory extraction failed on turn {self._turn_index}: {e}",
                {"session": session_id, "turn": self._turn_index},
            )

        # 4. Contradiction check against existing memory
        try:
            turn_text = f"User: {query}\n\nAssistant: {answer}"
            contradiction_ids = await check_contradictions(
                self.memory,
                turn_text,
                self.llm,
                context=f"session {session_id}, turn {self._turn_index}",
                max_tokens=ta.session_extraction.max_tokens,
                temperature=ta.session_extraction.temperature,
            )
            if contradiction_ids:
                logger.info(
                    "[extraction] Detected %d contradiction(s) on turn %d: %s",
                    len(contradiction_ids),
                    self._turn_index,
                    contradiction_ids,
                )
        except Exception as e:
            logger.warning(
                "[extraction] Contradiction check failed on turn %d: %s",
                self._turn_index,
                e,
            )

        # Version commit deferred to session end (batch per session)

    def _save_extracted_entities(self, response: str) -> None:
        """Parse entity extraction response and save to memory."""

        try:
            data = self._parse_json_response(response)
        except (json.JSONDecodeError, ValueError):
            # Try to salvage truncated JSON by closing braces
            try:
                text = response.strip()
                if "```" in text:
                    start = text.index("```") + 3
                    if text[start: start + 4] == "json":
                        start += 4
                    end = text.rfind("```")
                    text = text[start:end] if end > start else text[start:]
                last_brace = text.rfind("}")
                if last_brace > 0:
                    data = json.loads(text[: last_brace + 1])
                else:
                    raise ValueError("No JSON object found")
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "[extraction] Failed to parse entity extraction JSON"
                )
                return

        count = save_extracted_entities(self.memory, data)

        if count:
            self.memory.save_event(
                "discovery",
                f"Extracted {count} entities",
                {"count": count},
            )

    def _save_extracted_memory(self, response: str) -> None:
        """Parse memory extraction response and save to memory."""

        try:
            data = self._parse_json_response(response)
        except (json.JSONDecodeError, ValueError):
            logger.warning("[extraction] Failed to parse memory extraction JSON")
            self.memory.save_event(
                "error",
                "Failed to parse memory extraction JSON",
                {"raw_response": response[:200]},
            )
            return

        save_extracted_memory(self.memory, data)

        # Log events for corrections and patterns (orchestrator-specific)
        for correction in data.get("corrections", []):
            if isinstance(correction, str) and correction.strip():
                self.memory.save_event(
                    "learning",
                    f"Correction recorded: {correction[:100]}",
                    {"type": "correction"},
                )

        for pattern in data.get("patterns", []):
            if isinstance(pattern, str) and pattern.strip():
                self.memory.save_event(
                    "learning",
                    f"Pattern identified: {pattern[:100]}",
                    {"type": "pattern"},
                )

    def _parse_json_response(self, response: str) -> dict[str, Any]:
        """Parse JSON from LLM response, handling markdown code blocks."""
        return parse_json_response(response)

    async def _check_promotion(self) -> None:
        """Check if auto-promotion is due based on interval."""
        if not self._last_promotion_check:
            return

        interval = self.config.memory.working.promotion_interval_minutes
        elapsed = (datetime.now() - self._last_promotion_check).total_seconds() / 60

        if elapsed < interval:
            return

        self._last_promotion_check = datetime.now()
        await self._auto_promote()

    async def _auto_promote(self) -> None:
        """Auto-promote: mark current session as promoted.

        Per-turn extraction already saved entities/facts/patterns to
        project memory. This just marks the session in the manifest.
        Only runs when auto_promote is True in config.
        """
        if not self.config.memory.working.auto_promote:
            return

        session_id = self.session.session_id
        self.memory.mark_sessions_promoted([session_id])
        logger.info("[promotion] Auto-promoted session %s", session_id)

    async def promote_session(self) -> list[dict[str, str]]:
        """Mark current session as promoted. Called by /promote command.

        Per-turn extraction already saved data to project memory.
        This marks the session so it won't be re-searched at query time.
        Returns empty list (no LLM-based suggestions needed).
        """
        session_id = self.session.session_id
        self.memory.mark_sessions_promoted([session_id])
        self.memory.versioning.auto_commit("Manual promotion")
        return []

    async def _end_session_extract(self) -> None:
        """End-of-session extraction pass on full conversation."""
        conversation_text = format_conversation_for_memory(
            self.session.conversation,
        )

        # Run memory extraction on full conversation
        ta = self.ta
        response = await self.llm.complete(
            prompt=MEMORY_EXTRACTION_PROMPT.format(
                conversation=conversation_text,
            ),
            task="memory",
            max_tokens=ta.session_extraction.max_tokens,
            temperature=ta.session_extraction.temperature,
        )
        self._save_extracted_memory(response)
        self.memory.versioning.auto_commit(
            f"End-of-session extraction {self.session.session_id}"
        )
