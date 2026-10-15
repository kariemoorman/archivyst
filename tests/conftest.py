"""
Pytest configuration and fixtures for Archivyst tests.
"""

import tempfile
from pathlib import Path

import pytest

from archivyst.core.models import (
    Config,
    IndexingConfig,
    LLMConfig,
    LLMModelsConfig,
    MemoryConfig,
    ProjectConfig,
    VaultConfig,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir).resolve()


@pytest.fixture
def sample_vault(temp_dir: Path) -> Path:
    """Create a sample vault with test notes."""
    vault_path = temp_dir / "test-vault"
    vault_path.mkdir()

    # Create some test notes
    (vault_path / "index.md").write_text("""\
# Test Vault Index

This is the main index for the test vault.

## Quick Links

- [[project-alpha]]
- [[meeting-notes]]

## Overview

This vault contains test data for Archivyst.
""")

    (vault_path / "project-alpha.md").write_text("""\
---
aliases:
  - Alpha Project
  - Project A
---

# Project Alpha

## Overview

Project Alpha is a test project for validating Archivyst functionality.

## Goals

1. Test heading extraction
2. Test wikilink parsing
3. Test ToC generation

## Status

Currently in development.

### Milestones

- [x] Initial setup
- [ ] Core implementation
- [ ] Testing

## Related

See also [[meeting-notes]] for discussions.
""")

    (vault_path / "meeting-notes.md").write_text("""\
# Meeting Notes

## 2024-01-15 - Project Kickoff

Discussed [[project-alpha]] initial requirements.

### Attendees

- Alice
- Bob
- Charlie

### Action Items

- Alice: Create project plan
- Bob: Set up infrastructure

## 2024-01-22 - Progress Review

Reviewed progress on [[project-alpha]].
""")

    # Create a subdirectory with more notes
    archive_dir = vault_path / "archive"
    archive_dir.mkdir()

    (archive_dir / "old-note.md").write_text("""\
# Old Note

This is an archived note that should still be indexed.
""")

    return vault_path


@pytest.fixture
def memory_vault(temp_dir: Path) -> Path:
    """Create an empty memory vault."""
    memory_path = temp_dir / "agent-memory"
    memory_path.mkdir()
    return memory_path


@pytest.fixture
def test_config(sample_vault: Path, memory_vault: Path) -> Config:
    """Create a test configuration."""
    return Config(
        memory=MemoryConfig(path=memory_vault),
        projects=[
            ProjectConfig(
                name="test-project",
                vaults=[
                    VaultConfig(
                        name="test-vault",
                        path=sample_vault,
                        active=True,
                    )
                ],
            )
        ],
        default_project="test-project",
        llm=LLMConfig(
            base_url="http://localhost:1234/v1",
            models=LLMModelsConfig(
                summarization="test-model",
                routing="test-model",
                synthesis="test-model",
                vision="test-model",
            ),
        ),
        indexing=IndexingConfig(
            summarize_sections=False,  # Disable for faster tests
        ),
    )


@pytest.fixture
def note_content() -> str:
    """Sample markdown note content."""
    return """\
---
title: Sample Note
tags:
  - test
  - sample
aliases:
  - Example Note
---

# Sample Note

This is a sample note for testing.

## First Section

Content of the first section with a [[wikilink]].

### Subsection 1.1

More detailed content here.

### Subsection 1.2

Even more content.

## Second Section

Another section with ![[image.png]] embedded.

## Third Section

Final section content.
"""


# Skip marker for integration tests
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests requiring LM Studio",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration tests if LM Studio unavailable."""
    import asyncio

    from archivyst.llm.client import check_lm_studio_available

    # Check LM Studio availability once
    try:
        available = asyncio.run(check_lm_studio_available())
    except Exception:
        available = False

    if not available:
        skip_integration = pytest.mark.skip(reason="LM Studio not available")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
