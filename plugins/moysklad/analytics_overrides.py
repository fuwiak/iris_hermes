"""Manual analytics fills so the Дашборд can match the client's Excel.

Excel «образец аналитика Вереск» has rows MoySklad cannot derive alone:
  - purchase (закупка) — used in ``margin = revenue - purchase * share``
  - deliveries (доставки) — often courier/own logistics, not only Yandex fees

Drop a JSON file at ``$HERMES_HOME/moysklad/analytics_overrides.json``:

    {
      "purchase_by_month": {"2025-12": 2500000},
      "deliveries_by_month": {
        "2025-09": 56680,
        "2025-10": 68719,
        "2025-11": 112520,
        "2025-12": 282870
      },
      "yandex_use_cabinet": true
    }

``yandex_use_cabinet`` (default true): replace Яндекс Маркет оборот with
partner-API BUYER totals and prefer cabinet order counts. Deliveries fall
back to Yandex delivery commissions when the month is not overridden here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_OVERRIDES_NAME = "analytics_overrides.json"


def overrides_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "moysklad" / _OVERRIDES_NAME


def _bundled_example_path() -> Path:
    return Path(__file__).resolve().parent / "analytics_overrides.example.json"


def _default_overrides() -> dict[str, Any]:
    return {
        "purchase_by_month": {},
        "deliveries_by_month": {},
        "yandex_use_cabinet": True,
    }


def _coerce_overrides(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _default_overrides()
    purchase = raw.get("purchase_by_month") or {}
    deliveries = raw.get("deliveries_by_month") or {}
    use_cabinet = raw.get("yandex_use_cabinet")
    return {
        "purchase_by_month": {
            str(k): float(v)
            for k, v in purchase.items()
            if v is not None and str(k)
        }
        if isinstance(purchase, dict)
        else {},
        "deliveries_by_month": {
            str(k): float(v)
            for k, v in deliveries.items()
            if v is not None and str(k)
        }
        if isinstance(deliveries, dict)
        else {},
        "yandex_use_cabinet": True if use_cabinet is None else bool(use_cabinet),
    }


def load_analytics_overrides() -> dict[str, Any]:
    """Load HERMES_HOME overrides; fall back to bundled example (web deploys)."""
    path = overrides_path()
    if path.is_file():
        try:
            return _coerce_overrides(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            log.warning("analytics_overrides unreadable (%s): %s", path, exc)
    example = _bundled_example_path()
    if example.is_file():
        try:
            out = _coerce_overrides(json.loads(example.read_text(encoding="utf-8")))
            log.info("analytics_overrides: using bundled example %s", example)
            return out
        except Exception as exc:
            log.warning("bundled analytics_overrides.example unreadable: %s", exc)
    return _default_overrides()



def analytics_kwargs_from_env() -> dict[str, Any]:
    """Kwargs for ``build_analytics``: overrides + optional Yandex cabinet."""
    overrides = load_analytics_overrides()
    cabinet = None
    if overrides.get("yandex_use_cabinet", True):
        try:
            from plugins.moysklad.yandex_stats import yandex_monthly_stats_cached

            cabinet = yandex_monthly_stats_cached(months=14)
        except Exception:
            log.warning("yandex cabinet stats unavailable for analytics", exc_info=True)
    return {
        "purchase_by_month": overrides.get("purchase_by_month") or {},
        "deliveries_by_month": overrides.get("deliveries_by_month") or {},
        "yandex_cabinet": cabinet,
        "yandex_use_cabinet": bool(overrides.get("yandex_use_cabinet", True)),
    }
