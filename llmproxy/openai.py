import json
import time
from typing import Any


def build_openai_response(
    *,
    request_id: str,
    model: str,
    text: str,
    usage: dict[str, int] | None,
    created: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
    if usage:
        payload["usage"] = usage
    return payload


def build_openai_stream_chunk(
    *,
    request_id: str,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None,
    created: int,
) -> bytes:
    payload = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n".encode()


def build_openai_done_chunk() -> bytes:
    return b"data: [DONE]\n\n"


def usage_to_openai(usage_metadata: Any) -> dict[str, int] | None:
    """Map Gemini usage_metadata to OpenAI usage format."""
    if usage_metadata is None:
        return None

    prompt = getattr(usage_metadata, "prompt_token_count", None)
    completion = getattr(usage_metadata, "candidates_token_count", None)
    if completion is None:
        completion = getattr(usage_metadata, "response_token_count", None)
    total = getattr(usage_metadata, "total_token_count", None)
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion

    usage: dict[str, int] = {}
    if prompt is not None:
        usage["prompt_tokens"] = int(prompt)
    if completion is not None:
        usage["completion_tokens"] = int(completion)
    if total is not None:
        usage["total_tokens"] = int(total)

    return usage or None


def build_openai_embedding_response(
    *,
    model: str,
    embeddings: list[list[float]],
    input_texts: list[str],
) -> dict[str, Any]:
    """Build an OpenAI-compatible embedding response from Gemini results."""
    data = [{"object": "embedding", "index": i, "embedding": emb} for i, emb in enumerate(embeddings)]
    # Approximate token count: ~4 chars per token.
    total_chars = sum(len(t) for t in input_texts)
    approx_tokens = max(1, total_chars // 4)
    return {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {
            "prompt_tokens": approx_tokens,
            "total_tokens": approx_tokens,
        },
    }
