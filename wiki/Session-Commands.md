# Session Commands

During an interactive session (`archivyst session`), these commands are available:

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/trace` | Show retrieval trace from last query |
| `/vaults` | List active vaults |
| `/sources` | Show sources from last query |
| `/clear` | Clear conversation history |
| `/save` | Save conversation to memory now |
| `/remember <text>` | Save information to memory (LLM classifies as preference, fact, or pattern) |
| `/promote` | Mark current session as promoted |
| `/mode <strict\|lax>` | Switch response mode |
| `/exit` | End session |
| `exit`, `quit`, `q` | End session (without slash also works) |

## /remember Example

```
You: /remember Alice prefers markdown tables over bullet lists
Save as (classified: preference) [preference/fact/pattern] (preference): 
Saved to preference memory
```

## /trace Example

```
You: what is the architecture?

[response shown]

You: /trace

Retrieval Trace
  Routing query in vault 'my-vault'...
  Selected 1 notes: Archivyst.md
  Expanded to 2 notes via wikilinks
  Searching episodic memory...
  Searching semantic memory...
  Found 3 relevant semantic memories
  Retrieved 8 sources total

Sources
  Archivyst.md > Architecture (Heading matched query)
  Archivyst.md > Key Features (Selected via ToC traversal)
```

## Response Modes

- **strict** (default) — answers only from vault content and memory. If the query isn't covered, says so.
- **lax** — uses vault content if relevant, but also draws on the model's own knowledge. Notes when answering from its own knowledge vs vault sources.

```
You: /mode lax
Response mode set to: lax

You: /mode
Current response mode: lax
```

The default mode is set per project in `config.yaml` (`response_mode: strict|lax`) and can be overridden with `--mode` at session start.
