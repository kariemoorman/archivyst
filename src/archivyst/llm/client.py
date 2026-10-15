"""
LLM client for LM Studio (OpenAI-compatible API).

Handles model routing, retries, and vision requests.
"""

import asyncio
import logging
import time
from typing import Any, Literal

import httpx

from archivyst.core.models import LLMConfig

logger = logging.getLogger(__name__)

TaskType = Literal["summarization", "routing", "synthesis", "vision", "memory"]


class LLMClientError(Exception):
    """Base exception for LLM client errors."""

    pass


class LLMConnectionError(LLMClientError):
    """Failed to connect to LLM server."""

    pass


class LLMResponseError(LLMClientError):
    """LLM returned an error response."""

    pass


class LLMClient:
    """
    Async client for LM Studio's OpenAI-compatible API.

    Handles model routing per task type and vision requests.
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0.0

    async def __aenter__(self) -> "LLMClient":
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        self._client = httpx.AsyncClient(
            timeout=self.config.timeout,
            headers=headers,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise LLMClientError("Client not initialized. Use 'async with' context.")
        return self._client

    def _get_model(self, task: TaskType) -> str:
        """Get the model name for a given task type."""
        match task:
            case "summarization":
                return self.config.models.summarization
            case "routing":
                return self.config.models.routing
            case "synthesis":
                return self.config.models.synthesis
            case "vision":
                return self.config.models.vision
            case "memory":
                return self.config.models.memory

    def _get_base_url(self, task: TaskType) -> str:
        """Get the base URL for a given task type."""
        if task == "vision" and self.config.vision_base_url:
            return self.config.vision_base_url
        return self.config.base_url

    async def _request_with_retry(
        self,
        url: str,
        payload: dict[str, object],
        retries: int | None = None,
    ) -> dict[str, Any]:
        """Make a request with retry logic and rate limiting."""
        retries = retries if retries is not None else self.config.max_retries
        last_error: Exception | None = None

        # Rate limiting — enforce minimum interval between requests
        elapsed = time.monotonic() - self._last_request_time
        interval = self.config.min_request_interval
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)

        for attempt in range(retries + 1):
            try:
                self._last_request_time = time.monotonic()
                response = await self.client.post(url, json=payload)
                response.raise_for_status()
                return dict(response.json())

            except httpx.ConnectError as e:
                last_error = LLMConnectionError(
                    f"Failed to connect to LLM server at {url}: {e}"
                )
            except httpx.HTTPStatusError as e:
                last_error = LLMResponseError(
                    f"LLM server returned error: {e.response.status_code} - {e.response.text}"
                )
            except httpx.TimeoutException as e:
                last_error = LLMClientError(f"Request timed out: {e}")

            if attempt < retries:
                logger.warning(
                    "[request] Retry %d/%d after error: %s",
                    attempt + 1,
                    retries,
                    last_error,
                )
                await asyncio.sleep(2**attempt)  # Exponential backoff

        raise last_error or LLMClientError("Unknown error")

    async def complete(
        self,
        prompt: str,
        task: TaskType = "synthesis",
        system_prompt: str | None = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> str:
        """
        Generate a completion for a text prompt.

        Args:
            prompt: The user prompt
            task: Task type for model selection
            system_prompt: Optional system prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            The generated text
        """
        model = self._get_model(task)
        base_url = self._get_base_url(task)
        url = f"{base_url}/chat/completions"

        logger.info("[request] POST %s (task=%s, model=%s)", url, task, model)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        result = await self._request_with_retry(url, payload)

        try:
            message = result["choices"][0]["message"]
            content = message.get("content") or ""
            # Reasoning models (e.g. Qwen3) may place output in
            # reasoning_content when content is empty.
            if not content.strip() and message.get("reasoning_content"):
                content = message["reasoning_content"]
            return content
        except (KeyError, IndexError) as e:
            raise LLMResponseError(f"Unexpected response format: {e}") from e

    async def complete_vision(
        self,
        prompt: str,
        image_base64: str,
        media_type: str = "image/png",
        system_prompt: str | None = None,
        max_tokens: int = 1000,
    ) -> str:
        """
        Generate a completion for an image + text prompt.

        Args:
            prompt: The text prompt
            image_base64: Base64-encoded image data
            media_type: MIME type of the image
            system_prompt: Optional system prompt
            max_tokens: Maximum tokens to generate

        Returns:
            The generated text
        """
        model = self._get_model("vision")
        base_url = self._get_base_url("vision")
        url = f"{base_url}/chat/completions"

        logger.info("[request] POST %s (task=vision, model=%s)", url, model)

        messages: list[dict[str, object]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_base64}"
                        },
                    },
                ],
            }
        )

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        result = await self._request_with_retry(url, payload)

        try:
            message = result["choices"][0]["message"]
            content = message.get("content") or ""
            if not content.strip() and message.get("reasoning_content"):
                content = message["reasoning_content"]
            return content
        except (KeyError, IndexError) as e:
            raise LLMResponseError(f"Unexpected response format: {e}") from e

    async def complete_chat(
        self,
        messages: list[dict[str, str]],
        task: TaskType = "synthesis",
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        """
        Generate a completion for a multi-turn conversation.

        Args:
            messages: List of {"role": ..., "content": ...} dicts
            task: Task type for model selection
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            The generated text
        """
        model = self._get_model(task)
        base_url = self._get_base_url(task)
        url = f"{base_url}/chat/completions"

        logger.info(
            "[request] POST %s (task=%s, model=%s, messages=%d)",
            url,
            task,
            model,
            len(messages),
        )

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        result = await self._request_with_retry(url, payload)

        try:
            message = result["choices"][0]["message"]
            content = message.get("content") or ""
            if not content.strip() and message.get("reasoning_content"):
                content = message["reasoning_content"]
            return content
        except (KeyError, IndexError) as e:
            raise LLMResponseError(f"Unexpected response format: {e}") from e

    async def json_task(
        self,
        prompt: str,
        task: TaskType = "memory",
        response_model: type[Any] | None = None,
        max_tokens: int = 500,
        temperature: float = 0.3,
    ) -> Any:
        """Call LLM, parse JSON response, and optionally validate.

        Combines the complete → parse_json → validate pattern
        into one call.

        Args:
            prompt: The user prompt
            task: Task type for model selection
            response_model: Optional Pydantic BaseModel to validate
                against. If provided, returns the validated model
                instance. If None, returns the raw parsed dict.
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            Validated Pydantic model instance, or raw dict if no
            response_model provided.

        Raises:
            LLMResponseError: If LLM call fails
            json.JSONDecodeError: If response is not valid JSON
            pydantic.ValidationError: If response doesn't match model
        """
        from archivyst.llm.utils import parse_json_response

        response = await self.complete(
            prompt=prompt,
            task=task,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        data = parse_json_response(response)

        if response_model is not None:
            return response_model.model_validate(data)

        return data

    async def health_check(self) -> bool:
        """Check if the LLM server is reachable."""
        try:
            url = f"{self.config.base_url}/models"
            response = await self.client.get(url)
            return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List available models on the server."""
        url = f"{self.config.base_url}/models"
        response = await self.client.get(url)
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]


def create_client(config: LLMConfig) -> LLMClient:
    """Create an LLM client from config."""
    return LLMClient(config)


async def check_lm_studio_available(base_url: str = "http://localhost:1234/v1") -> bool:
    """
    Quick check if LM Studio is running.

    Useful for test skipping.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{base_url}/models")
            return response.status_code == 200
    except Exception:
        return False
