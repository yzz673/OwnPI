from __future__ import annotations

from ownpi.llm import load_provider_modules
from ownpi.llm.base import LLMClient
from ownpi.llm.registry import get_llm_provider_builder
from ownpi.schemas.runtime import LLMConfig


def build_llm_client(config: LLMConfig) -> LLMClient:
    load_provider_modules()
    builder = get_llm_provider_builder(config.provider)
    return builder(config)
