"""
Core data models for Archivyst.

Uses Pydantic v2 with Python 3.13+ type syntax.
"""

import os
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, field_validator, model_validator

FrontmatterValue = str | int | float | bool | list[str] | None


class MemoryFormat(StrEnum):
    """Format for agent-created notes."""

    MARKDOWN = "markdown"
    JSON = "json"


class GlobalAccess(StrEnum):
    """Global memory access level for a project."""

    FULL = "full"  # read + promote to global
    READ = "read"  # read global, write only to project
    NONE = "none"  # completely isolated from global


class WikilinkResolution(StrEnum):
    """Strategy for resolving [[wikilinks]]."""

    STRICT = "strict"
    CASE_INSENSITIVE = "case_insensitive"
    CASE_INSENSITIVE_WITH_ALIASES = "case_insensitive_with_aliases"


class IndexStrategy(StrEnum):
    """Index rebuild strategy."""

    INCREMENTAL = "incremental"
    FULL = "full"


# --- ToC Tree Models ---


class SectionNode(BaseModel):
    """A node in the Table of Contents tree."""

    heading: str
    level: int  # 1-6 for h1-h6
    start_line: int
    end_line: int | None = None  # None if extends to next heading or EOF
    summary: str | None = None  # LLM-generated summary
    children: list["SectionNode"] = Field(default_factory=list)

    def flatten(self) -> list["SectionNode"]:
        """Return all nodes in depth-first order."""
        result = [self]
        for child in self.children:
            result.extend(child.flatten())
        return result


class NoteToC(BaseModel):
    """Table of Contents for a single note."""

    path: Path
    title: str  # From H1 or filename
    summary: str | None = None  # LLM-generated overall summary
    sections: list[SectionNode] = Field(default_factory=list)
    wikilinks: list[str] = Field(default_factory=list)  # Outgoing [[links]]
    backlinks: list[Path] = Field(default_factory=list)  # Notes linking to this
    images: list[str] = Field(default_factory=list)  # Embedded images
    attachments: list[str] = Field(default_factory=list)  # PDFs, etc.
    frontmatter: dict[str, FrontmatterValue] = Field(default_factory=dict)
    mtime: datetime
    indexed_at: datetime = Field(default_factory=datetime.now)

    @property
    def aliases(self) -> list[str]:
        """Extract aliases from frontmatter."""
        aliases = self.frontmatter.get("aliases", [])
        if isinstance(aliases, str):
            return [aliases]
        if isinstance(aliases, list):
            return [str(a) for a in aliases]
        return []


class ImageDescription(BaseModel):
    """VLM-generated description of an image."""

    path: str
    description: str
    model: str
    processed_at: datetime = Field(default_factory=datetime.now)


class AttachmentSummary(BaseModel):
    """LLM-generated summary of a PDF/attachment."""

    source_path: str
    summary: str
    structure: list[str] | None = None  # ToC if extractable
    references: list[Path] = Field(default_factory=list)  # Notes referencing this
    model: str
    processed_at: datetime = Field(default_factory=datetime.now)


# --- Vault Models ---


class VaultConfig(BaseModel):
    """Configuration for a single knowledge vault."""

    name: str
    path: Path
    active: bool = True

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser().resolve()


class VaultManifest(BaseModel):
    """Index manifest for a vault."""

    vault_name: str
    vault_path: Path
    note_count: int
    last_indexed: datetime
    index_version: str = "1.0"
    notes: dict[str, datetime] = Field(default_factory=dict)  # path -> mtime


# --- Query/Retrieval Models ---


class RetrievalSource(BaseModel):
    """A source cited in a retrieval result."""

    project: str
    vault: str
    note_path: Path
    section_heading: str | None = None
    relevance_reason: str


class RetrievalResult(BaseModel):
    """Result from the Archivyst retrieval process."""

    query: str
    projects: list[str]
    sources: list[RetrievalSource]
    reasoning_trace: list[str]  # Steps taken to find sources
    content: dict[str, str]  # path -> extracted content


class RoutingDecision(BaseModel):
    """LLM decision about which notes to explore."""

    selected_notes: list[Path]
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class TraversalStep(BaseModel):
    """A step in the ToC traversal process."""

    note_path: Path
    section_path: list[str]  # Heading hierarchy
    action: str  # "expand", "select", "skip"
    reasoning: str


# --- Session/Scope Models ---


class ProjectScope(BaseModel):
    """Defines access boundaries for a project in a session."""

    project: str
    knowledge_vaults: list[str]
    memory_root: Path
    is_shared_memory: bool
    global_access: GlobalAccess

    @property
    def project_memory_path(self) -> Path:
        """Path to this project's memory directory."""
        if self.is_shared_memory:
            return self.memory_root / "projects" / self.project
        return self.memory_root

    @property
    def global_memory_path(self) -> Path | None:
        """Path to global memory, or None if not accessible."""
        if not self.is_shared_memory or self.global_access == GlobalAccess.NONE:
            return None
        return self.memory_root / "global"

    def can_read_path(self, path: Path) -> bool:
        """Check if path is readable in this scope."""
        try:
            if path.is_relative_to(self.project_memory_path):
                return True
        except ValueError:
            pass

        if self.global_memory_path and self.global_access in (
            GlobalAccess.FULL,
            GlobalAccess.READ,
        ):
            try:
                if path.is_relative_to(self.global_memory_path):
                    return True
            except ValueError:
                pass

        return False

    def can_write_path(self, path: Path) -> bool:
        """Check if path is writable in this scope."""
        try:
            if path.is_relative_to(self.project_memory_path):
                return True
        except ValueError:
            pass

        if self.global_memory_path and self.global_access == GlobalAccess.FULL:
            try:
                if path.is_relative_to(self.global_memory_path):
                    return True
            except ValueError:
                pass

        return False


class SessionScope(BaseModel):
    """Multi-project session scope."""

    projects: list[ProjectScope]

    @property
    def all_knowledge_vaults(self) -> set[str]:
        """All knowledge vaults accessible in this session."""
        vaults: set[str] = set()
        for p in self.projects:
            vaults.update(p.knowledge_vaults)
        return vaults

    @property
    def project_names(self) -> list[str]:
        """Names of all projects in scope."""
        return [p.project for p in self.projects]

    def can_read_vault(self, vault_name: str) -> bool:
        """Check if a vault is readable in this session."""
        return vault_name in self.all_knowledge_vaults

    def get_project_scope(self, project: str) -> ProjectScope | None:
        """Get scope for a specific project."""
        for p in self.projects:
            if p.project == project:
                return p
        return None


# --- Memory Models ---


class UserContext(BaseModel):
    """User context loaded at session start."""

    preferences: str | None = None
    profile: str | None = None
    patterns: str | None = None
    corrections: str | None = None
    entities: str | None = None
    facts: str | None = None
    vault_meta: str | None = None


class ConversationTurn(BaseModel):
    """A single turn in a conversation."""

    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


class SessionState(BaseModel):
    """State of an active session."""

    session_id: str
    projects: list[str]
    scope: SessionScope
    started_at: datetime = Field(default_factory=datetime.now)
    conversation: list[ConversationTurn] = Field(default_factory=list)
    working_context: dict[str, object] = Field(default_factory=dict)


class MemoryEntry(BaseModel):
    """An entry in the agent's memory."""

    path: Path
    content: str
    format: MemoryFormat
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    source: str  # "explicit", "inferred", "observed"


# --- LLM Response Models ---


class EntityExtractionResponse(BaseModel):
    """Validated structure for entity extraction LLM responses."""

    people: list[dict[str, str]] = Field(default_factory=list)
    projects: list[dict[str, str]] = Field(default_factory=list)
    concepts: list[dict[str, str]] = Field(default_factory=list)


class MemoryExtractionResponse(BaseModel):
    """Validated structure for memory extraction LLM responses."""

    preferences: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)


# --- Config Models ---


class ProjectMemoryConfig(BaseModel):
    """Per-project memory configuration."""

    path: Path | None = None  # None = use shared memory vault
    global_access: GlobalAccess = GlobalAccess.FULL

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path | None) -> Path | None:
        if v is None:
            return None
        return Path(v).expanduser().resolve()

    @model_validator(mode="after")
    def separate_path_implies_no_global(self) -> Self:
        """Separate memory path implies no global access."""
        if self.path is not None:
            self.global_access = GlobalAccess.NONE
        return self


class ProjectConfig(BaseModel):
    """Configuration for a single project."""

    name: str
    vaults: list[VaultConfig]
    memory: ProjectMemoryConfig = Field(default_factory=ProjectMemoryConfig)
    response_mode: Literal["strict", "lax"] = "strict"

    def get_active_vaults(self) -> list[VaultConfig]:
        """Return active vaults for this project."""
        return [v for v in self.vaults if v.active]


class WorkingMemoryConfig(BaseModel):
    """Working memory settings."""

    retention_sessions: int = 5
    promotion_interval_minutes: int = 30
    auto_promote: bool = False
    prune_before_delete: bool = True


class OrganizeDepth(StrEnum):
    """Depth of memory organization pass."""

    QUICK = "quick"
    FULL = "full"


class MemoryConfig(BaseModel):
    """Global/shared memory vault configuration."""

    path: Path
    working: WorkingMemoryConfig = Field(default_factory=WorkingMemoryConfig)
    organize_depth: OrganizeDepth = OrganizeDepth.QUICK

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser().resolve()


class LLMModelsConfig(BaseModel):
    """Model assignments per task."""

    summarization: str = "qwen2.5-7b-instruct"
    routing: str = "qwen2.5-7b-instruct"
    synthesis: str = "qwen2.5-14b-instruct"
    vision: str = "qwen3-vl"
    memory: str = "qwen2.5-7b-instruct"


class TokenEntry(BaseModel):
    """Token allocation for a single LLM operation."""

    max_tokens: int = 500
    temperature: float = 0.3


class SummarizationTokenEntry(BaseModel):
    """Token allocation for indexing summarization (two LLM calls)."""

    max_section_tokens: int = 150
    max_note_tokens: int = 200
    temperature: float = 0.3
    max_section_chars: int = 2000  # input content per section
    max_note_chars: int = 3000  # input content per note


class RetrievalTokenEntry(BaseModel):
    """Context budget settings for retrieval pipeline."""

    max_context_chars: int = 8000
    max_briefing_chars: int = 24000
    short_file_chars: int = 4000
    max_query_length: int = 10000
    heading_match_threshold: float = 0.5
    content_match_threshold: float = 0.4


class TokenAllocationConfig(BaseModel):
    """Per-operation token allocation and LLM settings."""

    # Session
    synthesis: TokenEntry = Field(
        default_factory=lambda: TokenEntry(max_tokens=4000, temperature=0.7)
    )
    routing: TokenEntry = Field(default_factory=lambda: TokenEntry(max_tokens=300))
    briefing: TokenEntry = Field(default_factory=lambda: TokenEntry(max_tokens=50))
    session_extraction: TokenEntry = Field(
        default_factory=lambda: TokenEntry(max_tokens=500)
    )

    # Indexing
    summarization: SummarizationTokenEntry = Field(
        default_factory=SummarizationTokenEntry
    )
    entity_extraction: TokenEntry = Field(
        default_factory=lambda: TokenEntry(max_tokens=500)
    )
    memory_extraction: TokenEntry = Field(
        default_factory=lambda: TokenEntry(max_tokens=500)
    )

    # Memory management
    organize: TokenEntry = Field(default_factory=lambda: TokenEntry(max_tokens=4000))
    lobotomy_scan: TokenEntry = Field(
        default_factory=lambda: TokenEntry(max_tokens=1000)
    )

    # Code processing
    code_description: TokenEntry = Field(
        default_factory=lambda: TokenEntry(max_tokens=150)
    )
    code_summary: TokenEntry = Field(default_factory=lambda: TokenEntry(max_tokens=150))

    # Contradiction detection and resolution
    contradiction_check: TokenEntry = Field(
        default_factory=lambda: TokenEntry(max_tokens=500)
    )
    contradiction_resolve: TokenEntry = Field(
        default_factory=lambda: TokenEntry(max_tokens=1000)
    )

    # Retrieval context budget
    retrieval: RetrievalTokenEntry = Field(default_factory=RetrievalTokenEntry)


class LLMConfig(BaseModel):
    """LLM API configuration."""

    base_url: str = "http://localhost:1234/v1"
    api_key: str | None = None
    models: LLMModelsConfig = Field(default_factory=LLMModelsConfig)
    vision_base_url: str | None = None
    timeout: int = 120
    max_retries: int = 3
    min_request_interval: float = 0.1  # seconds between requests

    @field_validator("api_key", mode="before")
    @classmethod
    def expand_api_key(cls, v: str | None) -> str | None:
        """Expand environment variable references in api_key.

        Supports ${VAR_NAME} syntax. Also checks common env var
        fallbacks if no key is configured.
        """
        if v is None:
            # Check common environment variable fallbacks
            for env_var in ("ARCHIVYST_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
                key = os.environ.get(env_var)
                if key:
                    return key
            return None

        if not isinstance(v, str):
            return v

        # Expand ${VAR_NAME} patterns
        def _expand(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), match.group(0))

        expanded = re.sub(r"\$\{(\w+)\}", _expand, v)
        return expanded if expanded else None


class ProcessConfig(BaseModel):
    """File processing/conversion configuration."""

    supported_formats: list[str] = Field(
        default_factory=lambda: [
            "md",
            "txt",
            "csv",
            "tsv",
            "json",
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "gif",
            "webp",
            # Code files
            "py",
            "c",
            "h",
            "cpp",
            "hpp",
            "java",
            "js",
            "ts",
            "go",
            "rs",
            "rb",
            "sql",
            "cs",
            "swift",
            "kt",
            "sh",
            "bash",
            "lua",
            "php",
            "r",
            "cob",
            "cbl",
        ]
    )
    exclude_patterns: list[str] = Field(
        default_factory=lambda: ["*.tmp", ".DS_Store", "__MACOSX"]
    )
    max_code_file_size: int = 512_000  # 500KB — skip code files larger than this


class PDFConfig(BaseModel):
    """PDF processing configuration."""

    enabled: bool = True
    ocr_fallback: bool = True
    store_summaries: bool = True
    max_summary_chars: int = 8000  # max chars for PDF summary


class ImagesConfig(BaseModel):
    """Image processing configuration."""

    max_image_size: int = 50_000_000  # 50MB max image file size

    vlm_descriptions: bool = True
    formats: list[str] = Field(
        default_factory=lambda: ["png", "jpg", "jpeg", "gif", "webp"]
    )


class IndexingConfig(BaseModel):
    """Indexing configuration."""

    strategy: IndexStrategy = IndexStrategy.INCREMENTAL
    summarize_sections: bool = True
    scan_code: bool = True  # Scan for code files before indexing
    wikilink_resolution: WikilinkResolution = (
        WikilinkResolution.CASE_INSENSITIVE_WITH_ALIASES
    )
    exclude_patterns: list[str] = Field(
        default_factory=lambda: ["*.excalidraw.md", ".obsidian/*", ".trash/*"]
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = "INFO"
    file: Path | None = None
    max_file_size: int = 5_000_000  # 5MB — rotate when exceeded
    backup_count: int = 3  # number of rotated backups to keep


class Config(BaseModel):
    """Root configuration model."""

    memory: MemoryConfig
    projects: list[ProjectConfig] = Field(default_factory=list)
    default_project: str | None = None
    llm: LLMConfig = Field(default_factory=LLMConfig)
    process: ProcessConfig = Field(default_factory=ProcessConfig)
    token_allocation: TokenAllocationConfig = Field(
        default_factory=TokenAllocationConfig
    )
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    pdf: PDFConfig = Field(default_factory=PDFConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def load(cls, path: Path) -> Self:
        """Load configuration from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def get_project(self, name: str) -> ProjectConfig | None:
        """Get a project by name."""
        for project in self.projects:
            if project.name == name:
                return project
        return None

    def get_project_names(self) -> list[str]:
        """Get all project names."""
        return [p.name for p in self.projects]

    def get_default_project(self) -> ProjectConfig | None:
        """Get the default project if set."""
        if self.default_project:
            return self.get_project(self.default_project)
        if self.projects:
            return self.projects[0]
        return None

    def get_default_project_vaults(self) -> list[VaultConfig]:
        """Get vaults from the default project, or empty list if none."""
        project = self.get_default_project()
        return project.vaults if project else []

    def build_project_scope(self, project_name: str) -> ProjectScope:
        """Build a ProjectScope for a given project."""
        project = self.get_project(project_name)
        if not project:
            raise ValueError(f"Project '{project_name}' not found")

        # Determine memory configuration
        if project.memory.path:
            # Separate memory vault
            memory_root = project.memory.path
            is_shared = False
        else:
            # Shared memory vault
            memory_root = self.memory.path
            is_shared = True

        return ProjectScope(
            project=project_name,
            knowledge_vaults=[v.name for v in project.get_active_vaults()],
            memory_root=memory_root,
            is_shared_memory=is_shared,
            global_access=project.memory.global_access,
        )

    def build_session_scope(self, project_names: list[str]) -> SessionScope:
        """Build a SessionScope for multiple projects."""
        scopes = [self.build_project_scope(name) for name in project_names]
        return SessionScope(projects=scopes)
