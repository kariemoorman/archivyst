# Installation

## Prerequisites

- **Python 3.13+**
- **LM Studio** (or any OpenAI-compatible local LLM server)
- **Obsidian** (optional, but Archivyst is designed for Obsidian vaults)

## Install from Source

```bash
# Clone the repository
git clone https://github.com/yourname/archivyst.git
cd archivyst

# Install in development mode
pip install -e .

# Or with dev dependencies (pytest, ruff, mypy)
pip install -e ".[dev]"

# For code file processing (tree-sitter support for non-Python languages)
pip install -e ".[code]"
```

## Verify Installation

```bash
# Both commands work (archive is a shorthand alias)
archivyst --help
archive --help
```

## Shell Completions

Enable tab completion for all commands:

```bash
# Auto-detect your shell and output the completion script
archivyst completions

# For a specific shell
archivyst completions --shell zsh
archivyst completions --shell bash
archivyst completions --shell fish

# Install (zsh example)
archivyst completions >> ~/.zshrc && source ~/.zshrc
```

## LLM Server Setup

Archivyst works with any OpenAI-compatible inference server:

| Server | Default Port | Install |
|--------|-------------|---------|
| [LM Studio](https://lmstudio.ai) | `http://localhost:1234/v1` | Desktop app |
| [Ollama](https://ollama.com) | `http://localhost:11434/v1` | `brew install ollama` |
| [vLLM](https://docs.vllm.ai) | `http://localhost:8000/v1` | `pip install vllm` |
| [llama.cpp](https://github.com/ggerganov/llama.cpp) | `http://localhost:8080/v1` | Build from source |
| OpenAI API | `https://api.openai.com/v1` | API key required |
| Anthropic API | `https://api.anthropic.com/v1` | API key required |

Recommended models:
- **Summarization/Routing/Memory**: `qwen2.5-7b-instruct`
- **Synthesis**: `qwen2.5-14b-instruct`
- **Vision (optional)**: `qwen3-vl`

> **Important**: Set the model context length to at least 8192 tokens (16384 recommended).

> **Note**: Use instruct models, not reasoning/thinking models. Reasoning models waste tokens on internal thinking and can return empty responses.

## API Key Configuration

For remote providers (OpenAI, Anthropic), set your API key:

```bash
# Option 1: Set environment variable (recommended)
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
# or
export ARCHIVYST_API_KEY=sk-...
```

```yaml
# Option 2: Reference env var in config.yaml
llm:
  base_url: https://api.openai.com/v1
  api_key: ${OPENAI_API_KEY}
```

For local servers (LM Studio, Ollama, etc.), no API key is needed.
