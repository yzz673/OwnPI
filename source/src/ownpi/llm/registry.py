from __future__ import annotations

from collections.abc import Callable

from ownpi.llm.base import LLMClient
from ownpi.schemas.runtime import LLMConfig

LLMClientBuilder = Callable[[LLMConfig], LLMClient]

_LLM_PROVIDER_REGISTRY: dict[str, LLMClientBuilder] = {}


def register_llm_provider(name: str, builder: LLMClientBuilder) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("LLM provider name must be non-empty.")
    _LLM_PROVIDER_REGISTRY[key] = builder


def get_llm_provider_builder(name: str) -> LLMClientBuilder:
    key = name.strip().lower()
    try:
        return _LLM_PROVIDER_REGISTRY[key]
    except KeyError as exc:
        available = ", ".join(sorted(_LLM_PROVIDER_REGISTRY)) or "<none>"
        raise ValueError(
            f"Unsupported llm provider: {name!r}. Registered providers: {available}."
        ) from exc


def list_llm_providers() -> list[str]:
    return sorted(_LLM_PROVIDER_REGISTRY)
