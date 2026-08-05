#!/bin/sh
# Shared entrypoint for Railway slim image AND Selectel VDS (deploy/selectel).
# Binds dashboard to $PORT; seeds Iris theme + MoySklad plugin on the volume.
set -e

export HOME="${HERMES_HOME:-/opt/data}"
export HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HERMES_WEB_DIST="${HERMES_WEB_DIST:-/opt/hermes/hermes_cli/web_dist}"
# venv activate references $OSTYPE; keep nounset off around it.
export OSTYPE="${OSTYPE:-linux-gnu}"

PORT="${PORT:-${HERMES_DASHBOARD_PORT:-8080}}"
HOST="${HERMES_DASHBOARD_HOST:-0.0.0.0}"
INSTALL_DIR="${INSTALL_DIR:-/opt/hermes}"
PY="${INSTALL_DIR}/.venv/bin/python"

cd "$HERMES_HOME"

# Persist deploy secrets from process env into the volume .env.
# Hermes load_hermes_dotenv uses override=True — a stale OPENROUTER_API_KEY /
# OPENROUTER_BASE_URL on the volume otherwise wins over compose env_file after
# rotation (chat keeps returning OpenRouter HTTP 403 "Access denied by security
# policy"). Also used for Railway egress OPENROUTER_BASE_URL on Selectel RU.
if [ -x "$PY" ]; then
  "$PY" "$INSTALL_DIR/scripts/sync_moysklad_env_from_railway.py" \
    --prefer-process-env \
    --env-file "$HERMES_HOME/.env" \
    >/dev/null 2>&1 || true
fi

# Iris defaults on the persistent volume: violet dashboard theme + MoySklad
# plugin allow-list. Safe to re-run — never overrides an explicit disable.
# OPENROUTER_BASE_URL (Railway egress) overrides model.base_url so Selectel
# never dials openrouter.ai from a RU datacenter IP (HTTP 403 security policy).
OR_BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
OR_BASE_URL="${OR_BASE_URL%/}"
if [ -x "$PY" ] && [ -f "$INSTALL_DIR/scripts/docker_config_migrate.py" ]; then
  if [ ! -f "$HERMES_HOME/config.yaml" ]; then
    # Minimal first-boot config so migrate + deep-merge have a file to own.
    cat >"$HERMES_HOME/config.yaml" <<YAML
_config_version: 34
model:
  default: deepseek/deepseek-v4-flash-0731
  provider: openrouter
  base_url: ${OR_BASE_URL}
agent:
  reasoning_effort: medium
auxiliary:
  compression:
    model: deepseek/deepseek-v4-flash-0731
    reasoning_effort: medium
  moysklad_outreach:
    model: deepseek/deepseek-v4-flash-0731
    reasoning_effort: none
display:
  skin: iris
dashboard:
  theme: iris
plugins:
  enabled:
    - moysklad
  disabled: []
YAML
  fi
  HERMES_HOME="$HERMES_HOME" "$PY" "$INSTALL_DIR/scripts/docker_config_migrate.py" \
    >/dev/null 2>&1 || true
  # Force-merge Iris theme + moysklad enable when volume has a stale empty list
  # (disk `plugins.enabled: []` replaces DEFAULT_CONFIG and hides MoySklad).
  # Also pin model.base_url to OPENROUTER_BASE_URL when set (egress proxy).
  OPENROUTER_BASE_URL="$OR_BASE_URL" HERMES_HOME="$HERMES_HOME" "$PY" - <<'PY' || true
from __future__ import annotations

import os
from pathlib import Path

import yaml

from hermes_constants import get_hermes_home

path = get_hermes_home() / "config.yaml"
if not path.is_file():
    raise SystemExit(0)
raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
if not isinstance(raw, dict):
    raise SystemExit(0)
changed = False

display = raw.setdefault("display", {})
if isinstance(display, dict) and display.get("skin") in (None, "", "default", "mono"):
    display["skin"] = "iris"
    changed = True

dash = raw.setdefault("dashboard", {})
if isinstance(dash, dict) and dash.get("theme") in (None, "", "default", "mono"):
    dash["theme"] = "iris"
    changed = True

plugins = raw.setdefault("plugins", {})
if not isinstance(plugins, dict):
    plugins = {}
    raw["plugins"] = plugins
disabled = plugins.get("disabled") or []
if not isinstance(disabled, list):
    disabled = []
disabled_set = {str(x) for x in disabled}
enabled = plugins.get("enabled")
if not isinstance(enabled, list):
    enabled = []
if "moysklad" not in disabled_set and "moysklad" not in enabled:
    enabled = list(enabled) + ["moysklad"]
    plugins["enabled"] = enabled
    changed = True

# Default chat model: DeepSeek V4 Flash 0731 · Med (OpenRouter catalog id).
# Only fill when missing/empty — never clobber an explicit user pick.
_IRIS_MODEL = "deepseek/deepseek-v4-flash-0731"
_DEFAULT_OR = "https://openrouter.ai/api/v1"
_or_base = (os.environ.get("OPENROUTER_BASE_URL") or _DEFAULT_OR).strip().rstrip("/") or _DEFAULT_OR
# Env OPENROUTER_BASE_URL is authoritative on Selectel (Railway egress). When
# it differs from the default, always rewrite model.base_url — even if the
# volume still points at openrouter.ai from an older boot.
_force_or_base = _or_base != _DEFAULT_OR
model_cfg = raw.get("model")
if model_cfg is None or model_cfg == "" or model_cfg == {}:
    raw["model"] = {
        "default": _IRIS_MODEL,
        "provider": "openrouter",
        "base_url": _or_base,
    }
    changed = True
elif isinstance(model_cfg, dict):
    current = (model_cfg.get("default") or model_cfg.get("model") or "").strip()
    if not current:
        model_cfg["default"] = _IRIS_MODEL
        model_cfg.setdefault("provider", "openrouter")
        model_cfg.setdefault("base_url", _or_base)
        changed = True
    if _force_or_base and (model_cfg.get("base_url") or "").rstrip("/") != _or_base:
        model_cfg["base_url"] = _or_base
        model_cfg.setdefault("provider", "openrouter")
        changed = True
elif isinstance(model_cfg, str) and not model_cfg.strip():
    raw["model"] = {
        "default": _IRIS_MODEL,
        "provider": "openrouter",
        "base_url": _or_base,
    }
    changed = True

agent = raw.setdefault("agent", {})
if isinstance(agent, dict) and not (agent.get("reasoning_effort") or "").strip():
    agent["reasoning_effort"] = "medium"
    changed = True

aux = raw.setdefault("auxiliary", {})
if isinstance(aux, dict):
    compression = aux.setdefault("compression", {})
    if isinstance(compression, dict) and not (compression.get("model") or "").strip():
        compression["model"] = _IRIS_MODEL
        if not (compression.get("reasoning_effort") or "").strip():
            compression["reasoning_effort"] = "medium"
        changed = True
    # Outreach drafts must not inherit compression's medium reasoning —
    # that made campaign "Генерируем…" stall while chat already streamed.
    outreach = aux.setdefault("moysklad_outreach", {})
    if isinstance(outreach, dict):
        if not (outreach.get("model") or "").strip():
            outreach["model"] = _IRIS_MODEL
            changed = True
        # Force none even on older volumes that never had this block.
        if (outreach.get("reasoning_effort") or "").strip().lower() not in {
            "none",
            "false",
            "disabled",
        }:
            outreach["reasoning_effort"] = "none"
            changed = True

if changed:
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
PY
fi

HERMES_BIN="${INSTALL_DIR}/.venv/bin/hermes"
if [ -x "$HERMES_BIN" ]; then
  exec "$HERMES_BIN" dashboard \
    --host "$HOST" \
    --port "$PORT" \
    --no-open \
    --skip-build
fi

# shellcheck disable=SC1091
. "${INSTALL_DIR}/.venv/bin/activate"
exec hermes dashboard \
  --host "$HOST" \
  --port "$PORT" \
  --no-open \
  --skip-build
