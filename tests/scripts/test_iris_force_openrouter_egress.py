"""Regression: Iris chat must land on OpenRouter egress, not DeepSeek direct.

Production failure mode (Yandex/Selectel):
  config.yaml drifts to provider=deepseek / base_url=api.deepseek.com /
  default=deepseek-flash while OPENROUTER_BASE_URL points at Railway egress.
  Boot migrate used setdefault(provider) + empty .venv (no PyYAML) so the
  rewrite never stuck — dashboard chat submitted with a broken runtime and
  looked \"dead\".
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "iris_force_openrouter_egress.py"


def _load():
    spec = importlib.util.spec_from_file_location("iris_force_openrouter_egress", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_should_force_egress_only_for_non_default_url() -> None:
    mod = _load()
    assert mod.should_force_egress("https://telegram-user-egress.example/t/x/api/v1")
    assert not mod.should_force_egress("https://openrouter.ai/api/v1")
    assert not mod.should_force_egress("")
    assert not mod.should_force_egress(None)


def test_apply_rewrites_deepseek_volume_to_openrouter_egress() -> None:
    """Exact prod shape that left /chat mute."""
    mod = _load()
    raw = {
        "_config_version": 34,
        "model": {
            "default": "deepseek-flash",
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
        },
        "auxiliary": {
            "compression": {"model": "deepseek-flash", "reasoning_effort": "medium"},
            "moysklad_outreach": {"model": "deepseek-flash", "reasoning_effort": "none"},
        },
    }
    egress = "https://telegram-user-egress.example/t/tok/api/v1"
    assert mod.apply_iris_openrouter_egress(raw, openrouter_base_url=egress) is True
    assert raw["model"]["provider"] == "openrouter"
    assert raw["model"]["base_url"] == egress
    assert raw["model"]["default"] == "deepseek/deepseek-v4-flash-0731"
    assert raw["agent"]["reasoning_effort"] == "none"
    assert raw["auxiliary"]["compression"]["model"] == "deepseek/deepseek-v4-flash-0731"
    assert raw["auxiliary"]["compression"]["reasoning_effort"] == "none"
    assert raw["auxiliary"]["moysklad_outreach"]["model"] == "deepseek/deepseek-v4-flash-0731"


def test_apply_is_idempotent_when_already_on_egress() -> None:
    mod = _load()
    egress = "https://telegram-user-egress.example/t/tok/api/v1"
    raw = {
        "model": {
            "default": "deepseek/deepseek-v4-flash-0731",
            "provider": "openrouter",
            "base_url": egress,
        },
        "agent": {"reasoning_effort": "none"},
    }
    assert mod.apply_iris_openrouter_egress(raw, openrouter_base_url=egress) is False


def test_apply_pins_reasoning_effort_none_on_already_routed_egress() -> None:
    mod = _load()
    egress = "https://telegram-user-egress.example/t/tok/api/v1"
    raw = {
        "model": {
            "default": "deepseek/deepseek-v4-flash-0731",
            "provider": "openrouter",
            "base_url": egress,
        },
        "agent": {"reasoning_effort": "medium"},
    }
    assert mod.apply_iris_openrouter_egress(raw, openrouter_base_url=egress) is True
    assert raw["agent"]["reasoning_effort"] == "none"


def test_apply_skips_when_base_is_public_openrouter() -> None:
    mod = _load()
    raw = {
        "model": {
            "default": "deepseek-flash",
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
        }
    }
    assert (
        mod.apply_iris_openrouter_egress(
            raw, openrouter_base_url="https://openrouter.ai/api/v1"
        )
        is False
    )
    assert raw["model"]["provider"] == "deepseek"


def test_main_rewrites_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "model:\n"
        "  default: deepseek-flash\n"
        "  provider: deepseek\n"
        "  base_url: https://api.deepseek.com/v1\n",
        encoding="utf-8",
    )
    egress = "https://telegram-user-egress.example/t/tok/api/v1"
    monkeypatch.setenv("OPENROUTER_BASE_URL", egress)
    assert mod.main(["--config", str(cfg)]) == 0
    text = cfg.read_text(encoding="utf-8")
    assert "provider: openrouter" in text
    assert egress in text
    assert "deepseek/deepseek-v4-flash-0731" in text
    assert "api.deepseek.com" not in text
    assert "deepseek-flash" not in text
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"OPENROUTER_BASE_URL={egress}" in env
