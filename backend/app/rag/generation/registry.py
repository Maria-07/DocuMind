"""The models available in chat: which ones, how to call them, and what their usage costs.

One provider object is built per model, lazily, so an unused model costs nothing. A model is available when
its provider's API key is configured. The registry is the only place that turns a user-supplied model id into a
provider, so an unknown or unconfigured id can never reach a provider SDK.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.rag.generation import make_llm
from app.rag.generation.base import LLMProvider
from app.rag.generation.catalog import Catalog, ModelSpec, Price, load_catalog, today, usage_cost

log = structlog.get_logger(__name__)


UNAVAILABLE_SECONDS = 600  # how long a model that failed on quota is shown as unavailable


@dataclass(frozen=True, slots=True)
class ModelOption:
    spec: ModelSpec
    is_default: bool
    unavailable_reason: str | None = None  # set while the model's quota or usage limit is exhausted

    def price(self, day: date | None = None) -> Price | None:
        return self.spec.price_on(day or today())


class ModelRegistry:
    def __init__(self, settings: Settings, *, catalog: Catalog | None = None, default: LLMProvider | None = None):
        self.settings = settings
        self.catalog = catalog or load_catalog(settings.llm_catalog_file)
        self._providers: dict[str, LLMProvider] = {}
        self._blocked: dict[str, tuple[float, str]] = {}  # model id -> (monotonic expiry, reason)
        self._default_id = settings.llm_model
        if default is not None:  # an already-built provider for the default model (also used by tests)
            self._default_id = default.model_id
            self._providers[default.model_id] = default

    # --- what is offered ------------------------------------------------------------------------
    def _spec_for(self, model_id: str) -> ModelSpec | None:
        spec = self.catalog.get(model_id)
        if spec is None and model_id == self._default_id:
            # The configured default may not be in the catalog: still usable, but its price is unknown.
            provider = self.settings.llm_provider
            spec = ModelSpec(id=model_id, provider=provider, label=model_id)
        return spec

    def _usable(self, spec: ModelSpec) -> bool:
        return spec.id in self._providers or self.settings.provider_configured(spec.provider)

    def _selectable(self, spec: ModelSpec) -> bool:
        """Usable, and allowed by CHAT_MODEL_IDS when that list is set (it limits what *users* can pick;
        internal calls such as routing only need a usable model)."""
        allowed = self.settings.chat_model_id_list
        return self._usable(spec) and (not allowed or spec.id in allowed)

    def is_selectable(self, model_id: str) -> bool:
        spec = self._spec_for(model_id)
        return spec is not None and self._selectable(spec)

    def available(self) -> list[ModelOption]:
        specs = [s for s in self.catalog.models if self._selectable(s)]
        if self._default_id not in {s.id for s in specs}:
            extra = self._spec_for(self._default_id)
            if extra is not None and self._selectable(extra):
                specs.insert(0, extra)
        default_id = self.default_id
        return [
            ModelOption(spec=s, is_default=s.id == default_id, unavailable_reason=self.unavailable_reason(s.id))
            for s in specs
        ]

    @property
    def default_id(self) -> str | None:
        """The configured default model, or the first available one when its provider has no key."""
        spec = self._spec_for(self._default_id)
        if spec is not None and self._selectable(spec):
            return spec.id
        for s in self.catalog.models:
            if self._selectable(s):
                return s.id
        return None

    @property
    def router_id(self) -> str | None:
        """The model for the routing call: LLM_ROUTER_MODEL when it is usable, else the default."""
        wanted = self.settings.llm_router_model
        if wanted:
            spec = self._spec_for(wanted)
            if spec is not None and self._usable(spec):
                return wanted
            log.warning("router_model_unavailable", model=wanted)
        return self.default_id

    # --- providers ------------------------------------------------------------------------------
    def get(self, model_id: str | None = None) -> LLMProvider:
        """The provider for ``model_id`` (None = the default). Unknown / unconfigured ids are rejected."""
        wanted = model_id or self.default_id
        if wanted is None:
            raise ValidationFailedError("No language model is configured", code="llm_not_configured")
        if wanted in self._providers:
            return self._providers[wanted]
        spec = self._spec_for(wanted)
        if spec is None or not self._usable(spec):
            raise ValidationFailedError(f"Unknown or unavailable model: {wanted}", code="unknown_model")
        provider = make_llm(self.settings, spec.provider, spec.id)
        self._providers[spec.id] = provider
        return provider

    # --- availability (quota) -------------------------------------------------------------------
    def mark_unavailable(self, model_id: str, reason: str, *, seconds: float = UNAVAILABLE_SECONDS) -> None:
        """Remember that ``model_id`` just failed on quota / usage limit, so the picker can grey it out.

        Selecting it is still allowed (the flag may be stale); the next success clears it.
        """
        self._blocked[model_id] = (time.monotonic() + seconds, reason)

    def clear_unavailable(self, model_id: str) -> None:
        self._blocked.pop(model_id, None)

    def unavailable_reason(self, model_id: str) -> str | None:
        entry = self._blocked.get(model_id)
        if entry is None:
            return None
        if entry[0] <= time.monotonic():
            del self._blocked[model_id]
            return None
        return entry[1]

    def register(self, provider: LLMProvider) -> None:
        """Make an already-built provider selectable under its ``model_id`` (used by tests and embedders)."""
        self._providers[provider.model_id] = provider

    def default_provider(self) -> LLMProvider | None:
        default = self.default_id
        return self.get(default) if default else None

    # --- cost -----------------------------------------------------------------------------------
    def cost(self, model_id: str, usage: Mapping[str, Any], day: date | None = None) -> float | None:
        """Estimated USD for ``usage`` on ``model_id`` at the price in force on ``day`` (None = price unknown)."""
        spec = self._spec_for(model_id)
        return usage_cost(spec.price_on(day or today()) if spec else None, usage)
