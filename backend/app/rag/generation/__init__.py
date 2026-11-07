from __future__ import annotations

from app.core.config import Settings
from app.rag.generation.base import LLMProvider


def build_llm(settings: Settings) -> LLMProvider | None:
    """The configured LLM, or None when its API key is missing (search keeps working)."""
    if not settings.llm_configured:
        return None
    if settings.llm_provider == "openai":
        from app.rag.generation.openai_llm import OpenAILLM

        return OpenAILLM(settings)
    from app.rag.generation.anthropic_llm import AnthropicLLM

    return AnthropicLLM(settings)
