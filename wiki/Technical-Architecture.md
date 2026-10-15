# Technical Architecture

## Module Dependencies

```
archivyst/
├── __init__.py
├── config.py              # Config loading utilities
│
├── core/
│   ├── models.py          # Pydantic models (Config, ToC, Scope, Memory, etc.)
│   ├── markdown.py        # Markdown parsing, heading extraction
│   ├── summarizer.py      # LLM-based summarization
│   ├── media.py           # PDF/image processing
│   ├── index.py           # VaultIndex, VaultManager
│   ├── code_parser.py     # Source code structure extraction (ast, tree-sitter)
│   └── code_converter.py  # Code-to-markdown assembly
│
├── retrieval/
│   ├── router.py          # Note selection via LLM
│   ├── traverser.py       # ToC tree traversal
│   └── linker.py          # Wikilink graph operations
│
├── agent/
│   ├── orchestrator.py    # ArchivystAgent — main query loop
│   ├── memory.py          # MemoryStore with scoping + versioning
│   ├── versioning.py      # Git-based memory versioning
│   ├── extraction.py      # Entity/fact extraction + contradiction detection
│   ├── prompt_builder.py  # Composable prompts with context budget
│   └── prompts.py         # All prompt templates
│
├── llm/
│   ├── client.py          # LLM API client (OpenAI-compatible)
│   └── utils.py           # JSON response parsing
│
└── cli/
    ├── main.py            # CLI entry point + completions
    ├── config_cmd.py      # config subcommands + init
    ├── project_cmd.py     # project subcommands
    ├── vault_cmd.py       # vault subcommands
    ├── index_cmd.py       # index subcommands + pre-index scan
    ├── query_cmd.py       # query + session + /commands
    ├── memory_cmd.py      # memory subcommands (promote, organize, prune, lobotomy, reconcile)
    ├── process_cmd.py     # file processing / import
    ├── scan_cmd.py        # vault code/image scanning
    ├── utils.py           # shared CLI utilities
    └── obsidian_defaults.py  # Obsidian vault JSON constants
```

## Key Design Decisions

- **No vector database** — ToC traversal + fuzzy matching replaces similarity search
- **Local-first** — all processing via OpenAI-compatible API (LM Studio, Ollama, llama.cpp)
- **Markdown everywhere** — memory, entities, facts, relationships all stored as `.md`
- **Git versioning** — memory changes tracked via git for recovery
- **Pydantic v2** — all config and LLM response validation
- **Async throughout** — all LLM calls are async via httpx
- **Task-based model routing** — different models for different tasks (routing vs synthesis vs vision)
