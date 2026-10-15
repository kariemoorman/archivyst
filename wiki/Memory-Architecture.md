# Memory Architecture

Archivyst maintains a cognitive memory system that persists across sessions. The agent learns preferences, remembers entities, and builds context over time.

## Memory Structure

```
agent-memory/
├── global/                              # Cross-project knowledge
│   ├── user/
│   │   ├── preferences.md
│   │   └── profile.md
│   ├── semantic/
│   │   ├── entities/
│   │   │   ├── people/
│   │   │   ├── projects/
│   │   │   └── concepts/
│   │   ├── facts/
│   │   ├── relationships.md
│   │   └── contradictions.md
│   ├── procedural/
│   │   ├── patterns/
│   │   ├── corrections/
│   │   └── promotions.jsonl
│   └── .git/
│
├── projects/
│   └── <project-name>/
│       ├── user/
│       │   └── preferences.md
│       ├── episodic/
│       │   ├── conversations/
│       │   └── events/
│       ├── semantic/
│       │   ├── entities/
│       │   ├── vaults/
│       │   ├── facts/
│       │   ├── relationships.md
│       │   └── contradictions.md
│       ├── procedural/
│       │   ├── patterns/
│       │   └── corrections/
│       ├── working/
│       │   ├── sessions/
│       │   └── queue/
│       ├── attachments/
│       └── .git/
```

## Memory Types

### Episodic Memory
- **Conversations**: Full transcripts saved at session end
- **Events**: System events (index rebuilds, errors, discoveries, learning moments, organization results)

### Semantic Memory
- **Entities**: People, projects, concepts — all stored as markdown with wikilinks
- **Vault Meta**: Understanding of what each vault contains (generated after indexing)
- **Facts**: Specific learned facts about vault content
- **Relationships**: Knowledge graph linking entities via wikilinks (`semantic/relationships.md`)
- **Contradictions**: Detected conflicts between facts (`semantic/contradictions.md`)

### Procedural Memory
- **Patterns**: Learned behavior patterns
- **Corrections**: User corrections to agent behavior

### Working Memory
- **Sessions**: Per-turn Q&A pairs saved during active sessions
- **Queue**: Pending tasks

## Memory Lifecycle

```
During Session (per turn, automatic)
    │
    ├── Query/response saved to working/sessions/<id>/
    ├── LLM extracts entities → semantic/entities/
    ├── LLM extracts facts, preferences, corrections, patterns
    ├── Contradiction check against existing memory
    └── Events logged to episodic/events/
    │
    ▼
Session End (automatic)
    │
    ├── Full conversation saved to episodic/conversations/
    └── End-of-session batch extraction
    │
    ▼
archivyst memory promote (manual)
    │
    ├── Marks sessions as promoted in working/promoted.json
    └── Promotes project memory → global (if global_access: full)
    │
    ▼
archivyst memory organize (manual)
    │
    ├── Quick: marks unpromoted sessions + promotes to global
    └── Full: LLM review for duplicates, conflicts, stale entries
            + extract entity relationships → semantic/relationships.md
            + add bidirectional links to entity files
    │
    ▼
archivyst memory prune (manual)
    │
    ├── Identify sessions beyond retention limit (default: 5)
    ├── If prune_before_delete: mark unpromoted sessions first
    ├── Git commit (pre-prune snapshot for recovery)
    └── Delete excess sessions, keeping most recent 5
```

## Knowledge Graph

During full-depth organization, Archivyst identifies relationships between entities and saves them as wikilinks in `semantic/relationships.md`. These relationships also get added as bidirectional links in each entity file under a `# Links` section.

Generate or update the knowledge graph:
```bash
archivyst memory organize --depth full
archivyst memory organize --global --depth full
```

## Memory Versioning

Memory directories are automatically versioned with git. Commit timing:
- **During sessions**: Per-turn extractions accumulate; batch commit at session end
- **CLI commands**: `promote`, `organize`, `prune`, `lobotomy`, `/remember` each trigger an immediate commit
- **Indexing**: Commits after vault meta-summary and entity extraction

```bash
archivyst memory history
archivyst memory history --path semantic/entities
archivyst memory history --global
```

## Memory Lobotomy

Remove specific topics or perform a full memory wipe:

```bash
archivyst memory lobotomy
archivyst memory lobotomy --dry-run
```

Both operations preserve version history — recoverable via `archivyst memory history`.
