# Quick Start

## 1. Initialize Your First Project

```bash
archivyst init my-project
```

This creates:
- Config file at `~/.config/archivyst/config.yaml`
- Knowledge vault at `~/obsidian-vaults/my-project/`
- Shared memory vault at `~/obsidian-vaults/agent-memory/`
- Project memory at `agent-memory/projects/my-project/`
- Git versioning for memory directories

## 2. Add Notes to Your Vault

Add markdown files to your knowledge vault, or point to an existing Obsidian vault:

```bash
archivyst init work --vault ~/Documents/work-notes
```

## 3. Build the Index

```bash
# Interactive — detects whether index exists and offers options
archivyst index

# Or explicitly rebuild
archivyst index rebuild
```

The index step:
- Parses all markdown files and extracts structure
- Generates per-section LLM summaries
- Builds ToC trees in `.pageindex/` directory
- Generates vault meta-summary and extracts entities to memory
- Checks for contradictions against existing memory
- Logs a system event to episodic memory

## 4. Start a Session

```bash
# Start interactive session
archivyst session

# With verbose output (shows memory loading, retrieval traces)
archivyst -v session

# Start in lax mode (model can answer beyond vault content)
archivyst session --mode lax
```

On session start, Archivyst:
- Loads all memory layers (preferences, profile, entities, facts, patterns, corrections, vault meta)
- Sends a memory briefing to the LLM to establish context
- Displays what was loaded (in verbose mode)

## 5. Query Your Knowledge

```bash
# One-shot query
archivyst query "What are the key decisions from last quarter?"

# Interactive session (recommended)
archivyst session
```
