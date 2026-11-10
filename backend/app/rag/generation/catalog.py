"""The models users can pick in chat, and what their tokens cost.

The catalog is data (``models.json``, or the file named by ``LLM_CATALOG_FILE``), so adding a model or
updating a price needs no code change. A model is offered only when its provider's API key is configured.

Costs are *estimates*: token counts come from the provider's usage report, prices from the catalog.
Prices carry an optional ``from`` date (providers announce increases in advance); the price in force on
the day of the request applies, and the cost is stored with the message, so history never changes when
the catalog does.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Provider = Literal["openai", "anthropic", "google"]
BUILTIN_CATALOG = Path(__file__).with_name("models.json")

# Token counters, in the provider-independent shape every LLMProvider reports. ``input_tokens`` is the whole
# prompt (including cache reads and writes); ``output_tokens`` includes reasoning ("thinking") tokens.
USAGE_COUNTERS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


class Price(BaseModel):
    """USD per 1M tokens."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    effective_from: date | None = Field(default=None, alias="from")
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: float | None = Field(default=None, ge=0)  # tokens served from the prompt cache
    cache_write: float | None = Field(default=None, ge=0)  # tokens written to the cache (Claude)


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    provider: Provider
    label: str = Field(min_length=1)
    description: str = ""
    prices: list[Price] = Field(default_factory=list)

    def price_on(self, day: date) -> Price | None:
        """The price in force on ``day``; the earliest known one if every entry is dated in the future."""
        if not self.prices:
            return None
        dated = sorted(self.prices, key=lambda p: p.effective_from or date.min)
        current = [p for p in dated if p.effective_from is None or p.effective_from <= day]
        return current[-1] if current else dated[0]


class Catalog(BaseModel):
    model_config = ConfigDict(extra="ignore")

    updated: date | None = None
    note: str = ""
    models: list[ModelSpec]

    @model_validator(mode="after")
    def _unique_ids(self) -> Catalog:
        ids = [m.id for m in self.models]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate model ids in the catalog: {duplicates}")
        return self

    def get(self, model_id: str) -> ModelSpec | None:
        return next((m for m in self.models if m.id == model_id), None)


def load_catalog(path: Path | None = None) -> Catalog:
    """Read the catalog file; a broken file fails loudly at start-up instead of showing wrong prices."""
    source = path or BUILTIN_CATALOG
    try:
        return Catalog.model_validate(json.loads(source.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Invalid model catalog {source}: {exc}") from exc


def today() -> date:
    return datetime.now(UTC).date()


# --- usage and cost ----------------------------------------------------------------------------------


def merge_usage(*parts: Mapping[str, Any]) -> dict[str, int]:
    """Sum token counters across calls (e.g. the routing call and the answer)."""
    total: dict[str, int] = {}
    for part in parts:
        for key in USAGE_COUNTERS:
            value = part.get(key)
            if value:
                total[key] = total.get(key, 0) + int(value)
    return total


def usage_cost(price: Price | None, usage: Mapping[str, Any]) -> float | None:
    """Estimated USD for one call's usage; None when the model's price is unknown."""
    if price is None:
        return None
    prompt = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cache_read_input_tokens") or 0)
    written = int(usage.get("cache_creation_input_tokens") or 0)
    output = int(usage.get("output_tokens") or 0)
    plain = max(prompt - cached - written, 0)
    cached_rate = price.input if price.cached_input is None else price.cached_input
    write_rate = price.input if price.cache_write is None else price.cache_write
    return (plain * price.input + cached * cached_rate + written * write_rate + output * price.output) / 1_000_000


def model_ids(catalog: Catalog) -> Iterable[str]:
    return (m.id for m in catalog.models)
