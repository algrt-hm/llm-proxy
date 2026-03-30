import logging
from typing import Any

from google import genai
from google.genai import types

LOGGER = logging.getLogger("llmproxy")


_ROLE_MAP = {
    "user": "user",
    "assistant": "model",
    "tool": "tool",
    "function": "tool",
}


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    parts.append(str(part["text"]))
                    continue
                if "text" in part:
                    parts.append(str(part["text"]))
                    continue
                # Skip unsupported multimodal parts (image_url, etc.)
                LOGGER.warning(
                    "Skipping unsupported content part type: %s",
                    part.get("type", "unknown"),
                )
                continue
            LOGGER.warning("Skipping non-text content part: %s", type(part).__name__)
        if parts:
            return "\n".join(p for p in parts if p)
        return ""
    return str(content)


def build_gemini_contents(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[types.Content]]:
    system_parts: list[str] = []
    contents: list[types.Content] = []

    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            text = _content_to_text(content)
            if text:
                system_parts.append(text)
            continue

        gemini_role = _ROLE_MAP.get(role, "user")
        text = _content_to_text(content)
        if not text:
            continue
        contents.append(types.Content(role=gemini_role, parts=[types.Part.from_text(text=text)]))

    system_instruction = "\n\n".join(system_parts).strip() if system_parts else None
    return system_instruction, contents


def build_gemini_config(payload: dict[str, Any], system_instruction: str | None) -> types.GenerateContentConfig | None:
    config: dict[str, Any] = {}

    if system_instruction:
        config["system_instruction"] = system_instruction

    if "temperature" in payload:
        config["temperature"] = payload["temperature"]
    if "top_p" in payload:
        config["top_p"] = payload["top_p"]
    if "top_k" in payload:
        config["top_k"] = payload["top_k"]
    if "max_tokens" in payload:
        config["max_output_tokens"] = payload["max_tokens"]
    if "stop" in payload:
        stop_value = payload["stop"]
        if isinstance(stop_value, str):
            config["stop_sequences"] = [stop_value]
        elif isinstance(stop_value, list):
            config["stop_sequences"] = stop_value
    if "n" in payload:
        config["candidate_count"] = payload["n"]
    if "presence_penalty" in payload:
        config["presence_penalty"] = payload["presence_penalty"]
    if "frequency_penalty" in payload:
        config["frequency_penalty"] = payload["frequency_penalty"]
    if "seed" in payload:
        config["seed"] = payload["seed"]

    # Map OpenAI-style `reasoning` parameter to Gemini thinking config.
    # Only set thinking_config when the client explicitly sends `reasoning`.
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        if reasoning.get("enabled", False):
            budget = reasoning.get("budget")
            if isinstance(budget, int) and budget > 0:
                config["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
            # else: omit thinking_config, let Gemini use its default thinking
        else:
            config["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

    return types.GenerateContentConfig(**config) if config else None


async def generate_gemini_response(
    *,
    api_key: str,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig | None,
) -> types.GenerateContentResponse:
    async with genai.Client(api_key=api_key).aio as client:
        return await client.models.generate_content(model=model, contents=contents, config=config)


async def generate_gemini_embedding(
    *,
    api_key: str,
    model: str,
    texts: list[str],
) -> list[list[float]]:
    """Call the Gemini embed_content API and return embeddings.

    Passes all texts in a single batched SDK call.
    """
    async with genai.Client(api_key=api_key).aio as client:
        response = await client.models.embed_content(
            model=model,
            contents=texts,
        )
        return [list(e.values) for e in response.embeddings]


async def stream_gemini_response(
    *,
    api_key: str,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig | None,
):
    async with genai.Client(api_key=api_key).aio as client:
        stream = client.models.generate_content_stream(model=model, contents=contents, config=config)
        if hasattr(stream, "__aiter__"):
            async_iter = stream
        else:
            async_iter = await stream
        async for chunk in async_iter:
            yield chunk
