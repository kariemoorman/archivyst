# Commands Reference

## Global Flags

All commands support:

```bash
--config, -c     Path to config file
--format, -f     Output format: md (default) or json
--verbose, -v    Enable verbose output (place before subcommand)
```

> **Note**: Place `-v` before the subcommand: `archivyst -v session`, not `archivyst session -v`

## Initialization

```bash
archivyst init <project-name>
archivyst init <project-name> --vault ~/path/to/vault
archivyst init <project-name> --memory-path ~/path/to/memory
archivyst init <project-name> --global=read
archivyst init <project-name> --global=none
```

## Configuration

```bash
archivyst config show
archivyst config validate
```

## Project Management

```bash
archivyst project list
archivyst project list --details
archivyst project add <name> --vault ~/path/to/vault
archivyst project remove <name>
archivyst project remove <name> --delete-files
archivyst project set-default <name>
archivyst project set-global <name> full|read|none
archivyst project show <name>
```

## Vault Management

```bash
archivyst vault list
archivyst vault add <name> --path ~/path
archivyst vault activate <name>
archivyst vault deactivate <name>
archivyst vault status
```

## Indexing

```bash
archivyst index                          # Smart default
archivyst index rebuild                  # Incremental rebuild
archivyst index rebuild --full           # Full rebuild (ignore cache)
archivyst index rebuild --no-summaries   # Skip LLM summaries
archivyst index rebuild --no-scan        # Skip pre-index code scan
archivyst index rebuild --vault my-vault # Specific vault
archivyst index status
archivyst index check
```

## File Processing / Import

```bash
archivyst process                                        # Interactive
archivyst process ~/Downloads/data/ my-project           # Specify input + project
archivyst process ~/data/ --output ~/vaults/research/    # Override output
archivyst process ~/data/ my-project --structure         # LLM-suggested organization
archivyst process ~/data/ my-project --structure flat    # Flatten into single dir
archivyst process ~/data/ my-project --structure topic   # Group by topic
archivyst process ~/data/ my-project --index             # Auto-index after
```

## Vault Scanning

```bash
archivyst scan                           # Scan default project
archivyst scan --project work            # Scan specific project
archivyst scan --vault work-notes        # Scan specific vault
archivyst scan --no-index                # Skip indexing after
```

## Querying

```bash
archivyst query "your question"
archivyst query "your question" --show-sources
archivyst query "your question" --show-trace
archivyst query "your question" --mode lax
archivyst session
archivyst session --mode lax
archivyst -v session
```

## Memory Management

```bash
archivyst memory show
archivyst memory show user/preferences.md
archivyst memory show --global
archivyst memory tree
archivyst memory tree --global
archivyst memory conversations
archivyst memory entities
archivyst memory entities people|projects|concepts
archivyst memory promote
archivyst memory promote --session <session-id>
archivyst memory organize
archivyst memory organize --depth full
archivyst memory organize --global
archivyst memory organize --global --depth full
archivyst memory prune
archivyst memory prune --dry-run
archivyst memory history
archivyst memory history --global
archivyst memory history --path semantic/entities
archivyst memory lobotomy
archivyst memory lobotomy --dry-run
archivyst memory reconcile
```

## Shell Completions

```bash
archivyst completions
archivyst completions --shell zsh|bash|fish
```
