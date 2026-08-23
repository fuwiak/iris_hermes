"""Backend for the «Карточки» sub-tabs (Sasha's six capability areas).

- ``search_ms_assortment`` — pick a bouquet from the MoySklad catalog
  (creation flow step 1; bundles are the bouquets, prices in kopecks).
- ``generate_card_draft`` — LLM writes per-marketplace descriptions adapted
  to each platform's requirements (creation flow step 3). Publishing via
  products/create / offer-mappings is the next stage — drafts only for now.
- ``recent_yandex_orders`` — live orders + statuses from the Yandex cabinet
  (orders → MS visibility; Flowwow's open API has no orders).
"""

from __future__ import annotations

from typing import Any

DRAFT_LLM_MAX_TOKENS = 900
DRAFT_LLM_TIMEOUT = 60.0

_DRAFT_SYSTEM = """Ты пишешь карточки товаров для цветочной студии «Вереск».
Дано название букета из МоегоСклада и цена. Верни ДВА описания, каждое
адаптировано под площадку:

[FLOWWOW]
Тёплый живой тон, 2–3 коротких абзаца: повод, состав/впечатление, уход и
доставка (аквабокс, инструкция). Без HTML.

[YANDEX]
Структура для контент-рейтинга Яндекс Маркета: первый абзац — что это и
повод; затем маркированный список преимуществ (свежесть, упаковка,
доставка); в конце строка «Состав и размер могут незначительно отличаться».
Без HTML.

Пиши только по данным из запроса, ничего не выдумывай про состав, если он
не передан. Ровно эти две секции с метками [FLOWWOW] и [YANDEX].
"""


def search_ms_assortment(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """MoySklad assortment search → slim rows (price already in rubles)."""
    from plugins.moysklad.client import MoySkladClient

    query = (query or "").strip()
    if not query:
        return []
    page = MoySkladClient()._request(
        "GET",
        "/entity/assortment",
        params={"search": query, "limit": max(1, min(50, limit))},
    )
    out: list[dict[str, Any]] = []
    for row in page.get("rows") or []:
        prices = row.get("salePrices") or []
        raw_price = float((prices[0] or {}).get("value") or 0) if prices else 0.0
        out.append(
            {
                "id": str(row.get("id") or ""),
                "type": str((row.get("meta") or {}).get("type") or ""),
                "name": str(row.get("name") or ""),
                "price": round(raw_price / 100.0, 2),
                "archived": bool(row.get("archived")),
            }
        )
    return out


def generate_card_draft(
    *,
    name: str,
    price: float | None = None,
    composition: str = "",
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Per-marketplace descriptions for one MS bouquet. ``{ok, drafts}``."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    user = f"Букет: «{name}»."
    if price:
        user += f" Цена: {price:.0f} руб."
    if composition.strip():
        user += f" Состав: {composition.strip()}."

    from agent.auxiliary_client import call_llm, extract_content_or_reasoning

    kwargs: dict[str, Any] = {
        "task": "moysklad_outreach",
        "messages": [
            {"role": "system", "content": _DRAFT_SYSTEM},
            {"role": "user", "content": user},
        ],
        "max_tokens": DRAFT_LLM_MAX_TOKENS,
        "temperature": 0.6,
        "timeout": DRAFT_LLM_TIMEOUT,
        "reasoning_config": {"enabled": False, "effort": "none"},
        "extra_body": {"reasoning": {"enabled": False}},
    }
    if (provider or "").strip():
        kwargs["provider"] = provider.strip()
    if (model or "").strip():
        kwargs["model"] = model.strip()
    text = (extract_content_or_reasoning(call_llm(**kwargs)) or "").strip()
    if not text:
        return {"ok": False, "error": "empty_llm_response"}
    drafts = _split_draft_sections(text)
    return {"ok": True, "name": name, "price": price, "drafts": drafts, "raw": text}


def _split_draft_sections(text: str) -> dict[str, str]:
    """[FLOWWOW]/[YANDEX] sections → dict; whole text as fallback for both."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        marker = line.strip().upper()
        if marker in ("[FLOWWOW]", "[YANDEX]"):
            if current and buffer:
                sections[current] = "\n".join(buffer).strip()
            current = "flowwow" if marker == "[FLOWWOW]" else "yandex_market"
            buffer = []
        elif current:
            buffer.append(line)
    if current and buffer:
        sections[current] = "\n".join(buffer).strip()
    if not sections:
        sections = {"flowwow": text, "yandex_market": text}
    return sections


def recent_yandex_orders(*, limit: int = 25) -> dict[str, Any]:
    """Fresh orders + statuses from every Yandex campaign, newest first."""
    from plugins.moysklad.yandex_market import YandexMarketClient, token_configured

    if not token_configured():
        return {"configured": False, "orders": []}
    client = YandexMarketClient()
    orders: list[dict[str, Any]] = []
    for campaign in client.campaigns():
        campaign_id = campaign.get("id")
        if not campaign_id:
            continue
        payload = client._request(
            "GET", f"/campaigns/{campaign_id}/orders", params={"limit": 20}
        )
        for order in payload.get("orders") or []:
            orders.append(
                {
                    "id": order.get("id"),
                    "campaign": campaign.get("domain") or campaign_id,
                    "status": order.get("status"),
                    "substatus": order.get("substatus"),
                    "created": order.get("creationDate"),
                    "buyer_total": order.get("buyerTotal"),
                    "items": [
                        str((item or {}).get("offerName") or "")[:80]
                        for item in (order.get("items") or [])[:4]
                    ],
                }
            )
    def _created_key(row: dict[str, Any]) -> str:
        raw = str(row.get("created") or "")
        # API format DD-MM-YYYY HH:MM:SS → sortable YYYYMMDDHHMMSS
        if len(raw) >= 10 and raw[2] == "-" and raw[5] == "-":
            return raw[6:10] + raw[3:5] + raw[0:2] + raw[10:]
        return raw
    orders.sort(key=_created_key, reverse=True)
    return {"configured": True, "orders": orders[: max(1, limit)]}
