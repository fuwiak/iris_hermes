#!/usr/bin/env python3
"""Force Iris dashboard chat onto OpenRouter egress when env says so.

Selectel/Yandex VDS cannot dial openrouter.ai (HTTP 403). Chat must use
``OPENROUTER_BASE_URL`` (Railway telegram-user-egress proxy) with an
OpenRouter catalog model id.

Volume ``config.yaml`` often drifts to ``provider: deepseek`` +
``base_url: https://api.deepseek.com/v1`` + bare ``deepseek-flash``. The
boot inline migrate used to skip (empty ``.venv`` → no PyYAML) or only
``setdefault`` provider, so chat stayed on a broken combo.

This script is idempotent and safe to run every boot when
``OPENROUTER_BASE_URL`` is a non-default egress URL.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_OPENROUTER = "https://openrouter.ai/api/v1"
IRIS_MODEL = "deepseek/deepseek-v4-flash-0731"

# Bare DeepSeek / legacy ids that must not be sent to OpenRouter egress.
_LEGACY_MODEL_IDS = frozenset(
    {
        "deepseek-flash",
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-coder",
    }
)


def _normalize_base(url: str) -> str:
    return (url or "").strip().rstrip("/")


def should_force_egress(openrouter_base_url: str | None = None) -> bool:
    base = _normalize_base(
        openrouter_base_url
        if openrouter_base_url is not None
        else os.environ.get("OPENROUTER_BASE_URL", "")
    )
    return bool(base) and base != DEFAULT_OPENROUTER


def apply_iris_openrouter_egress(
    raw: dict,
    *,
    openrouter_base_url: str,
    iris_model: str = IRIS_MODEL,
) -> bool:
    """Mutate config dict. Return True if anything changed."""
    base = _normalize_base(openrouter_base_url)
    if not base or base == DEFAULT_OPENROUTER:
        return False

    changed = False
    model_cfg = raw.get("model")
    if not isinstance(model_cfg, dict):
        raw["model"] = {
            "default": iris_model,
            "provider": "openrouter",
            "base_url": base,
        }
        return True

    current = (model_cfg.get("default") or model_cfg.get("model") or "").strip()
    provider = (model_cfg.get("provider") or "").strip().lower()
    cfg_base = _normalize_base(str(model_cfg.get("base_url") or ""))

    if cfg_base != base:
        model_cfg["base_url"] = base
        changed = True
    if provider != "openrouter":
        model_cfg["provider"] = "openrouter"
        changed = True
    if (
        not current
        or current in _LEGACY_MODEL_IDS
        or current.startswith("deepseek-")
        and "/" not in current
    ):
        model_cfg["default"] = iris_model
        if "model" in model_cfg and not (model_cfg.get("default") or "").strip():
            model_cfg["model"] = iris_model
        changed = True

    aux = raw.get("auxiliary")
    if isinstance(aux, dict):
        for section_name in ("compression", "moysklad_outreach"):
            section = aux.get(section_name)
            if not isinstance(section, dict):
                continue
            mid = (section.get("model") or "").strip()
            if (
                not mid
                or mid in _LEGACY_MODEL_IDS
                or (mid.startswith("deepseek-") and "/" not in mid)
            ):
                section["model"] = iris_model
                changed = True

    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="config.yaml path (default: $HERMES_HOME/config.yaml)",
    )
    args = parser.parse_args(argv)

    if not should_force_egress():
        print("✓ skip: OPENROUTER_BASE_URL unset or default openrouter.ai")
        return 0

    try:
        import yaml
    except ImportError:
        print("✗ PyYAML missing — cannot rewrite config.yaml", file=sys.stderr)
        return 2

    base = _normalize_base(os.environ.get("OPENROUTER_BASE_URL", ""))
    if args.config is not None:
        path = args.config
    else:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        path = home / "config.yaml"

    if not path.is_file():
        print(f"✓ skip: no config at {path}")
        return 0

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        print(f"✗ invalid config.yaml at {path}", file=sys.stderr)
        return 1

    if not apply_iris_openrouter_egress(raw, openrouter_base_url=base):
        print(f"✓ {path}: already on OpenRouter egress")
        return 0

    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"✓ {path}: forced provider=openrouter base_url=egress model={IRIS_MODEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
