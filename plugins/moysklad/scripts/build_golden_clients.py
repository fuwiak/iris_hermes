#!/usr/bin/env python3
"""Export ~20 rich MoySklad clients into a golden eval fixture.

Selection: maximize order count, multi-channel, tags/attrs, seasonal months
(e.g. March / Feb / Sept / Dec) so AI hallucination is detectable vs facts.

Usage (from repo root, with MOYSKLAD_API_TOKEN in env or ~/.hermes/.env):

  python plugins/moysklad/scripts/build_golden_clients.py
  python plugins/moysklad/scripts/build_golden_clients.py --limit 20 --max-orders 8000

Does NOT print or write API tokens. Writes:
  plugins/moysklad/eval/golden_clients_v1.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv() -> None:
    """Best-effort load of ~/.hermes/.env without overwriting existing env."""
    candidates = [
        Path(os.environ.get("HERMES_HOME", "")).expanduser() / ".env"
        if os.environ.get("HERMES_HOME")
        else None,
        Path.home() / ".hermes" / ".env",
        REPO_ROOT / "reference" / "selectel-kinetic-deploy" / "deploy.env",
    ]
    for path in candidates:
        if path is None or not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key.startswith("MOYSKLAD_") and key not in os.environ and val:
                    os.environ[key] = val
        except OSError:
            continue


def _minor_to_rub(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _entity_id(ref: Any) -> str | None:
    if isinstance(ref, dict):
        rid = ref.get("id")
        if rid:
            return str(rid)
        meta = ref.get("meta") or {}
        href = str(meta.get("href") or "")
        if href:
            return href.rstrip("/").rsplit("/", 1)[-1] or None
    return None


def _channel_name(order: dict[str, Any], channels_by_id: dict[str, str]) -> str:
    sc = order.get("salesChannel") or order.get("sales_channel")
    if isinstance(sc, str) and sc.strip():
        return sc.strip()
    if isinstance(sc, dict):
        name = sc.get("name")
        if name:
            return str(name).strip()
        cid = _entity_id(sc)
        if cid and cid in channels_by_id:
            return channels_by_id[cid]
    return ""


def _attr_map(cp: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raw = cp.get("attributes")
    if not isinstance(raw, list):
        return out
    for attr in raw:
        if not isinstance(attr, dict):
            continue
        name = str(attr.get("name") or "").strip()
        if not name:
            continue
        val = attr.get("value")
        if isinstance(val, dict):
            val = val.get("name") or val.get("value") or val
        out[name] = val
    return out


def _position_snippets(client: Any, order_id: str, *, max_lines: int = 5) -> list[str]:
    try:
        payload = client.positions(order_id)
    except Exception:
        return []
    snippets: list[str] = []
    for pos in list(payload.get("rows") or [])[:max_lines]:
        assortment = pos.get("assortment") if isinstance(pos.get("assortment"), dict) else {}
        name = str(assortment.get("name") or pos.get("name") or "").strip()
        qty = pos.get("quantity")
        if name:
            snippets.append(f"{name}" + (f" ×{qty}" if qty not in (None, "") else ""))
    return snippets


_AGGREGATE_NAME_RE = re.compile(
    r"(яндекс|yandex|озон|ozon|wildberries|wb\b|флавери|flaveri|флау.?вау|"
    r"flow.?wow|не\s*использовать|неизвестн|без\s*номера|аноним|"
    r"розничн(ый)?\s*покупател|маркет\s*плейс|marketplace|"
    r"!еда|еда\s*\(|skyloft)",
    re.IGNORECASE,
)


def _is_aggregate_stub(entry: dict[str, Any]) -> bool:
    """Skip marketplace buckets / placeholders — useless for seller-eval."""
    name = str(entry.get("name") or "").strip()
    if not name or _AGGREGATE_NAME_RE.search(name):
        return True
    # Pure channel buckets often have huge order counts and no personal attrs.
    tags = entry.get("tags") or []
    attrs = entry.get("attributes") or {}
    contacts = any(entry.get(k) for k in ("phone", "email", "tg_nick", "tg_conversation"))
    if entry.get("order_count", 0) >= 40 and not tags and not attrs and not contacts:
        return True
    return False


def _richness_score(entry: dict[str, Any]) -> tuple:
    orders = entry.get("orders") or []
    months = {str(o.get("date") or "")[5:7] for o in orders if len(str(o.get("date") or "")) >= 7}
    seasonal = bool(months & {"02", "03", "09", "12", "01"})
    channels = {str(o.get("channel") or "") for o in orders if o.get("channel")}
    attrs = entry.get("attributes") or {}
    tags = entry.get("tags") or []
    contacts = sum(
        1
        for k in ("phone", "email", "tg_nick", "tg_conversation")
        if entry.get(k)
    )
    has_lines = sum(1 for o in orders if o.get("line_items"))
    # Prefer named retail clients with tags/contacts over raw volume.
    named = 1 if (contacts or tags or attrs) else 0
    # Cap order-count influence so 800-order marketplace rows don't dominate
    # after filter; still reward multi-order history.
    order_score = min(len(orders), 40)
    return (
        named,
        order_score,
        len(channels),
        1 if seasonal else 0,
        len(tags) + min(len(attrs), 12),
        contacts,
        has_lines,
        len(orders),  # tie-break
    )


def select_and_export(
    *,
    limit: int,
    max_orders: int,
    max_counterparties: int,
    fetch_positions: bool,
    positions_per_client: int,
    out_path: Path,
) -> dict[str, Any]:
    from plugins.moysklad.client import MoySkladClient, token_configured
    from plugins.moysklad.sales_channels import counterparty_row_from_api

    if not token_configured():
        raise SystemExit(
            "MOYSKLAD_API_TOKEN missing. Set it in the environment or ~/.hermes/.env"
        )

    client = MoySkladClient()
    print("Fetching channels…", flush=True)
    channels_payload = client.channels(fetch_all=True, limit=0)
    channels_by_id = {
        str(c.get("id")): str(c.get("name") or "")
        for c in (channels_payload.get("rows") or [])
        if c.get("id")
    }

    print(f"Fetching up to {max_orders} orders…", flush=True)
    orders_payload = client.orders(fetch_all=True, limit=max_orders)
    orders = list(orders_payload.get("rows") or [])
    print(f"  orders: {len(orders)}", flush=True)

    by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for order in orders:
        aid = _entity_id(order.get("agent"))
        if not aid:
            continue
        by_agent[aid].append(order)

    print(f"Fetching counterparties (limit={max_counterparties or 'all'})…", flush=True)
    cps_payload = client.counterparties(
        fetch_all=True, limit=max_counterparties, include_archived=False
    )
    counterparties = {str(cp.get("id")): cp for cp in (cps_payload.get("rows") or []) if cp.get("id")}
    print(f"  counterparties: {len(counterparties)}", flush=True)

    candidates: list[dict[str, Any]] = []
    for agent_id, agent_orders in by_agent.items():
        if len(agent_orders) < 3:
            continue
        cp = counterparties.get(agent_id)
        if not cp:
            continue
        row = counterparty_row_from_api(
            cp,
            order_channels=[
                _channel_name(o, channels_by_id) for o in agent_orders if _channel_name(o, channels_by_id)
            ],
        )
        attrs = _attr_map(cp)
        tg_nick = row.get("ТГ ник") or attrs.get("ТГ ник") or attrs.get("Telegram") or ""
        tg_conv = row.get("TG conversation") or attrs.get("TG conversation") or ""
        order_rows: list[dict[str, Any]] = []
        for o in sorted(
            agent_orders,
            key=lambda x: str(x.get("moment") or ""),
            reverse=True,
        ):
            ch = _channel_name(o, channels_by_id)
            desc = str(o.get("description") or "").strip()
            oname = str(o.get("name") or "").strip()
            order_rows.append(
                {
                    "id": str(o.get("id") or ""),
                    "name": oname,
                    "date": str(o.get("moment") or ""),
                    "sum": round(_minor_to_rub(o.get("sum")), 2),
                    "channel": ch,
                    "description": desc[:300],
                    "product_snippet": (desc or oname)[:120],
                    "line_items": [],
                }
            )

        entry = {
            "id": agent_id,
            "name": str(cp.get("name") or row.get("Наименование") or ""),
            "phone": str(cp.get("phone") or ""),
            "email": str(cp.get("email") or ""),
            "state": str(row.get("_moysklad_state") or ""),
            "company_type": str(row.get("Тип контрагента") or ""),
            "sex": str(row.get("Пол") or ""),
            "role": str(row.get("Заказчик или получатель") or ""),
            "tags": list(cp.get("tags") or []),
            "actual_address": str(cp.get("actualAddress") or ""),
            "tg_nick": str(tg_nick or ""),
            "tg_conversation": str(tg_conv or ""),
            "bonus_points": row.get("Баллы начисленные") or attrs.get("Баллы начисленные") or "",
            "attributes": attrs,
            "order_count": len(order_rows),
            "avg_check": round(
                (
                    sum(o["sum"] for o in order_rows if o["sum"] > 0)
                    / max(1, sum(1 for o in order_rows if o["sum"] > 0))
                ),
                2,
            )
            if any(o["sum"] > 0 for o in order_rows)
            else 0.0,
            "channels": sorted(
                {o["channel"] for o in order_rows if o.get("channel")}
            ),
            "orders": order_rows,
        }
        if _is_aggregate_stub(entry):
            continue
        candidates.append(entry)

    candidates.sort(key=_richness_score, reverse=True)
    selected = candidates[:limit]

    if fetch_positions:
        print(
            f"Fetching line items for top orders on {len(selected)} clients "
            f"(up to {positions_per_client} orders each)…",
            flush=True,
        )
        for entry in selected:
            for order in entry["orders"][:positions_per_client]:
                oid = order.get("id")
                if not oid:
                    continue
                lines = _position_snippets(client, oid)
                order["line_items"] = lines
                if lines and not order.get("product_snippet"):
                    order["product_snippet"] = "; ".join(lines)[:120]
                time.sleep(0.15)

    payload = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selection": {
            "limit": limit,
            "max_orders_scanned": max_orders,
            "counterparties_scanned": len(counterparties),
            "orders_scanned": len(orders),
            "candidates_ge_3_orders": len(candidates),
            "criteria": (
                "Prefer counterparties with ≥3 orders; rank by order_count, "
                "distinct channels, seasonal months (02/03/09/12/01), "
                "tags+attrs richness, contacts, line-item coverage."
            ),
            "pii_note": (
                "Contains shop-eval PII (phones/emails) for private iris_hermes eval. "
                "Do not commit API tokens. Re-export before publishing publicly."
            ),
        },
        "clients": selected,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(selected)} clients → {out_path}", flush=True)
    for i, c in enumerate(selected, 1):
        print(
            f"  {i:2d}. {c['name'][:40]:40s}  orders={c['order_count']:3d}  "
            f"channels={len(c['channels'])}  tags={len(c['tags'])}",
            flush=True,
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-orders", type=int, default=8000)
    parser.add_argument("--max-counterparties", type=int, default=0)
    parser.add_argument(
        "--no-positions",
        action="store_true",
        help="Skip fetching order line items (faster)",
    )
    parser.add_argument("--positions-per-client", type=int, default=3)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "plugins" / "moysklad" / "eval" / "golden_clients_v1.json",
    )
    args = parser.parse_args()
    _load_dotenv()
    select_and_export(
        limit=args.limit,
        max_orders=args.max_orders,
        max_counterparties=args.max_counterparties,
        fetch_positions=not args.no_positions,
        positions_per_client=args.positions_per_client,
        out_path=args.out,
    )


if __name__ == "__main__":
    main()
