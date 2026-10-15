# Configuration

## Config File Location

Archivyst looks for configuration in this order:
1. `--config` flag
2. `./archivyst.yaml` (current directory)
3. `./config.yaml` (current directory)
4. `~/.config/archivyst/config.yaml` (default)

## Projects

A **project** groups related knowledge vaults and has its own memory space. Use projects to:
- Separate work and personal knowledge
- Isolate client-specific information
- Control what the agent can access during a session

## Vaults

A **vault** is an Obsidian-compatible directory of markdown files. Each project can have multiple vaults:
- `active: true` vaults are indexed and searchable
- `active: false` vaults are ignored until activated

## Global Access Levels

| Level | Read Global | Write Global | Use Case |
|-------|-------------|--------------|----------|
| `full` | Yes | Yes (via promote) | Personal projects, shared context |
| `read` | Yes | No | Work projects, consume but don't modify |
| `none` | No | No | Client projects, complete isolation |

## Response Mode

Each project can set a default response mode:

- **`strict`** (default) — answers only from vault content and memory
- **`lax`** — uses vault content if relevant, plus the model's own knowledge

Override with `--mode lax` on `query` or `session` commands, or toggle mid-session with `/mode`.

## Token Allocation

All LLM token limits and temperatures are configurable per operation under `token_allocation:`:

```yaml
token_allocation:
  synthesis:           { max_tokens: 4000, temperature: 0.7 }
  routing:             { max_tokens: 300,  temperature: 0.3 }
  briefing:            { max_tokens: 50,   temperature: 0.3 }
  session_extraction:  { max_tokens: 1000, temperature: 0.3 }
  summarization:
    max_section_tokens: 150
    max_note_tokens: 200
    temperature: 0.3
    max_section_chars: 2000
    max_note_chars: 3000
  entity_extraction:   { max_tokens: 500,  temperature: 0.3 }
  memory_extraction:   { max_tokens: 500,  temperature: 0.3 }
  organize:            { max_tokens: 4000, temperature: 0.3 }
  lobotomy_scan:       { max_tokens: 1000, temperature: 0.3 }
  code_description:    { max_tokens: 150,  temperature: 0.3 }
  code_summary:        { max_tokens: 150,  temperature: 0.3 }
  contradiction_check: { max_tokens: 1000, temperature: 0.3 }
  contradiction_resolve: { max_tokens: 1000, temperature: 0.3 }
  retrieval:
    max_context_chars: 8000
    max_briefing_chars: 24000
    short_file_chars: 4000
    max_query_length: 10000
    heading_match_threshold: 0.5
    content_match_threshold: 0.4
```

## Logging

```yaml
logging:
  level: INFO
  file: ~/.config/archivyst/archivyst.log
  max_file_size: 5000000    # 5MB, rotate when exceeded
  backup_count: 3           # keep 3 rotated backups
```

Set `file: null` to disable file logging. Console output stays at WARNING level unless `-v` is used.

## Full Config Example

See `config.example.yaml` in the repository for a complete annotated example with all available settings.
