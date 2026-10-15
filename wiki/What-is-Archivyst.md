# What is Archivyst?

## The Problem with Vector RAG

Traditional RAG systems:
1. **Chunk** your documents into fixed-size pieces (512 tokens, etc.)
2. **Embed** each chunk into a high-dimensional vector
3. **Search** by computing cosine similarity between query and chunk vectors
4. **Return** the top-k most "similar" chunks

This approach has fundamental limitations:
- **Arbitrary boundaries** — chunks split mid-sentence, mid-paragraph, mid-thought
- **Lost structure** — headings, sections, and document hierarchy disappear
- **Black box retrieval** — no explanation for why a chunk was selected
- **Context collapse** — a chunk about "Python" (the snake) looks similar to "Python" (the language)

## The Archivyst Approach

Archivyst works differently:

1. **Parse structure** — extract headings, sections, and natural document boundaries
2. **Build ToC trees** — create hierarchical Table of Contents for each note
3. **Reason about navigation** — use LLM to decide which sections are relevant
4. **Traverse with explanation** — follow the path, logging why each decision was made
5. **Learn and remember** — extract entities, facts, and patterns into persistent memory

This mirrors how a human researcher would work:
- Skim the table of contents
- Identify promising sections
- Read those sections deeply
- Follow links to related documents
- Remember what you learned for next time

## Key Features

- **No vector database** — no Chroma, Pinecone, or embeddings to manage
- **No chunking** — respects natural document structure
- **Explainable retrieval** — every source includes reasoning trace
- **Local LLMs** — runs entirely on your machine via LM Studio
- **Obsidian-native** — works with wikilinks, backlinks, and vault structure
- **Cognitive memory** — learns across sessions (entities, facts, patterns, corrections)
- **Memory lifecycle** — promote, prune, organize, and version memory
- **Project isolation** — separate knowledge and memory per project
- **Fuzzy matching** — finds relevant sections even with approximate queries
- **Shell completions** — tab completion for all commands
- **Response modes** — strict (vault only) or lax (vault + model's own knowledge)
- **Contradiction detection** — detects conflicting facts across sessions
- **Code processing** — parse source code into structured markdown

## Comparison

| Aspect | Vector RAG | Archivyst |
|--------|-----------|-----------|
| **Retrieval** | Similarity search | Structured navigation + fuzzy matching |
| **Boundaries** | Fixed chunks | Natural sections |
| **Explainability** | Cosine scores | Reasoning trace |
| **Memory** | None | Full cognitive memory system |
| **Storage** | Vector DB | Markdown + git versioning |
| **Dependencies** | Embeddings model + vector DB | LLM only |
| **Updates** | Re-embed chunks | Re-parse changed files |
