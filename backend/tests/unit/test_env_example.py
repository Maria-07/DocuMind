"""`.env.example` is copied to `.env` by the docs and the deploy script, so it must parse the way it reads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.core.config import Settings

EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


def example_values() -> dict[str, str]:
    return {k: v for k, v in dotenv_values(EXAMPLE).items() if v is not None}


def test_no_value_is_secretly_a_comment() -> None:
    """`KEY=   # note` is read as KEY="# note" by dotenv: a bogus API key or model name. Put comments above."""
    bad = [k for k, v in example_values().items() if v.lstrip().startswith("#")]
    assert bad == []


def test_every_value_is_valid_for_its_setting() -> None:
    fields = {name.upper(): name for name in Settings.model_fields}
    known: dict[str, Any] = {fields[k]: v for k, v in example_values().items() if k in fields and v != ""}
    settings = Settings(_env_file=None, **known)
    assert settings.chat_model_id_list == []  # empty = every model with a key
    assert settings.registration_enabled is True  # the example keeps today's behaviour; the deploy script closes it
    assert settings.llm_router_model is None and settings.llm_catalog_file is None and settings.eval_judge_model is None


def test_blank_optional_settings_mean_unset(monkeypatch) -> None:
    """docker compose hands an empty `.env` entry to the container as ''; that must not become Path('.')."""
    from tests.helpers import make_settings

    settings = make_settings(llm_catalog_file="", llm_router_model="")
    assert settings.llm_catalog_file is None
    assert settings.llm_router_model is None
