from __future__ import annotations

from app.core.config import Settings
from app.rag.generation.base import LLMProvider


def make_llm(settings: Settings, provider: str, model: str) -> LLMProvider:
    """A provider object for one specific model. The API key for ``provider`` must be configured."""
    scoped = settings.model_copy(update={"llm_provider": provider, "llm_model": model})
    if provider == "openai":
        from app.rag.generation.openai_llm import OpenAILLM

        return OpenAILLM(scoped)
    if provider == "google":
        from app.rag.generation.google_llm import GeminiLLM

        return GeminiLLM(scoped)
    from app.rag.generation.anthropic_llm import AnthropicLLM

    return AnthropicLLM(scoped)


def build_llm(settings: Settings) -> LLMProvider | None:
    """The configured default LLM, or None when its API key is missing (search keeps working)."""
    if not settings.llm_configured:
        return None
    return make_llm(settings, settings.llm_provider, settings.llm_model)


def build_judge(settings: Settings) -> LLMProvider | None:
    """Optional separate judge model for evaluations (EVAL_JUDGE_MODEL); None = use the answer model."""
    if not settings.eval_judge_model:
        return None
    return build_llm(settings.model_copy(update={"llm_model": settings.eval_judge_model}))
