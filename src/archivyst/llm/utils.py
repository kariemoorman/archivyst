"""
LLM response parsing utilities.
"""

import json
from typing import Any


def parse_json_response(response: str) -> dict[str, Any]:
    """Parse JSON from LLM response, handling markdown code blocks.

    Extracts JSON from responses that may be wrapped in ```json ... ```
    code fences.

    Args:
        response: Raw LLM response text.

    Returns:
        Parsed JSON as a dictionary.

    Raises:
        json.JSONDecodeError: If the response is not valid JSON.
        ValueError: If code block markers are malformed.
    """
    text = response.strip()
    if "```" in text:
        start = text.index("```") + 3
        if text[start : start + 4] == "json":
            start += 4
        end = text.index("```", start)
        text = text[start:end]
    result: dict[str, Any] = json.loads(text.strip())
    return result
