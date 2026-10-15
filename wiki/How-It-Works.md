# How It Works

## End-to-End Query Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                           USER QUERY                                │
│              "What were the key decisions from Q3?"                 │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    1. MEMORY BRIEFING (session start)               │
│  Load all memory layers (preferences, entities, facts, etc.)        │
│  Send briefing to LLM as first message                              │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      2. ROUTE TO NOTES                              │
│  LLM examines all note ToCs and selects relevant ones.              │
│  Fuzzy matching catches headings matching query terms.              │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     3. RETRIEVE CONTENT                             │
│  Short files (< 4000 chars): full content passed directly.          │
│  Larger files:                                                      │
│    a. Fuzzy match query against headings + summaries                │
│    b. LLM traverses ToC to select additional sections               │
│    c. Merge matched + traversed sections                            │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  4. SEARCH MEMORY                                   │
│  Search episodic memory (past conversations)                        │
│  Search semantic memory (entities, facts, relationships)            │
│  Add matches to retrieved content                                   │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    5. SYNTHESIZE ANSWER                             │
│  LLM generates response using:                                      │
│  • Memory briefing (in conversation history)                        │
│  • Retrieved vault content                                          │
│  • Episodic + semantic memory matches                               │
│  • Strict mode: vault only / Lax mode: vault + own knowledge        │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    6. EXTRACT & LEARN                               │
│  Save turn to working memory                                        │
│  Extract entities (people, projects, concepts)                      │
│  Extract facts, preferences, corrections, patterns                  │
│  Check for contradictions against existing memory                   │
│  Log events                                                         │
└─────────────────────────────────────────────────────────────────────┘
```
