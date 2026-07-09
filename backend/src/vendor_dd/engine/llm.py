from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def structured(self, prompt: str, schema: type[T]) -> T: ...


class NebiusLLM:
    """LangChain + Nebius structured-output client."""

    def __init__(self, model: str = "moonshotai/Kimi-K2.6"):
        from langchain_nebius import ChatNebius
        self._model = ChatNebius(model=model)

    def structured(self, prompt: str, schema: type[T]) -> T:
        return self._model.with_structured_output(schema).invoke(prompt)
