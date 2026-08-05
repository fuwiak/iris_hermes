"""Tests for process-env → HERMES_HOME/.env upsert (OpenRouter key rotation)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "sync_moysklad_env_from_railway.py"
)


def _load_mod():
    spec = importlib.util.spec_from_file_location("sync_moysklad_env", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_upsert_replaces_stale_openrouter_key(tmp_path: Path) -> None:
    mod = _load_mod()
    env = tmp_path / ".env"
    env.write_text(
        "OPENROUTER_API_KEY=sk-or-v1-OLD\nMOYSKLAD_ENABLED=true\n",
        encoding="utf-8",
    )
    updated, added = mod.upsert_env_file(
        env,
        {"OPENROUTER_API_KEY": "sk-or-v1-NEW", "DEEPSEEK_API_KEY": "sk-ds-NEW"},
    )
    assert updated == 1
    assert added == 1
    text = env.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-or-v1-NEW" in text
    assert "sk-or-v1-OLD" not in text
    assert "DEEPSEEK_API_KEY=sk-ds-NEW" in text
    assert "MOYSKLAD_ENABLED=true" in text


def test_prefer_process_env_syncs_llm_without_moysklad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_mod()
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=stale\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fresh")
    monkeypatch.delenv("MOYSKLAD_API_TOKEN", raising=False)
    assert mod.main(["--prefer-process-env", "--env-file", str(env)]) == 0
    assert "OPENROUTER_API_KEY=sk-or-v1-fresh" in env.read_text(encoding="utf-8")


def test_prefer_process_env_syncs_openrouter_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Railway egress URL must win over a stale volume .env value."""
    mod = _load_mod()
    env = tmp_path / ".env"
    env.write_text(
        "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "OPENROUTER_BASE_URL",
        "https://egress.example/t/secret/api/v1",
    )
    monkeypatch.delenv("MOYSKLAD_API_TOKEN", raising=False)
    assert mod.main(["--prefer-process-env", "--env-file", str(env)]) == 0
    text = env.read_text(encoding="utf-8")
    assert "OPENROUTER_BASE_URL=https://egress.example/t/secret/api/v1" in text
    assert "openrouter.ai" not in text
