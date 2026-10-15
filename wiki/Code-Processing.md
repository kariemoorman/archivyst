# Code Processing

Archivyst can parse source code files into structured markdown notes, making code queryable through the same ToC traversal and indexing pipeline used for regular notes.

## Two Entry Points

### `archivyst process` — External Import

Converts files from an external directory into the vault:

```bash
archivyst process ~/Downloads/code-dump/ my-project
archivyst process ~/code/ my-project --index
```

### `archivyst scan` — In-Vault Discovery

Discovers code files already inside the vault and creates companion `.md` notes:

```bash
archivyst scan
archivyst scan --project work
archivyst scan --vault work-notes
```

Scan also runs automatically as a pre-step of `archivyst index` (disable with `--no-scan`).

## How It Works

1. **Parse structure** — extract classes, functions, imports from source code
2. **Generate descriptions** — LLM describes each structural element in 1-2 sentences
3. **Assemble markdown** — headings per symbol, descriptions underneath

### Parser Strategy

| Language | Parser | Dependency |
|----------|--------|-----------|
| Python | `ast` (stdlib) | None |
| All others | `tree-sitter` | `pip install archivyst[code]` |
| Fallback | Raw code fence | None |

## Output Format

A Python file like `parser.py` becomes `parser.py.md`:

```markdown
---
source: "src/parser.py"
converted_at: "2026-04-14T11:00:00"
original_format: py
language: python
source_hash: a1b2c3d4...
input_source: external
---

# parser

Module for parsing configuration files.

## Imports

```
os
pathlib.Path
yaml
```

## Class: `Parser`

Main parser class that reads YAML configuration files.

### `__init__(self, config_path: Path)`

Constructor — takes a config file path. Public API.

### `parse(self) -> dict`

Reads and validates the config file. Returns parsed dict. Public API.

## Function: `_validate_schema(data: dict) -> bool`

Internal helper — validates parsed data against expected schema.
```

## Naming Convention

All converted files preserve the original extension:
- `parser.py` → `parser.py.md`
- `data.csv` → `data.csv.md`
- `report.pdf` → `report.pdf.md`

## Code Manifest

Both `process` and `scan` track converted files in `.pageindex/code_manifest.json`. This enables:
- **Deduplication** — same file imported via `process` won't be re-converted by `scan`
- **Change detection** — modified files detected on next scan
- **Move detection** — renamed/moved files identified by hash
- **Model tracking** — regenerate descriptions when LLM model changes

## File Selection

When `scan` finds files, you can choose how to select:

```
Include files? [all/types/files/skip] (all):
```

- **all** — convert everything
- **types** — select by file extension
- **files** — toggle individual files by number
- **skip** — convert nothing

## Supported Languages

Python, C, C++, Rust, Go, Java, Kotlin, Scala, C#, F#, VB.NET, JavaScript, TypeScript, Ruby, Perl, Lua, R, PHP, Swift, Dart, SQL, COBOL, Fortran, Bash, PowerShell, Haskell, Elixir, Erlang, OCaml, Clojure, and more (305 languages via tree-sitter).

## Configuration

```yaml
process:
  supported_formats: [md, txt, csv, json, pdf, py, c, java, sql, ...]
  exclude_patterns: ["*.tmp", ".DS_Store"]
  max_code_file_size: 512000  # 500KB

indexing:
  scan_code: true  # Enable pre-index code scan

token_allocation:
  code_description: { max_tokens: 150, temperature: 0.3 }
  code_summary:     { max_tokens: 150, temperature: 0.3 }
```
