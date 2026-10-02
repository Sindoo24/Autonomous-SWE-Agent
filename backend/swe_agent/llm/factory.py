"""Build providers from settings. The only place that knows concrete provider classes."""

from __future__ import annotations

from swe_agent.config import ModelSettings, ProviderKind
from swe_agent.llm.base import LLMProvider
from swe_agent.llm.ollama import OllamaProvider
from swe_agent.llm.openai_compatible import OpenAICompatibleProvider
from swe_agent.llm.types import GenParams


def build_provider(cfg: ModelSettings) -> LLMProvider:
    if cfg.provider is ProviderKind.OLLAMA:
        return OllamaProvider(cfg.base_url, timeout_s=cfg.timeout_s, max_retries=cfg.max_retries)
    if cfg.provider is ProviderKind.OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(
            cfg.base_url,
            api_key=cfg.api_key,
            timeout_s=cfg.timeout_s,
            max_retries=cfg.max_retries,
        )
    raise ValueError(f"unknown provider {cfg.provider}")


def gen_params(cfg: ModelSettings) -> GenParams:
    return GenParams(
        temperature=cfg.temperature,
        max_tokens=cfg.max_output_tokens,
        seed=cfg.seed,
        num_ctx=cfg.num_ctx,
        think=cfg.think,
    )
