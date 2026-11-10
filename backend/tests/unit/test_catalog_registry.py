"""Model catalog, price schedules, cost maths, and the registry that maps a model id to a provider."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import ValidationFailedError
from app.rag.generation import registry as registry_module
from app.rag.generation.catalog import Catalog, ModelSpec, Price, load_catalog, merge_usage, usage_cost
from app.rag.generation.registry import ModelRegistry
from app.services.chat import usage_record
from tests.fakes import ScriptedLLM
from tests.helpers import make_settings

# --- the shipped catalog ------------------------------------------------------------------------


def test_builtin_catalog_is_valid_and_every_model_has_a_price() -> None:
    catalog = load_catalog()
    assert len(catalog.models) >= 8
    for model in catalog.models:
        assert model.provider in ("openai", "anthropic", "google")
        price = model.price_on(date(2026, 11, 9))
        assert price is not None and price.input > 0 and price.output >= price.input, model.id
        assert price.cached_input is None or price.cached_input <= price.input, model.id


def test_builtin_catalog_covers_all_three_providers() -> None:
    assert {m.provider for m in load_catalog().models} == {"openai", "anthropic", "google"}


def test_price_schedule_switches_on_the_effective_date() -> None:
    spec = load_catalog().get("gemini-3.6-flash")
    assert spec is not None
    assert spec.price_on(date(2026, 12, 31)).input == 0.75  # type: ignore[union-attr]
    assert spec.price_on(date(2027, 1, 1)).input == 1.5  # type: ignore[union-attr]
    assert spec.price_on(date(2030, 1, 1)).output == 7.5  # type: ignore[union-attr]


def test_price_before_every_dated_entry_falls_back_to_the_earliest() -> None:
    spec = ModelSpec(
        id="m",
        provider="openai",
        label="m",
        prices=[Price(**{"from": date(2027, 1, 1), "input": 2, "output": 4}), Price(input=1, output=2)],
    )
    assert spec.price_on(date(2026, 1, 1)).input == 1  # type: ignore[union-attr]
    assert ModelSpec(id="m", provider="openai", label="m").price_on(date(2026, 1, 1)) is None


# --- cost ---------------------------------------------------------------------------------------


def test_cost_of_plain_cached_and_cache_written_tokens() -> None:
    price = Price(input=2.0, cached_input=0.2, cache_write=2.5, output=10.0)
    usage = {
        "input_tokens": 10_000,  # the whole prompt, including the 4,000 read from and 1,000 written to the cache
        "cache_read_input_tokens": 4_000,
        "cache_creation_input_tokens": 1_000,
        "output_tokens": 500,
    }
    # 5,000 plain * $2 + 4,000 cached * $0.2 + 1,000 written * $2.5 + 500 out * $10, per million
    assert usage_cost(price, usage) == pytest.approx((5_000 * 2 + 4_000 * 0.2 + 1_000 * 2.5 + 500 * 10) / 1e6)


def test_cost_without_cache_prices_charges_the_input_rate() -> None:
    assert usage_cost(Price(input=1.0, output=3.0), {"input_tokens": 1_000, "cache_read_input_tokens": 400}) == (
        pytest.approx(1_000 / 1e6)
    )


def test_cost_is_none_without_a_price_and_zero_without_tokens() -> None:
    assert usage_cost(None, {"input_tokens": 5}) is None
    assert usage_cost(Price(input=1, output=1), {}) == 0


def test_merge_usage_sums_known_counters_only() -> None:
    merged = merge_usage(
        {"input_tokens": 100, "output_tokens": 10, "junk": 5}, {"input_tokens": 50, "reasoning_tokens": 7}
    )
    assert merged == {"input_tokens": 150, "output_tokens": 10, "reasoning_tokens": 7}
    assert merge_usage({}, {"output_tokens": 0}) == {}


# --- loading ------------------------------------------------------------------------------------


def test_custom_catalog_file(tmp_path: Path) -> None:
    f = tmp_path / "models.json"
    f.write_text(
        json.dumps({"models": [{"id": "x", "provider": "openai", "label": "X", "prices": [{"input": 1, "output": 2}]}]})
    )
    assert [m.id for m in load_catalog(f).models] == ["x"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"models": [{"id": "x", "provider": "nope", "label": "X"}]}),
        json.dumps(
            {"models": [{"id": "x", "provider": "openai", "label": "X", "prices": [{"input": -1, "output": 1}]}]}
        ),
        json.dumps(
            {
                "models": [
                    {"id": "x", "provider": "openai", "label": "A"},
                    {"id": "x", "provider": "openai", "label": "B"},
                ]
            }
        ),
    ],
)
def test_a_broken_catalog_fails_loudly(tmp_path: Path, content: str) -> None:
    f = tmp_path / "models.json"
    f.write_text(content)
    with pytest.raises(ValueError, match="Invalid model catalog"):
        load_catalog(f)
    with pytest.raises(ValueError, match="Invalid model catalog"):
        load_catalog(tmp_path / "missing.json")


# --- registry -----------------------------------------------------------------------------------


def make_registry(**settings: Any) -> ModelRegistry:
    return ModelRegistry(make_settings(**settings))


def ids(registry: ModelRegistry) -> list[str]:
    return [o.spec.id for o in registry.available()]


def test_only_models_with_a_configured_key_are_offered() -> None:
    reg = make_registry(llm_provider="openai", llm_model="gpt-5-nano", openai_api_key="k")
    assert ids(reg) and all(m.startswith("gpt") for m in ids(reg))
    reg = make_registry(llm_provider="openai", llm_model="gpt-5-nano", openai_api_key="k", google_api_key="g")
    assert {o.spec.provider for o in reg.available()} == {"openai", "google"}
    assert make_registry().available() == []  # no keys at all
    assert make_registry().default_id is None


def test_the_default_model_is_flagged_and_listed_even_when_not_in_the_catalog() -> None:
    reg = make_registry(llm_provider="openai", llm_model="gpt-5-nano", openai_api_key="k")
    assert [o.spec.id for o in reg.available() if o.is_default] == ["gpt-5-nano"]
    custom = make_registry(llm_provider="openai", llm_model="my-finetune", openai_api_key="k")
    assert custom.default_id == "my-finetune" and ids(custom)[0] == "my-finetune"
    assert custom.available()[0].price() is None  # unknown price: tokens are shown, cost is not


def test_default_falls_back_to_an_available_model_when_its_provider_has_no_key() -> None:
    reg = make_registry(llm_provider="openai", llm_model="gpt-5-nano", anthropic_api_key="a")
    assert reg.default_id is not None and reg.default_id.startswith("claude")
    assert "gpt-5-nano" not in ids(reg)


def test_unknown_or_unconfigured_models_are_rejected() -> None:
    reg = make_registry(llm_provider="openai", llm_model="gpt-5-nano", openai_api_key="k")
    for bad in ("does-not-exist", "claude-opus-5-5", "gemini-3.6-flash"):  # unknown, and two without a key
        with pytest.raises(ValidationFailedError) as exc:
            reg.get(bad)
        assert exc.value.code == "unknown_model"
    with pytest.raises(ValidationFailedError) as exc:
        make_registry().get(None)
    assert exc.value.code == "llm_not_configured"


def test_providers_are_built_lazily_once(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple[str, str]] = []

    def fake_make_llm(settings: Any, provider: str, model: str) -> ScriptedLLM:
        built.append((provider, model))
        return ScriptedLLM(model)

    monkeypatch.setattr(registry_module, "make_llm", fake_make_llm)
    reg = make_registry(llm_provider="openai", llm_model="gpt-5-nano", openai_api_key="k", google_api_key="g")
    assert built == []  # nothing is built just to list the models
    first = reg.get("gemini-3.6-flash")
    assert reg.get("gemini-3.6-flash") is first and built == [("google", "gemini-3.6-flash")]
    assert reg.default_provider().model_id == "gpt-5-nano"  # type: ignore[union-attr]
    assert built[-1] == ("openai", "gpt-5-nano")


def test_an_injected_default_provider_is_used_and_listed() -> None:
    fake = ScriptedLLM("test/fake")
    reg = ModelRegistry(make_settings(), default=fake)
    assert reg.default_id == "test/fake" and reg.get(None) is fake and ids(reg) == ["test/fake"]
    other = ScriptedLLM("test/other")
    reg.register(other)
    assert reg.get("test/other") is other


def test_router_model_prefers_a_usable_setting_else_the_default() -> None:
    both = {"llm_provider": "openai", "llm_model": "gpt-6-sol", "openai_api_key": "k", "google_api_key": "g"}
    assert make_registry(**both).router_id == "gpt-6-sol"
    assert make_registry(**both, llm_router_model="gemini-3.5-flash-lite").router_id == "gemini-3.5-flash-lite"
    assert make_registry(**both, llm_router_model="claude-haiku-5-5").router_id == "gpt-6-sol"  # no Anthropic key


def test_registry_cost_uses_the_price_in_force_on_the_day() -> None:
    reg = make_registry(llm_provider="google", llm_model="gemini-3.6-flash", google_api_key="g")
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert reg.cost("gemini-3.6-flash", usage, date(2026, 12, 1)) == pytest.approx(0.75 + 3.75)
    assert reg.cost("gemini-3.6-flash", usage, date(2027, 2, 1)) == pytest.approx(1.5 + 7.5)
    assert reg.cost("not-a-model", usage) is None


# --- CHAT_MODEL_IDS: what users may pick -----------------------------------------------------------


def test_chat_model_ids_limits_what_users_can_pick() -> None:
    reg = make_registry(
        llm_provider="openai",
        llm_model="gpt-5-nano",
        openai_api_key="k",
        chat_model_ids=" gpt-5-nano, gpt-5-mini ,nope",
    )
    assert ids(reg) == ["gpt-5-nano", "gpt-5-mini"]  # ids that aren't offered are simply ignored
    assert reg.is_selectable("gpt-5-mini") and not reg.is_selectable("gpt-5.5") and not reg.is_selectable("nope")
    assert reg.get("gpt-5.5").model_id == "gpt-5.5"  # internal callers are not restricted, only user choices


def test_the_default_moves_to_an_allowed_model_and_routing_is_not_restricted() -> None:
    both = {"llm_provider": "openai", "llm_model": "gpt-5.5", "openai_api_key": "k"}
    reg = make_registry(**both, chat_model_ids="gpt-5-mini", llm_router_model="gpt-6-luna")
    assert reg.default_id == "gpt-5-mini" and [o.is_default for o in reg.available()] == [True]
    assert reg.router_id == "gpt-6-luna"  # routing only needs a usable model, not an allowed one
    assert make_registry(**both).default_id == "gpt-5.5"  # no allowlist: the configured default


def test_an_empty_allowlist_offers_everything() -> None:
    reg = make_registry(llm_provider="openai", llm_model="gpt-5-nano", openai_api_key="k", chat_model_ids="  ")
    assert reg.settings.chat_model_id_list == [] and len(ids(reg)) == 5


# --- the per-reply usage record -----------------------------------------------------------------


def priced_registry() -> ModelRegistry:
    reg = ModelRegistry(
        make_settings(),
        catalog=Catalog(
            models=[
                ModelSpec(id="big", provider="openai", label="Big", prices=[Price(input=10, output=50)]),
                ModelSpec(id="small", provider="openai", label="Small", prices=[Price(input=1, output=2)]),
                ModelSpec(id="mystery", provider="openai", label="Mystery"),
            ]
        ),
    )
    for model in ("big", "small", "mystery"):
        reg.register(ScriptedLLM(model))
    return reg


def test_usage_record_totals_both_calls_and_prices_each_with_its_own_model() -> None:
    record = usage_record(
        priced_registry(),
        answer_model="big",
        answer_usage={"input_tokens": 2_000, "output_tokens": 500, "reasoning_tokens": 300},
        served_model="big-2026-11-01",
        router_model="small",
        routing_usage={"input_tokens": 400, "output_tokens": 20},
    )
    assert record["input_tokens"] == 2_400 and record["output_tokens"] == 520 and record["reasoning_tokens"] == 300
    assert record["routing"]["cost_usd"] == pytest.approx((400 * 1 + 20 * 2) / 1e6)
    assert record["answer"]["cost_usd"] == pytest.approx((2_000 * 10 + 500 * 50) / 1e6)
    assert record["answer"]["model"] == "big" and record["answer"]["model_version"] == "big-2026-11-01"
    assert record["cost_usd"] == pytest.approx(record["routing"]["cost_usd"] + record["answer"]["cost_usd"])


def test_usage_record_without_routing_or_without_usage() -> None:
    reg = priced_registry()
    only_answer = usage_record(
        reg, answer_model="small", answer_usage={"input_tokens": 10, "output_tokens": 5}, served_model=None,
        router_model="small", routing_usage={},
    )  # fmt: skip
    assert "routing" not in only_answer and only_answer["cost_usd"] == pytest.approx((10 + 10) / 1e6)
    assert (
        usage_record(
            reg, answer_model="small", answer_usage={}, served_model=None, router_model="small", routing_usage={}
        )
        == {}
    )


def test_one_unpriced_call_makes_the_total_cost_unknown_but_keeps_the_tokens() -> None:
    record = usage_record(
        priced_registry(), answer_model="mystery", answer_usage={"input_tokens": 10, "output_tokens": 5},
        served_model=None, router_model="small", routing_usage={"input_tokens": 3, "output_tokens": 1},
    )  # fmt: skip
    assert record["cost_usd"] is None and record["input_tokens"] == 13
    assert record["answer"]["cost_usd"] is None and record["routing"]["cost_usd"] is not None
