"""
Archivyst - Vectorless, reasoning-based RAG for Obsidian vaults.

Archivyst mimics human document reading by constructing hierarchical
ToC trees instead of vector similarity search. It uses local LLMs
via LM Studio for reasoning-based retrieval.

Example usage:

    from archivyst import load_config, ArchivystAgent, LLMClient, VaultManager, MemoryStore

    config = load_config()
    scope = config.build_project_scope(config.get_default_project().name)

    async with LLMClient(config.llm) as llm:
        vaults = VaultManager(config, llm)
        memory = MemoryStore(config, scope)
        agent = ArchivystAgent(config, llm, vaults, memory)

        await agent.start_session()
        answer = await agent.query("What did I write about project X?")
        await agent.end_session()
"""

from archivyst.config import find_config, get_default_config, load_config
from archivyst.core.models import Config

__version__ = "0.1.0"
__all__ = [
    "Config",
    "find_config",
    "get_default_config",
    "load_config",
]
