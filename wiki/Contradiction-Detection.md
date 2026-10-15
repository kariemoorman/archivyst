# Contradiction Detection

Archivyst detects when new information conflicts with existing memory — a fact changes, a preference is reversed, or an entity's attributes contradict previous records.

## How It Works

### Detection Points

Contradictions are checked at three points:

1. **Per-turn during sessions** — after each query/response, new extractions are compared against existing memory
2. **During indexing** — after entity and fact extraction, new knowledge is checked against existing
3. **During `memory reconcile`** — dedicated command for interactive resolution

### What Gets Checked

- New facts vs existing facts
- New entity attributes vs existing entity records
- New preferences vs existing preferences
- All of the above against `semantic/relationships.md`

## Contradiction File

Contradictions are cataloged in `semantic/contradictions.md` (also `procedural/` and `user/` scopes):

```markdown
# Contradictions

## Unresolved

### C-2026-04-14-001
- **Detected**: 2026-04-14T16:30:00
- **Source A**: [[semantic/facts/team-lead.md]] — "Alice leads Project X"
- **Source B**: [[(new extraction)]] — "Bob leads Project X"
- **Type**: factual
- **Context**: session 002b, turn 3

## Resolved

### C-2026-04-13-001
- **Detected**: 2026-04-13T10:00:00
- **Source A**: [[semantic/entities/people/alice.md]] — "Alice is CTO"
- **Source B**: [[(new extraction)]] — "Alice is VP Engineering"
- **Resolution**: Kept "CTO" — user confirmed Alice was promoted
- **Resolved at**: 2026-04-13T10:15:00
- **Resolved by**: user
```

## Resolving Contradictions

```bash
archivyst memory reconcile
```

Shows unresolved contradictions and offers resolution options:

```
Found 2 unresolved contradictions:

  [1] factual conflict
      A: semantic/facts/team-lead.md — "Alice leads Project X"
      B: (new extraction) — "Bob leads Project X"

  [2] preference conflict
      A: user/preferences.md — "Prefers bullet lists"
      B: Session turn 3 — "Prefers tables"

Resolve? [1-2/skip]:
```

Resolution options:
- **[a] Keep A** — keep the existing fact
- **[b] Keep B** — keep the new information
- **[c] Keep both** — mark as not a real conflict
- **[d] Discuss with LLM** — describe the resolution, LLM suggests file updates
- **[e] Skip** — leave unresolved for later

### LLM-Assisted Resolution

Choosing "Discuss with LLM" lets you explain the situation:

```
You: Alice was promoted and Bob took over Project X in March

LLM proposes:
  team-lead.md: "Alice leads Project X" → "Bob leads Project X (since March 2026)"
  alice.md: add "Previously led Project X, promoted to CTO"

Apply changes? [y/n]:
```

## Configuration

```yaml
token_allocation:
  contradiction_check:   { max_tokens: 1000, temperature: 0.3 }
  contradiction_resolve: { max_tokens: 1000, temperature: 0.3 }
```
