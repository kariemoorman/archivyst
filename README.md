<h1 align='center'>archivyst</h1>

<p align='center'><img src='assets/archivyst.png' width=100></p>

---

## Description

Archivyst mimics human document reading by constructing hierarchical Table of Contents (ToC) trees instead of vector similarity search. It uses local LLMs via LM Studio for reasoning-based retrieval with explainable navigation paths.

## Key Features

- **No Vector Database** - Relies on document structure + LLM reasoning instead of embeddings
- **No Chunking** - Uses natural structural boundaries (headings, sections)
- **Explainable Retrieval** - Traceable navigation paths show how content was found
- **Local LLM** - Uses LM Studio's OpenAI-compatible API for privacy
- **VLM Support** - Describes images and diagrams using vision models
- **Cognitive Memory** - Persistent agent memory across sessions

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Archivyst Agent                         │
├─────────────────────────────────────────────────────────────────┤
│  Query Analysis  →  Routing  →  ToC Traversal  →  Synthesis     │
└─────────────────────────────────────────────────────────────────┘
         │               │              │               │
         ▼               ▼              ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌──────────┐  ┌─────────────┐
│   Memory    │  │ Vault Index │  │ Wikilink │  │  LM Studio  │
│   Store     │  │ (ToC Trees) │  │  Graph   │  │     API     │
└─────────────┘  └─────────────┘  └──────────┘  └─────────────┘
```

## Installation

```bash
# Clone and install
git clone https://github.com/yourname/archivyst
cd archivyst
pip install -e .

# Or install from pip (when published)
pip install archivyst
```

### Requirements

- Python 3.13+
- LM Studio running with API server enabled (default: http://localhost:1234)
- Recommended models:
  - Summarization/Routing: `qwen2.5-7b-instruct`
  - Synthesis: `qwen2.5-14b-instruct`
  - Vision: `qwen3-vl` (for image descriptions)

## Quick Start

### 1. Initialize Configuration

```bash
archivyst config init
```

This creates `~/.config/archivyst/config.yaml`. Edit it to configure your vaults:

```yaml
vaults:
  knowledge:
    - name: my-notes
      path: ~/vaults/my-notes
      active: true

  memory:
    path: ~/vaults/agent-memory

llm:
  base_url: http://localhost:1234/v1
  models:
    summarization: qwen2.5-7b-instruct
    routing: qwen2.5-7b-instruct
    synthesis: qwen2.5-14b-instruct
    vision: qwen3-vl
```

### 2. Build the Index

```bash
# Index all active vaults
archivyst index rebuild

# Index a specific vault
archivyst index rebuild --vault my-notes

# Full rebuild (ignore incremental)
archivyst index rebuild --full
```

### 3. Query Your Knowledge Base

```bash
# Single query
archivyst query "What are the key decisions from our Q4 planning?"

# Interactive session
archivyst session
```

## CLI Reference

```
archivyst config init|show|validate
archivyst vault list|add|activate|deactivate|status [name]
archivyst index rebuild|status|check [--vault=name] [--full]
archivyst query "question" [--vault=X] [--show-sources] [--show-trace]
archivyst session [--vault=X]
archivyst memory show|tree|prune|conversations|entities
```

Global options:
- `--config=<path>` - Custom config file path
- `--format={human,json}` - Output format
- `--verbose` - Enable verbose output

## How It Works

### 1. Indexing

Archivyst extracts structure from your Obsidian notes:

- **Headings** → Hierarchical ToC tree
- **Wikilinks** → Connection graph
- **Frontmatter** → Aliases and metadata
- **Images/PDFs** → VLM descriptions (optional)

Each note gets an LLM-generated summary stored alongside its ToC.

### 2. Retrieval

When you ask a question:

1. **Query Analysis** - Determine query type and relevant vaults
2. **Document Routing** - LLM selects most relevant notes from summaries
3. **ToC Traversal** - LLM navigates heading trees to find specific sections
4. **Link Expansion** - Follow wikilinks to discover related content
5. **Synthesis** - Generate answer from retrieved sections with citations

### 3. Memory

Archivyst maintains cognitive memory:

```
agent-memory/
├── user/                    # Preferences, profile
├── semantic/
│   ├── vaults/             # Meta-knowledge per vault
│   ├── entities/           # People, projects, concepts
│   └── facts/
├── episodic/
│   ├── conversations/      # Saved sessions
│   └── events/
├── procedural/
│   ├── patterns/           # Learned behaviors
│   └── corrections/
└── working/                # Current session state
```

## Python API

```python
from archivyst import load_config
from archivyst.agent import ArchivystAgent, MemoryStore
from archivyst.core import VaultManager
from archivyst.llm import LLMClient

config = load_config()

async with LLMClient(config.llm) as llm:
    vaults = VaultManager(config, llm)
    memory = MemoryStore(config)
    agent = ArchivystAgent(config, llm, vaults, memory)
    
    await agent.start_session()
    
    # Simple query
    answer = await agent.query("What did I write about project X?")
    print(answer)
    
    # With retrieval details
    retrieval = await agent.retrieve("project deadlines")
    for source in retrieval.sources:
        print(f"Found: {source.note_path} > {source.section_heading}")
    
    await agent.end_session()
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests excluding integration tests (no LM Studio needed)
pytest -m "not integration"

# Type checking
mypy src/archivyst

# Linting
ruff check src/archivyst
```

## Configuration Reference

See `config.example.yaml` for all options. Key settings:

| Setting | Description | Default |
|---------|-------------|---------|
| `llm.base_url` | LM Studio API endpoint | `http://localhost:1234/v1` |
| `indexing.strategy` | `incremental` or `full` | `incremental` |
| `indexing.summarize_sections` | Generate LLM summaries | `true` |
| `indexing.wikilink_resolution` | Link matching strategy | `case_insensitive_with_aliases` |
| `pdf.enabled` | Process PDF attachments | `true` |
| `images.vlm_descriptions` | Describe images with VLM | `true` |

## Comparison to Vector RAG

| Aspect | Vector RAG | Archivyst |
|--------|-----------|-----------|
| Retrieval | Embedding similarity | LLM reasoning on structure |
| Chunking | Fixed size / semantic | Natural boundaries |
| Explainability | Low (similarity scores) | High (navigation trace) |
| Context | Isolated chunks | Full document structure |
| Updates | Re-embed changed docs | Re-index ToC only |
| Dependencies | Vector DB + embeddings | LM Studio only |

## License

APGL-3.0

## Acknowledgments

- Inspired by the way humans actually read and navigate documents
- Built for use with [Obsidian](https://obsidian.md) vaults
- Powered by [LM Studio](https://lmstudio.ai) for local LLM inference
