"""
Agent memory store for Archivyst.

Manages the cognitive memory architecture with project-scoped access:
- global/ for cross-project knowledge (when permitted)
- projects/<name>/ for project-specific memory

Each project memory contains:
- user/: project-specific preferences
- episodic/: conversations, events
- semantic/: entities, vaults, facts
- procedural/: patterns, corrections
- working/: current task, scratchpad
- attachments/: files referenced by notes
"""

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from archivyst.agent.versioning import MemoryVersioning
from archivyst.core.models import (
    Config,
    GlobalAccess,
    ProjectScope,
    SessionScope,
    SessionState,
    UserContext,
)

MemoryLocation = Literal["project", "global"]
logger = logging.getLogger(__name__)


class MemoryAccessError(Exception):
    """Raised when attempting to access memory outside of scope."""

    pass


class MemoryStore:
    """
    Manages agent memory with project-scoped access control.

    Enforces read/write permissions based on ProjectScope.
    """

    def __init__(self, config: Config, scope: ProjectScope):
        self.config = config
        self.scope = scope
        self._promoted_cache: set[str] | None = None
        self._versioning: MemoryVersioning | None = None
        self._global_versioning: MemoryVersioning | None = None

        self._ensure_structure()

    @property
    def versioning(self) -> MemoryVersioning:
        """Lazy-initialized project memory versioning."""
        if self._versioning is None:
            self._versioning = MemoryVersioning(self.project_path)
            self._versioning.ensure_repo()
        return self._versioning

    @property
    def global_versioning(self) -> MemoryVersioning | None:
        """Lazy-initialized global memory versioning."""
        if not self.global_path:
            return None
        if self._global_versioning is None:
            self._global_versioning = MemoryVersioning(self.global_path)
            self._global_versioning.ensure_repo()
        return self._global_versioning

    @property
    def project_path(self) -> Path:
        """Path to this project's memory directory."""
        return self.scope.project_memory_path

    @property
    def global_path(self) -> Path | None:
        """Path to global memory, or None if not accessible."""
        return self.scope.global_memory_path

    def _ensure_structure(self) -> None:
        """Create memory directory structure if missing."""
        # Project directories
        project_dirs = [
            "user",
            "episodic/conversations",
            "episodic/events",
            "semantic/entities/people",
            "semantic/entities/projects",
            "semantic/entities/concepts",
            "semantic/vaults",
            "semantic/facts",
            "procedural/patterns",
            "procedural/corrections",
            "working/queue",
            "working/sessions",
            "attachments",
        ]

        for dir_path in project_dirs:
            (self.project_path / dir_path).mkdir(parents=True, exist_ok=True)

        # Create default files if missing
        defaults = {
            "user/preferences.md": (
                f"# {self.scope.project} Preferences\n\n" "(No preferences set yet)\n"
            ),
        }

        for rel_path, content in defaults.items():
            file_path = self.project_path / rel_path
            if not file_path.exists():
                file_path.write_text(content)

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Sanitize a name for use as a filename.

        Strips dangerous characters, limits length, and ensures
        the result is a valid, safe filename.
        """
        safe = re.sub(r"[^\w\-.]", "-", name.lower())
        safe = safe.strip("-.")
        safe = re.sub(r"-{2,}", "-", safe)  # Collapse multiple dashes
        safe = safe[:100]  # Length limit
        return safe or "unnamed"

    def _check_read(self, path: Path) -> None:
        """Raise MemoryAccessError if path is not readable."""
        if not self.scope.can_read_path(path):
            raise MemoryAccessError(
                f"Cannot read '{path}': outside project scope "
                f"(global access: {self.scope.global_access.value})"
            )

    def _check_write(self, path: Path) -> None:
        """Raise MemoryAccessError if path is not writable."""
        if not self.scope.can_write_path(path):
            raise MemoryAccessError(
                f"Cannot write '{path}': outside project scope "
                f"(global access: {self.scope.global_access.value})"
            )

    def _resolve_path(
        self, rel_path: str, location: MemoryLocation = "project"
    ) -> Path:
        """
        Resolve a relative path to absolute.

        Validates the resolved path stays within the memory directory
        to prevent path traversal attacks (e.g., "../../etc/passwd").

        Args:
            rel_path: Relative path within memory
            location: "project" or "global"
        """
        if location == "global":
            if not self.global_path:
                raise MemoryAccessError("Global memory not accessible in this scope")
            base = self.global_path
        else:
            base = self.project_path

        unresolved = base / rel_path

        # Reject symlinks BEFORE resolving — resolve() follows symlinks
        # so checking after would always return False
        if unresolved.exists() and unresolved.is_symlink():
            raise MemoryAccessError(f"Symlinks not allowed in memory: '{rel_path}'")

        resolved = unresolved.resolve()

        # Prevent path traversal — resolved path must stay within base
        if not resolved.is_relative_to(base.resolve()):
            raise MemoryAccessError(
                f"Path traversal blocked: '{rel_path}' escapes memory directory"
            )

        return resolved

    def read(self, rel_path: str, location: MemoryLocation = "project") -> str | None:
        """
        Read a memory file.

        Args:
            rel_path: Relative path within memory
            location: "project" or "global"
        """
        file_path = self._resolve_path(rel_path, location)
        self._check_read(file_path)

        if file_path.exists():
            return file_path.read_text()
        return None

    # Maximum size for memory files (entities, facts, conversations, etc.)
    MAX_MEMORY_FILE_SIZE = 24 * 1024  # 24KB

    def write(
        self, rel_path: str, content: str, location: MemoryLocation = "project"
    ) -> None:
        """
        Write a memory file.

        Args:
            rel_path: Relative path within memory
            content: Content to write
            location: "project" or "global"
        """
        file_path = self._resolve_path(rel_path, location)
        self._check_write(file_path)

        # Enforce size limit on memory files (use char length, not byte
        # length, to avoid premature truncation of non-ASCII text)
        if len(content) > self.MAX_MEMORY_FILE_SIZE:
            logger.warning(
                "[memory] Truncating file to %d chars: %s",
                self.MAX_MEMORY_FILE_SIZE,
                rel_path,
            )
            content = content[: self.MAX_MEMORY_FILE_SIZE]

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    def delete(self, rel_path: str, location: MemoryLocation = "project") -> bool:
        """
        Delete a memory file.

        Returns True if file existed and was deleted.
        """
        file_path = self._resolve_path(rel_path, location)
        self._check_write(file_path)

        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def list_files(
        self, rel_path: str, location: MemoryLocation = "project"
    ) -> list[str]:
        """
        List files in a memory directory.

        Returns list of filenames (not full paths).
        """
        dir_path = self._resolve_path(rel_path, location)
        self._check_read(dir_path)

        if not dir_path.exists():
            return []

        return [f.name for f in dir_path.iterdir() if f.is_file()]

    def load_user_context(self) -> UserContext:
        """
        Load user context at session start.

        Loads full user/, procedural/, and semantic/ memory.
        Merges global memory (if accessible) with project memory.
        """
        # Load project memory (always accessible)
        project_prefs = self.read("user/preferences.md")
        project_profile = self.read("user/profile.md")

        # Load all procedural patterns (not just user-patterns.md)
        project_patterns = self._load_dir_contents("procedural/patterns")

        # Load all procedural corrections
        project_corrections = self._load_dir_contents("procedural/corrections")

        # Load semantic entities (compact summaries)
        project_entities = self._load_entities_summary()

        # Load semantic facts
        project_facts = self._load_dir_contents("semantic/facts")

        # Load vault meta-summaries
        project_vault_meta = self._load_dir_contents("semantic/vaults")

        # Load global memory if accessible
        global_prefs = None
        global_profile = None
        global_patterns = None
        global_corrections = None
        global_entities = None
        global_facts = None

        if self.global_path and self.scope.global_access in (
            GlobalAccess.FULL,
            GlobalAccess.READ,
        ):
            try:
                global_prefs = self.read("user/preferences.md", "global")
                global_profile = self.read("user/profile.md", "global")
                global_patterns = self._load_dir_contents(
                    "procedural/patterns", "global"
                )
                global_corrections = self._load_dir_contents(
                    "procedural/corrections", "global"
                )
                global_entities = self._load_entities_summary("global")
                global_facts = self._load_dir_contents("semantic/facts", "global")
            except MemoryAccessError:
                pass

        # Merge: project overrides global
        def merge(global_val: str | None, project_val: str | None) -> str | None:
            if project_val and global_val:
                return f"{global_val}\n\n---\n# Project-specific:\n{project_val}"
            return project_val or global_val

        return UserContext(
            preferences=merge(global_prefs, project_prefs),
            profile=merge(global_profile, project_profile),
            patterns=merge(global_patterns, project_patterns),
            corrections=merge(global_corrections, project_corrections),
            entities=merge(global_entities, project_entities),
            facts=merge(global_facts, project_facts),
            vault_meta=project_vault_meta,
        )

    def _load_dir_contents(
        self,
        rel_dir: str,
        location: MemoryLocation = "project",
    ) -> str | None:
        """Load and concatenate all text files in a memory directory."""
        parts = []
        for fname in self.list_files(rel_dir, location):
            if not fname.endswith((".md", ".json", ".jsonl", ".txt")):
                continue
            try:
                content = self.read(f"{rel_dir}/{fname}", location)
                if content and content.strip():
                    parts.append(f"## {fname}\n{content.strip()}")
            except MemoryAccessError as e:
                logger.warning("[memory] Cannot read %s/%s: %s", rel_dir, fname, e)
            except UnicodeDecodeError as e:
                logger.warning(
                    "[memory] Unicode error reading %s/%s: %s", rel_dir, fname, e
                )
        return "\n\n".join(parts) if parts else None

    def _load_entities_summary(
        self, location: MemoryLocation = "project"
    ) -> str | None:
        """Load a compact summary of all entities."""
        parts = []
        for entity_type in ["people", "projects", "concepts"]:
            names = self.list_entities(entity_type, location)
            if names:
                parts.append(f"### {entity_type.capitalize()}")
                for name in names:
                    entity = self.load_entity(entity_type, name, location)
                    if isinstance(entity, dict):
                        summary = ", ".join(
                            f"{k}: {v}" for k, v in entity.items() if v and k != "name"
                        )
                        parts.append(f"- {name}: {summary}")
                    elif entity:
                        parts.append(f"- {name}: {str(entity)[:200]}")
        return "\n".join(parts) if parts else None

    def save_user_preference(
        self, key: str, value: str, location: MemoryLocation = "project"
    ) -> None:
        """Update a specific user preference."""
        prefs_path = "user/preferences.md"
        content = self.read(prefs_path, location) or "# Preferences\n\n"

        # Append or update preference
        lines = content.split("\n")
        updated = False

        for i, line in enumerate(lines):
            if line.startswith(f"- {key}:"):
                lines[i] = f"- {key}: {value}"
                updated = True
                break

        if not updated:
            lines.append(f"- {key}: {value}")

        self.write(prefs_path, "\n".join(lines), location)

    def save_conversation(self, session: SessionState) -> Path:
        """
        Save a conversation to episodic memory.

        Returns the path to the saved file.
        """
        date_str = session.started_at.strftime("%Y-%m-%d")
        filename = f"{date_str}-{session.session_id}.md"
        rel_path = f"episodic/conversations/{filename}"

        # Format conversation
        lines = [
            f"# Conversation {session.session_id}",
            "",
            f"Date: {session.started_at.isoformat()}",
            f"Project: {self.scope.project}",
            f"Vaults: {', '.join(self.scope.knowledge_vaults)}",
            "",
            "## Transcript",
            "",
        ]

        for turn in session.conversation:
            role = "User" if turn.role == "user" else "Assistant"
            lines.append(f"### {role} ({turn.timestamp.strftime('%H:%M:%S')})")
            lines.append("")
            lines.append(turn.content)
            lines.append("")

        self.write(rel_path, "\n".join(lines))
        return self.project_path / rel_path

    def save_event(
        self,
        event_type: str,
        description: str,
        metadata: dict[str, object] | None = None,
    ) -> Path:
        """
        Save an event to episodic memory.

        Returns the path to the saved file.
        """
        timestamp = datetime.now()
        date_str = timestamp.strftime("%Y-%m-%d")
        time_str = timestamp.strftime("%H%M%S")
        filename = f"{date_str}-{time_str}-{event_type}.md"
        rel_path = f"episodic/events/{filename}"

        lines = [
            f"# Event: {event_type}",
            "",
            f"Timestamp: {timestamp.isoformat()}",
            f"Project: {self.scope.project}",
            "",
            "## Description",
            "",
            description,
        ]

        if metadata:
            lines.extend(
                [
                    "",
                    "## Metadata",
                    "",
                    "```json",
                    json.dumps(metadata, indent=2),
                    "```",
                ]
            )

        self.write(rel_path, "\n".join(lines))
        return self.project_path / rel_path

    def save_entity(
        self,
        entity_type: str,
        name: str,
        content: str | dict[str, Any],
        location: MemoryLocation = "project",
    ) -> Path:
        """
        Save an entity to semantic memory as markdown with wikilinks.

        Args:
            entity_type: people, projects, or concepts
            name: Entity name (used as filename)
            content: Entity data (str or dict — dicts converted to markdown)
            location: "project" or "global" (requires FULL access)

        Returns the path to the saved file.
        """
        safe_name = self._safe_filename(name)

        if isinstance(content, dict):
            content_str = self._entity_dict_to_markdown(name, content)
        else:
            content_str = content

        rel_path = f"semantic/entities/{entity_type}/{safe_name}.md"
        self.write(rel_path, content_str, location)

        if location == "global":
            if not self.global_path:
                raise MemoryAccessError("Global memory not accessible in this scope")
            return self.global_path / rel_path
        return self.project_path / rel_path

    @staticmethod
    def _entity_dict_to_markdown(name: str, data: dict[str, Any]) -> str:
        """Convert an entity dict to markdown with wikilinks."""
        lines = [f"# {name}", ""]
        for key, value in data.items():
            if key == "name" or not value:
                continue
            val_str = str(value)
            # Wrap known entity references in wikilinks
            if key in ("role", "context", "description", "status", "definition"):
                lines.append(f"**{key.capitalize()}**: {val_str}")
            else:
                lines.append(f"**{key.capitalize()}**: {val_str}")
        lines.append("")
        return "\n".join(lines)

    def load_entity(
        self,
        entity_type: str,
        name: str,
        location: MemoryLocation = "project",
    ) -> dict[str, object] | str | None:
        """Load an entity from semantic memory."""
        safe_name = self._safe_filename(name)

        # Try markdown first (current format)
        md_path = f"semantic/entities/{entity_type}/{safe_name}.md"
        content = self.read(md_path, location)
        if content:
            return content

        # Fallback to JSON (legacy format, for migration)
        json_path = f"semantic/entities/{entity_type}/{safe_name}.json"
        content = self.read(json_path, location)
        if content:
            try:
                result: dict[str, object] = json.loads(content)
                return result
            except json.JSONDecodeError:
                return content

        return None

    def list_entities(
        self, entity_type: str, location: MemoryLocation = "project"
    ) -> list[str]:
        """List all entities of a given type."""
        rel_path = f"semantic/entities/{entity_type}"
        files = self.list_files(rel_path, location)
        return [Path(f).stem for f in files]

    def promote_to_global(self, rel_path: str, reason: str) -> Path:
        """
        Promote a project-scoped file to global memory.

        Requires FULL global access.

        Args:
            rel_path: Relative path within project memory
            reason: Justification for promotion (logged)

        Returns path to the global file.
        """
        if self.scope.global_access != GlobalAccess.FULL:
            raise MemoryAccessError(
                f"Cannot promote to global: access level is {self.scope.global_access.value}"
            )

        # Read from project
        content = self.read(rel_path, "project")
        if content is None:
            raise FileNotFoundError(f"Project file not found: {rel_path}")

        # Add promotion metadata
        promotion_note = (
            f"\n\n---\n_Promoted from project '{self.scope.project}'"
            f" on {datetime.now().isoformat()}_\n"
            f"_Reason: {reason}_\n"
        )

        # Write to global
        self.write(rel_path, content + promotion_note, "global")

        # Log the promotion
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "project": self.scope.project,
            "file": rel_path,
            "reason": reason,
        }

        # Append to promotion log directly (bypass size limit —
        # audit logs are append-only and should grow unbounded)
        log_file = self._resolve_path("procedural/promotions.jsonl", "global")
        self._check_write(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        # Version commit global memory
        if self.global_versioning:
            self.global_versioning.auto_commit(
                f"Promote from {self.scope.project}: {rel_path}"
            )

        if not self.global_path:
            raise MemoryAccessError("Global memory not accessible in this scope")
        return self.global_path / rel_path

    def commit_global(self, message: str) -> str | None:
        """Commit changes to global memory versioning. Returns commit hash or None."""
        gv = self.global_versioning
        if gv:
            return gv.auto_commit(message)
        return None

    def save_vault_meta(self, vault_name: str, content: str) -> None:
        """Save vault meta-knowledge."""
        safe_name = self._safe_filename(vault_name)
        rel_path = f"semantic/vaults/{safe_name}.md"
        self.write(rel_path, content)

    def load_vault_meta(self, vault_name: str) -> str | None:
        """Load vault meta-knowledge."""
        safe_name = self._safe_filename(vault_name)
        rel_path = f"semantic/vaults/{safe_name}.md"
        return self.read(rel_path)

    def save_working_note(self, name: str, content: str) -> None:
        """Save to working memory."""
        rel_path = f"working/{name}.md"
        self.write(rel_path, content)

    def load_working_note(self, name: str) -> str | None:
        """Load from working memory."""
        rel_path = f"working/{name}.md"
        return self.read(rel_path)

    def clear_working_memory(self) -> int:
        """
        Clear all working memory files.

        Returns count of files deleted.
        """
        count = 0
        working_dir = self.project_path / "working"

        for file in working_dir.glob("*.md"):
            if file.name != "README.md":
                file.unlink()
                count += 1

        return count

    def save_session_turn(
        self,
        session_id: str,
        turn_index: int,
        query: str,
        response: str,
    ) -> None:
        """Save a single query/response turn to working session memory."""
        timestamp = datetime.now().isoformat()
        content = (
            f"---\n"
            f"session: {session_id}\n"
            f"turn: {turn_index}\n"
            f"timestamp: {timestamp}\n"
            f"---\n\n"
            f"## Query\n\n{query}\n\n"
            f"## Response\n\n{response}\n"
        )
        rel_path = f"working/sessions/{session_id}/{turn_index:03d}.md"
        self.write(rel_path, content)

    def load_session_turns(self, session_id: str) -> list[dict[str, str]]:
        """Load all turns for a session, sorted by turn index."""
        session_dir = self.project_path / "working" / "sessions" / session_id
        if not session_dir.exists():
            return []

        turns = []
        for turn_file in sorted(session_dir.glob("*.md")):
            text = turn_file.read_text()
            # Parse frontmatter
            parts = text.split("---", 2)
            turn_data = {"file": turn_file.name, "raw": text}
            if len(parts) >= 3:
                for line in parts[1].strip().split("\n"):
                    if ": " in line:
                        key, val = line.split(": ", 1)
                        turn_data[key.strip()] = val.strip()
                # Extract query and response from body
                body = parts[2]
                if "## Query" in body and "## Response" in body:
                    q_start = body.index("## Query") + len("## Query")
                    r_start = body.index("## Response")
                    turn_data["query"] = body[q_start:r_start].strip()
                    turn_data["response"] = body[r_start + len("## Response") :].strip()
            turns.append(turn_data)
        return turns

    def list_working_sessions(self) -> list[str]:
        """List session IDs in working memory, sorted oldest first."""
        sessions_dir = self.project_path / "working" / "sessions"
        if not sessions_dir.exists():
            return []
        dirs = [d.name for d in sessions_dir.iterdir() if d.is_dir()]
        dirs.sort()
        return dirs

    def list_unpromoted_sessions(self) -> list[str]:
        """List session IDs that have NOT been promoted yet."""
        all_sessions = self.list_working_sessions()
        promoted = self._load_promoted_manifest()
        return [s for s in all_sessions if s not in promoted]

    def _load_promoted_manifest(self) -> set[str]:
        """Load the set of promoted session IDs from manifest (cached)."""
        if self._promoted_cache is not None:
            return self._promoted_cache

        manifest_path = self.project_path / "working" / "promoted.json"
        if not manifest_path.exists():
            self._promoted_cache = set()
            return self._promoted_cache
        try:
            data = json.loads(manifest_path.read_text())
            self._promoted_cache = set(data.get("promoted", []))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[versioning] Failed to load promoted manifest: %s", e)
            self._promoted_cache = set()
        return self._promoted_cache

    def mark_sessions_promoted(self, session_ids: list[str]) -> None:
        """Mark session IDs as promoted in the manifest. Invalidates cache."""
        promoted = self._load_promoted_manifest()
        promoted.update(session_ids)
        manifest_path = self.project_path / "working" / "promoted.json"
        manifest_path.write_text(json.dumps({"promoted": sorted(promoted)}, indent=2))
        self._promoted_cache = promoted  # Update cache

    def is_session_promoted(self, session_id: str) -> bool:
        """Check if a session has been promoted."""
        return session_id in self._load_promoted_manifest()

    def get_session_size(self, session_id: str) -> int:
        """Get total file size of a working session in bytes."""
        session_dir = self.project_path / "working" / "sessions" / session_id
        if not session_dir.exists():
            return 0
        return sum(f.stat().st_size for f in session_dir.rglob("*") if f.is_file())

    def get_oversized_sessions(self) -> list[str]:
        """List session IDs that exceed the file size threshold."""
        oversized = []
        for session_id in self.list_working_sessions():
            if self.get_session_size(session_id) > self.MAX_MEMORY_FILE_SIZE:
                oversized.append(session_id)
        return oversized

    def prune_working_memory(self) -> list[str]:
        """
        Prune old working sessions beyond retention limit.

        Returns list of session IDs to prune.
        If prune_before_delete is True, returns sessions that should be
        promoted before deletion — caller is responsible for promoting
        and then calling delete_sessions.
        """
        sessions = self.list_working_sessions()
        retention = self.config.memory.working.retention_sessions
        if len(sessions) <= retention:
            return []

        to_prune = sessions[:-retention]

        if self.config.memory.working.prune_before_delete:
            # Return the list — caller should promote then call delete_sessions
            return to_prune

        # Delete directly
        for session_id in to_prune:
            session_dir = self.project_path / "working" / "sessions" / session_id
            if session_dir.exists():
                shutil.rmtree(session_dir)
        return to_prune

    def delete_sessions(self, session_ids: list[str]) -> None:
        """Delete specific working sessions by ID."""

        for session_id in session_ids:
            session_dir = self.project_path / "working" / "sessions" / session_id
            if session_dir.exists():
                shutil.rmtree(session_dir)

    def save_fact(
        self, name: str, content: str, location: MemoryLocation = "project"
    ) -> None:
        """Save a fact to semantic memory."""
        safe_name = self._safe_filename(name)
        rel_path = f"semantic/facts/{safe_name}.md"
        self.write(rel_path, content, location)

    def save_correction(self, correction: str, context: str) -> Path:
        """Save a correction for procedural learning."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rel_path = f"procedural/corrections/{timestamp}.md"

        content = (
            f"# Correction\n\n## Context\n{context}\n\n## Correction\n{correction}\n"
        )
        self.write(rel_path, content)
        return self.project_path / rel_path

    def save_pattern(self, pattern_name: str, pattern: str) -> None:
        """Save a learned pattern."""
        safe_name = self._safe_filename(pattern_name)
        rel_path = f"procedural/patterns/{safe_name}.md"
        self.write(rel_path, pattern)

    def get_recent_conversations(self, limit: int = 5) -> list[str]:
        """Get recent conversation file paths."""
        files = self.list_files("episodic/conversations")
        files.sort(reverse=True)
        return files[:limit]

    def get_stats(self) -> dict[str, object]:
        """Get memory statistics."""
        stats: dict[str, object] = {
            "project": self.scope.project,
            "global_access": self.scope.global_access.value,
            "is_shared_memory": self.scope.is_shared_memory,
        }

        # Count files in each category
        categories = [
            ("conversations", "episodic/conversations"),
            ("events", "episodic/events"),
            ("entities_people", "semantic/entities/people"),
            ("entities_projects", "semantic/entities/projects"),
            ("entities_concepts", "semantic/entities/concepts"),
            ("patterns", "procedural/patterns"),
            ("corrections", "procedural/corrections"),
        ]

        for name, path in categories:
            try:
                stats[name] = len(self.list_files(path))
            except MemoryAccessError:
                stats[name] = 0

        return stats

    # ------------------------------------------------------------------
    # Contradiction management
    # ------------------------------------------------------------------

    CONTRADICTION_SCOPES = ("semantic", "procedural", "user")

    def _contradiction_path(
        self, scope: str, location: MemoryLocation = "project"
    ) -> Path:
        """Resolve the path to a contradictions.md file."""
        return self._resolve_path(f"{scope}/contradictions.md", location)

    def _next_contradiction_id(self) -> str:
        """Generate a sequential contradiction ID for today."""
        today = datetime.now().strftime("%Y-%m-%d")
        # Scan all scopes for existing IDs with today's date
        existing: list[int] = []
        for scope in self.CONTRADICTION_SCOPES:
            content = self.read(f"{scope}/contradictions.md")
            if content:
                for m in re.finditer(rf"### C-{today}-(\d+)", content):
                    existing.append(int(m.group(1)))
        seq = max(existing, default=0) + 1
        return f"C-{today}-{seq:03d}"

    def save_contradiction(
        self,
        scope: str,
        source_a_path: str,
        source_a_text: str,
        source_b_path: str,
        source_b_text: str,
        conflict_type: str,
        context: str = "",
        location: MemoryLocation = "project",
    ) -> str:
        """Catalog a new contradiction in the appropriate contradictions.md.

        Args:
            scope: "semantic", "procedural", or "user"
            source_a_path: Path to first source file
            source_a_text: Conflicting text from source A
            source_b_path: Path to second source file
            source_b_text: Conflicting text from source B
            conflict_type: "factual", "preference", "pattern", "entity"
            context: Where/when the contradiction was detected
            location: "project" or "global"

        Returns:
            The contradiction ID (e.g., "C-2026-04-14-001")
        """
        c_id = self._next_contradiction_id()
        now = datetime.now().isoformat()

        entry = (
            f"\n### {c_id}\n"
            f"- **Detected**: {now}\n"
            f'- **Source A**: [[{source_a_path}]] — "{source_a_text}"\n'
            f'- **Source B**: [[{source_b_path}]] — "{source_b_text}"\n'
            f"- **Type**: {conflict_type}\n"
            f"- **Context**: {context}\n"
        )

        rel_path = f"{scope}/contradictions.md"
        existing = self.read(rel_path, location) or ""

        if not existing.strip():
            existing = "# Contradictions\n\n## Unresolved\n"

        # Ensure Unresolved section exists
        if "## Unresolved" not in existing:
            existing += "\n## Unresolved\n"

        # Insert after "## Unresolved" header
        unresolved_idx = existing.index("## Unresolved")
        insert_at = existing.index("\n", unresolved_idx) + 1
        updated = existing[:insert_at] + entry + existing[insert_at:]

        self.write(rel_path, updated, location)
        return c_id

    def resolve_contradiction(
        self,
        scope: str,
        contradiction_id: str,
        resolution: str,
        resolved_by: str = "user",
        location: MemoryLocation = "project",
    ) -> bool:
        """Mark a contradiction as resolved.

        Moves the entry from Unresolved to Resolved section and adds
        resolution details.

        Args:
            scope: "semantic", "procedural", or "user"
            contradiction_id: e.g., "C-2026-04-14-001"
            resolution: Description of the resolution decision
            resolved_by: "user", "llm", "auto"
            location: "project" or "global"

        Returns:
            True if the contradiction was found and resolved.
        """
        rel_path = f"{scope}/contradictions.md"
        content = self.read(rel_path, location)
        if not content or contradiction_id not in content:
            return False

        now = datetime.now().isoformat()

        # Find the contradiction entry
        entry_start = content.index(f"### {contradiction_id}")

        # Find where this entry ends (next ### or ## or EOF)
        rest = content[entry_start + 1 :]
        next_section = len(content)
        for marker in ("### C-", "## Resolved", "## Unresolved"):
            idx = rest.find(marker)
            if idx != -1:
                next_section = entry_start + 1 + idx
                break

        entry_text = content[entry_start:next_section]

        # Add resolution details to the entry
        resolved_entry = entry_text.rstrip() + (
            f"\n- **Resolution**: {resolution}\n"
            f"- **Resolved at**: {now}\n"
            f"- **Resolved by**: {resolved_by}\n"
        )

        # Remove from current position
        content = content[:entry_start] + content[next_section:]

        # Ensure Resolved section exists
        if "## Resolved" not in content:
            content = content.rstrip() + "\n\n## Resolved\n"

        # Append to Resolved section
        resolved_idx = content.index("## Resolved")
        insert_at = content.index("\n", resolved_idx) + 1
        content = content[:insert_at] + "\n" + resolved_entry + content[insert_at:]

        self.write(rel_path, content, location)
        return True

    def load_contradictions(
        self,
        scope: str | None = None,
        unresolved_only: bool = True,
        location: MemoryLocation = "project",
    ) -> list[dict[str, str]]:
        """Load contradictions from contradictions.md files.

        Args:
            scope: Specific scope or None for all scopes
            unresolved_only: Only return unresolved contradictions
            location: "project" or "global"

        Returns:
            List of dicts with id, scope, source_a, source_b, type, etc.
        """
        scopes = [scope] if scope else list(self.CONTRADICTION_SCOPES)
        results: list[dict[str, str]] = []

        for s in scopes:
            content = self.read(f"{s}/contradictions.md", location)
            if not content:
                continue

            # Parse entries from the Unresolved section
            in_unresolved = False
            in_resolved = False
            current_entry: dict[str, str] = {}

            for line in content.split("\n"):
                if line.strip() == "## Unresolved":
                    in_unresolved = True
                    in_resolved = False
                    continue
                elif line.strip() == "## Resolved":
                    in_unresolved = False
                    in_resolved = True
                    # Save last entry from unresolved
                    if current_entry.get("id"):
                        current_entry["scope"] = s
                        results.append(current_entry)
                        current_entry = {}
                    continue

                if not in_unresolved and (unresolved_only or not in_resolved):
                    continue

                if line.startswith("### C-"):
                    # Save previous entry
                    if current_entry.get("id"):
                        current_entry["scope"] = s
                        results.append(current_entry)
                    current_entry = {"id": line[4:].strip()}
                elif line.startswith("- **Detected**:"):
                    current_entry["detected"] = line.split(":", 1)[1].strip()
                elif line.startswith("- **Source A**:"):
                    current_entry["source_a"] = line.split(":", 1)[1].strip()
                elif line.startswith("- **Source B**:"):
                    current_entry["source_b"] = line.split(":", 1)[1].strip()
                elif line.startswith("- **Type**:"):
                    current_entry["type"] = line.split(":", 1)[1].strip()
                elif line.startswith("- **Context**:"):
                    current_entry["context"] = line.split(":", 1)[1].strip()

            # Save last entry
            if current_entry.get("id"):
                current_entry["scope"] = s
                results.append(current_entry)

        return results

    def get_related_entries(
        self,
        content: str,
        location: MemoryLocation = "project",
    ) -> list[tuple[str, str]]:
        """Find existing memory entries that may relate to new content.

        Searches facts, entities, and patterns for keyword overlap.
        Returns list of (path, content_preview) tuples.
        """
        # Extract keywords (words > 3 chars, lowercased)
        words = set(w.lower() for w in re.split(r"\W+", content) if len(w) > 3)
        if not words:
            return []

        related: list[tuple[str, str]] = []

        # Check relationships.md directly (not in a search dir)
        rel_content = self.read("semantic/relationships.md", location)
        if rel_content:
            rel_lower = rel_content.lower()
            rel_matches = sum(1 for w in words if w in rel_lower)
            if rel_matches >= 2:
                preview = rel_content[:200].replace("\n", " ")
                related.append(("semantic/relationships.md", preview))

        search_dirs = [
            "semantic/facts",
            "semantic/entities/people",
            "semantic/entities/projects",
            "semantic/entities/concepts",
            "procedural/patterns",
            "user",
        ]

        max_related = 10  # Cap to avoid excessive I/O
        for dir_path in search_dirs:
            if len(related) >= max_related:
                break
            for fname in self.list_files(dir_path, location):
                if len(related) >= max_related:
                    break
                if not fname.endswith((".md", ".json", ".txt")):
                    continue
                try:
                    file_content = self.read(f"{dir_path}/{fname}", location)
                    if not file_content:
                        continue
                    file_lower = file_content.lower()
                    # Check for keyword overlap
                    matches = sum(1 for w in words if w in file_lower)
                    if matches >= 2:  # At least 2 keyword matches
                        preview = file_content[:200].replace("\n", " ")
                        related.append((f"{dir_path}/{fname}", preview))
                except (UnicodeDecodeError, OSError):
                    continue

        return related


class MultiProjectMemoryStore:
    """
    Manages memory access across multiple projects in a session.

    Use this when SessionScope contains multiple projects.
    """

    def __init__(self, config: Config, scope: SessionScope):
        self.config = config
        self.scope = scope

        self._stores: dict[str, MemoryStore] = {}
        for project_scope in scope.projects:
            self._stores[project_scope.project] = MemoryStore(config, project_scope)

    def get_store(self, project: str) -> MemoryStore:
        """Get the MemoryStore for a specific project."""
        if project not in self._stores:
            raise ValueError(f"Project '{project}' not in session scope")
        return self._stores[project]

    @property
    def stores(self) -> dict[str, MemoryStore]:
        """All project stores."""
        return self._stores

    def load_all_user_contexts(self) -> dict[str, UserContext]:
        """Load user context for all projects."""
        return {
            project: store.load_user_context()
            for project, store in self._stores.items()
        }
