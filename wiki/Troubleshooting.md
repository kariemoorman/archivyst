# Troubleshooting

## LM Studio Connection Failed

```bash
# Check if LM Studio is running
curl http://localhost:1234/v1/models

# Verify config
archivyst config show
```

## Context Length Exceeded

```
Error: n_keep >= n_ctx
```

Increase the model context length in LM Studio settings to at least 8192 (16384 recommended).

## Index Build Slow

- Disable section summaries for initial testing: `summarize_sections: false`
- Use a faster model for summarization
- Exclude large directories with `exclude_patterns`

## No Index Found

```
No index found for vault 'my-vault'.
```

Run `archivyst index` — it will detect the missing index and offer to build it.

## Memory Access Denied

```
MemoryAccessError: Cannot read 'global/...': outside project scope
```

Check project's global access level:
```bash
archivyst project show my-project
```

## Model Returns Empty or "ready"

If using a reasoning model (Qwen3 base), switch to an instruct model. Reasoning models put output in `reasoning_content` instead of `content`, causing empty responses.

## Entity Extraction JSON Parsing Warning

```
WARNING [extraction] Failed to parse entity extraction JSON
```

The LLM returned malformed JSON during entity extraction. This is non-critical — the answer still comes through, but nothing gets saved to memory for that turn. Increase `session_extraction.max_tokens` in your config (default 500, try 1000).

## Vault Not Found

```
Vault 'my-vault' not found
```

The `--vault` flag searches all projects. If the vault belongs to a non-default project, use `--project`:
```bash
archivyst scan --project work
```

Or pass the vault's exact name:
```bash
archivyst scan --vault work-notes-vault
```

## Missing Dependencies

```bash
# Reinstall with all dependencies
pip install -e ".[dev]"

# For code file processing (tree-sitter)
pip install -e ".[code]"

# For PDF processing
pip install pymupdf pdfplumber pytesseract
```

## Log File Not Writing

Ensure `logging.file` is set in `config.yaml`:
```yaml
logging:
  level: INFO
  file: ~/.config/archivyst/archivyst.log
```

Logs only go to the file — console stays at WARNING level unless `-v` is used.
