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
# Hidden thinking on DeepSeek-flash via OpenRouter adds TTFT; Iris chat
# should answer, not reason. Boot pin so volume config cannot drift back.
_FAST_REASONING = frozenset({"none", "off", "minimal"})

# Bare DeepSeek / legacy ids that must not be sent to OpenRouter egress.
_LEGACY_MODEL_IDS = frozenset(
    {
        "deepseek-flash",
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-coder",
    }
)
# Live chat used these SKUs; OpenRouter routed them through Together and
# the SSE died ("h2 protocol error"). Pin back to the Iris flash id.
_SLOW_FLASH_ALIASES = frozenset(
    {
        "deepseek/deepseek-v4.1-flash",
        "deepseek/deepseek-v4-flash",
    }
)
_IRIS_IGNORE_PROVIDERS = ("Together",)


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
        changed = True
        model_cfg = raw["model"]
    else:
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
            or current in _SLOW_FLASH_ALIASES
            or (current.startswith("deepseek-") and "/" not in current)
        ):
            model_cfg["default"] = iris_model
            if "model" in model_cfg and not (model_cfg.get("default") or "").strip():
                model_cfg["model"] = iris_model
            changed = True

    agent = raw.get("agent")
    if not isinstance(agent, dict):
        raw["agent"] = {"reasoning_effort": "none"}
        changed = True
    else:
        effort = str(agent.get("reasoning_effort") or "").strip().lower()
        if effort not in _FAST_REASONING:
            agent["reasoning_effort"] = "none"
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
            effort = str(section.get("reasoning_effort") or "").strip().lower()
            if effort and effort not in _FAST_REASONING:
                section["reasoning_effort"] = "none"
                changed = True

    if _ensure_fast_provider_routing(raw):
        changed = True

    return changed


def _ensure_fast_provider_routing(raw: dict) -> bool:
    """Skip Together (SSE h2 drops) and pick the lowest-latency OpenRouter hop."""
    pr = raw.get("provider_routing")
    if not isinstance(pr, dict):
        raw["provider_routing"] = {
            "ignore": list(_IRIS_IGNORE_PROVIDERS),
            "sort": "latency",
        }
        return True

    changed = False
    ignore = pr.get("ignore")
    if not isinstance(ignore, list):
        ignore = []
        changed = True
    have = {str(item).strip().lower() for item in ignore}
    for name in _IRIS_IGNORE_PROVIDERS:
        if name.lower() not in have:
            ignore.append(name)
            changed = True
    if pr.get("ignore") != ignore:
        pr["ignore"] = ignore
        changed = True
    if str(pr.get("sort") or "").strip().lower() != "latency":
        pr["sort"] = "latency"
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

    changed = apply_iris_openrouter_egress(raw, openrouter_base_url=base)
    if changed:
        path.write_text(
            yaml.safe_dump(
                raw, allow_unicode=True, default_flow_style=False, sort_keys=False
            ),
            encoding="utf-8",
        )
        print(f"✓ {path}: forced provider=openrouter base_url=egress model={IRIS_MODEL}")
    else:
        print(f"✓ {path}: already on OpenRouter egress")

    # Always pin volume .env BASE_URL to process env — dotenv override=True
    # otherwise keeps a rotated-out /t/<token>/ and chat returns HTTP 401.
    env_path = path.parent / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    lines = text.splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("OPENROUTER_BASE_URL="):
            out.append(f"OPENROUTER_BASE_URL={base}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"OPENROUTER_BASE_URL={base}")
    env_path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    print(f"✓ {env_path}: OPENROUTER_BASE_URL pinned to process env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
