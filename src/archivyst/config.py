"""
Configuration loading utilities for Archivyst.
"""

from pathlib import Path

from archivyst.core.models import Config, LLMConfig, MemoryConfig


def find_config() -> Path | None:
    """
    Find the configuration file in standard locations.

    Search order:
    1. ./archivyst.yaml
    2. ./config.yaml
    3. ~/.config/archivyst/config.yaml
    """
    candidates = [
        Path.cwd() / "archivyst.yaml",
        Path.cwd() / "config.yaml",
        Path.home() / ".config" / "archivyst" / "config.yaml",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def load_config(path: Path | str | None = None) -> Config:
    """
    Load configuration from file.

    Args:
        path: Explicit path to config file, or None to search

    Raises:
        FileNotFoundError: If config file not found
        ValueError: If config file is invalid
    """
    if path is None:
        path = find_config()

    if path is None:
        raise FileNotFoundError(
            "Config file not found. Run 'archivyst config init' to create one."
        )

    return Config.load(Path(path))


def get_default_config() -> Config:
    """
    Create a minimal default configuration.

    Useful for testing or programmatic usage.
    """
    return Config(
        memory=MemoryConfig(
            path=Path.home() / "vaults" / "agent-memory",
        ),
        llm=LLMConfig(),
    )
