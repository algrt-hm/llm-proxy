from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: Any | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = ""
    messages: list[ChatMessage]
    stream: bool = False


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = ""
    input: str | list[str]


class OllamaShowRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    model: str | None = None
