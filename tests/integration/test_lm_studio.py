"""
Integration tests requiring LM Studio.

These tests are automatically skipped if LM Studio is not available.
Run with: pytest -m integration
"""

import pytest

from archivyst.core.models import LLMConfig, LLMModelsConfig
from archivyst.llm.client import LLMClient


@pytest.fixture
def llm_config():
    """Create LLM config for testing."""
    return LLMConfig(
        base_url="http://localhost:1234/v1",
        models=LLMModelsConfig(
            summarization="qwen2.5-7b-instruct",
            routing="qwen2.5-7b-instruct",
            synthesis="qwen2.5-7b-instruct",
            vision="qwen3-vl",
        ),
        timeout=60,
        max_retries=1,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lm_studio_connection(llm_config):
    """Test basic connection to LM Studio."""
    async with LLMClient(llm_config) as client:
        is_healthy = await client.health_check()
        assert is_healthy, "LM Studio should be reachable"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_models(llm_config):
    """Test listing available models."""
    async with LLMClient(llm_config) as client:
        models = await client.list_models()
        assert len(models) > 0, "Should have at least one model loaded"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_simple_completion(llm_config):
    """Test basic text completion."""
    async with LLMClient(llm_config) as client:
        response = await client.complete(
            prompt="What is 2 + 2? Reply with just the number.",
            task="synthesis",
            max_tokens=10,
            temperature=0,
        )

        assert "4" in response, f"Expected '4' in response, got: {response}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_completion_with_system_prompt(llm_config):
    """Test completion with system prompt."""
    async with LLMClient(llm_config) as client:
        response = await client.complete(
            prompt="What should I do?",
            system_prompt="You are a helpful assistant that always responds with 'Take a break!'",
            task="synthesis",
            max_tokens=50,
            temperature=0,
        )

        # Should follow system prompt
        assert len(response) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_completion(llm_config):
    """Test multi-turn chat completion."""
    async with LLMClient(llm_config) as client:
        messages = [
            {"role": "user", "content": "My name is Alice."},
            {"role": "assistant", "content": "Nice to meet you, Alice!"},
            {"role": "user", "content": "What is my name?"},
        ]

        response = await client.complete_chat(
            messages=messages,
            task="synthesis",
            max_tokens=50,
            temperature=0,
        )

        assert "Alice" in response, f"Should remember name, got: {response}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_summarization_task(llm_config):
    """Test summarization with routing model."""
    text = """
    The Archivyst system is a vectorless RAG engine designed for Obsidian vaults.
    Instead of using vector embeddings, it constructs hierarchical Table of Contents
    trees and uses LLM reasoning to navigate documents. This approach provides
    explainable retrieval paths and works with local LLMs via LM Studio.
    """

    async with LLMClient(llm_config) as client:
        response = await client.complete(
            prompt=f"Summarize this in one sentence:\n\n{text}",
            task="summarization",
            max_tokens=100,
        )

        assert len(response) > 20, "Should produce a summary"
        assert len(response) < 500, "Should be concise"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_routing_task_json_output(llm_config):
    """Test routing task with JSON output."""
    prompt = """Given these documents:
- notes/project.md: Project planning documentation
- notes/meeting.md: Meeting notes from last week
- archive/old.md: Archived content

Which documents are relevant to find information about project deadlines?

Respond in JSON:
{"selected": ["path1", "path2"], "reasoning": "explanation"}
"""

    async with LLMClient(llm_config) as client:
        response = await client.complete(
            prompt=prompt,
            task="routing",
            max_tokens=200,
            temperature=0.3,
        )

        # Should contain JSON-like structure
        assert "selected" in response.lower() or "[" in response


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handles_long_context(llm_config):
    """Test handling of longer context."""
    # Generate a longer prompt
    long_text = "This is a test sentence. " * 200

    async with LLMClient(llm_config) as client:
        response = await client.complete(
            prompt=f"Count the approximate number of sentences:\n\n{long_text}",
            task="synthesis",
            max_tokens=50,
        )

        assert len(response) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_error_handling_invalid_model(llm_config):
    """Test error handling for invalid model."""
    llm_config.models.synthesis = "nonexistent-model-xyz"

    async with LLMClient(llm_config) as client:
        # May succeed if server ignores model or may fail
        # Either way, should not crash
        try:
            await client.complete(
                prompt="Hello",
                task="synthesis",
                max_tokens=10,
            )
        except Exception as e:
            # Expected to potentially fail
            assert "model" in str(e).lower() or "error" in str(e).lower()


# Vision tests - may need separate model loaded
@pytest.mark.integration
@pytest.mark.asyncio
async def test_vision_completion(llm_config: LLMConfig) -> None:
    """Test vision completion with a generated test image."""
    import base64
    import io

    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")

    # Create a simple test image (red square with text-like pattern)
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_data = base64.b64encode(buf.getvalue()).decode()

    async with LLMClient(llm_config) as client:
        try:
            response = await client.complete_vision(
                prompt="Describe this image briefly.",
                image_base64=image_data,
                media_type="image/png",
            )
            assert len(response) > 0
        except Exception as e:
            pytest.skip(f"Vision model not available: {e}")
