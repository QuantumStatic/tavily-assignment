from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def structured(self, prompt: str, schema: type[T]) -> T: ...


_NEBIUS_BASE_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/"


class NebiusLLM:
    """Nebius Token Factory structured-output client, via the OpenAI-compatible API
    (Nebius exposes an OpenAI-shaped endpoint for its hosted open-weight models)."""

    def __init__(self, model: str = "zai-org/GLM-5.2", api_key: str | None = None):
        import os

        from langchain_openai import ChatOpenAI
        key = api_key or os.environ["NEBIUS_API_KEY"]
        self._model = ChatOpenAI(model=model, base_url=_NEBIUS_BASE_URL, api_key=key)

    def structured(self, prompt: str, schema: type[T]) -> T:
        return self._model.with_structured_output(schema).invoke(prompt)
