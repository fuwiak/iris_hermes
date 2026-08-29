"""Backtest of the «аналитика Вереск» report against a reference (call 22.08.2026).

Methodology from the client: hold out months from the reference report,
rebuild them from MoySklad data alone, then

- report which figures the system CANNOT find in MoySklad (`missing_data`
  — «мне не хватает цифр с такого-то маркетплейса»),
- accept manually supplied figures for those holes (``overrides`` —
  «вот тебе недостающие цифры»),
- diff the rebuilt months against the reference and surface mismatches
  for reconciliation.

Reference format (JSON, numbers straight from the client's Excel):

    {
      "months": {
        "2026-07": {
          "yandex_market": {"turnover": 100000, "orders": 42, "revenue": 70000},
          "flowwow": {"turnover": 50000}
        }
      }
    }

Metrics compared when present in the reference: turnover, revenue,
margin, orders, avg_check (the by_month series of
``dashboard_analytics.build_analytics``). Extra metrics in the
reference are ignored rather than failing.
"""

from __future__ import annotations

from typing import Any

COMPARABLE_METRICS = ("turnover", "revenue", "margin", "orders", "avg_check", "deliveries")


def extract_month_report(analytics: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """build_analytics() payload → {month_id: {channel: metrics}}.

    ``by_month.channels`` carries one series per metric aligned with
    ``by_month.periods`` — re-pivot it into per-month cells.
    """
    by_month = analytics.get("by_month") or {}
    periods = [str(p.get("id") or "") for p in by_month.get("periods") or []]
    out: dict[str, dict[str, dict[str, Any]]] = {m: {} for m in periods if m}
    for channel in by_month.get("channels") or []:
        key = str(channel.get("key") or "")
        if not key:
            continue
        for idx, month_id in enumerate(periods):
            if not month_id:
                continue
            cell: dict[str, Any] = {}
            for metric in COMPARABLE_METRICS:
                series = channel.get(metric)
                if isinstance(series, list) and idx < len(series):
                    cell[metric] = series[idx]
            out[month_id][key] = cell
    return out


def apply_overrides(
    computed: dict[str, dict[str, dict[str, Any]]],
    overrides: dict[str, Any] | None,
) -> list[str]:
    """Merge manually supplied figures into the computed report (in place).

    Returns human-readable notes of what was overridden.
    """
    notes: list[str] = []
    for month_id, channels in (overrides or {}).items():
        if not isinstance(channels, dict):
            continue
        month = computed.setdefault(str(month_id), {})
        for channel, metrics in channels.items():
            if not isinstance(metrics, dict):
                continue
            cell = month.setdefault(str(channel), {})
            for metric, value in metrics.items():
                cell[str(metric)] = value
                notes.append(f"{month_id}/{channel}: {metric} ← {value} (override)")
    return notes


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare_reports(
    computed: dict[str, dict[str, dict[str, Any]]],
    reference_months: dict[str, Any],
    *,
    months: list[str] | None = None,
    tolerance: float = 0.01,
) -> dict[str, Any]:
    """Diff computed vs reference for the held-out months.

    tolerance is relative (|delta| / max(|ref|, 1) ≤ tolerance → ok).
    """
    wanted = [str(m) for m in (months or sorted(reference_months))]
    ok: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for month_id in wanted:
        ref_channels = reference_months.get(month_id)
        if not isinstance(ref_channels, dict):
            missing.append(
                {
                    "month": month_id,
                    "channel": None,
                    "metric": None,
                    "reason": "месяца нет в reference-файле",
                }
            )
            continue
        got_channels = computed.get(month_id) or {}
        for channel, ref_metrics in sorted(ref_channels.items()):
            if not isinstance(ref_metrics, dict):
                continue
            cell = got_channels.get(channel)
            ref_turnover = _num(ref_metrics.get("turnover")) or 0.0
            got_turnover = _num((cell or {}).get("turnover")) or 0.0
            if (cell is None or got_turnover == 0.0) and ref_turnover > 0.0:
                missing.append(
                    {
                        "month": month_id,
                        "channel": channel,
                        "metric": "turnover",
                        "reference": ref_turnover,
                        "reason": (
                            f"в МоемСкладе нет оборота по «{channel}» за {month_id} — "
                            "нужны цифры из кабинета маркетплейса"
                        ),
                    }
                )
                continue
            for metric in COMPARABLE_METRICS:
                ref_val = _num(ref_metrics.get(metric))
                if ref_val is None:
                    continue
                got_val = _num((cell or {}).get(metric))
                row = {
                    "month": month_id,
                    "channel": channel,
                    "metric": metric,
                    "reference": ref_val,
                    "computed": got_val,
                }
                if got_val is None:
                    row["reason"] = "метрика не рассчитана"
                    missing.append(row)
                    continue
                delta = got_val - ref_val
                rel = abs(delta) / max(abs(ref_val), 1.0)
                row["delta"] = round(delta, 2)
                row["relative"] = round(rel, 4)
                (ok if rel <= tolerance else mismatches).append(row)
    return {
        "ok": ok,
        "mismatches": mismatches,
        "missing_data": missing,
        "months": wanted,
        "tolerance": tolerance,
        "verdict": (
            "match"
            if not mismatches and not missing
            else "needs_data"
            if missing and not mismatches
            else "mismatch"
        ),
    }


def reference_template(
    computed: dict[str, dict[str, dict[str, Any]]],
    *,
    months: list[str] | None = None,
) -> dict[str, Any]:
    """Skeleton reference JSON prefilled with computed values.

    Hand it to the client / take the client's Excel and replace the numbers —
    then the backtest compares against ground truth instead of ourselves.
    """
    wanted = [m for m in (months or sorted(computed))]
    out: dict[str, Any] = {"months": {}}
    for month_id in wanted:
        out["months"][month_id] = {
            channel: {
                metric: cell.get(metric)
                for metric in COMPARABLE_METRICS
                if cell.get(metric) is not None
            }
            for channel, cell in sorted((computed.get(month_id) or {}).items())
        }
    return out
