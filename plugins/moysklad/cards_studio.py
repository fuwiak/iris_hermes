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


# --- Concrete SEO fixes: description variants + photo shot plan / generation ---

_PHOTO_TARGET = 6  # Yandex seller guidance: 5–6 photos


def photo_shot_plan(
    name: str,
    *,
    images_count: int | None = None,
    min_photos: int = 3,
) -> list[dict[str, str]]:
    """Concrete missing shots for a card (marketplace photo guidance, not fluff)."""
    title = (name or "").strip() or "товар"
    lower = title.lower()
    is_card = any(w in lower for w in ("открытк", "postcard", "карт"))
    have = max(0, int(images_count or 0))
    need = max(min_photos, _PHOTO_TARGET) - have
    if need <= 0:
        need = 2  # still offer upgrade shots when rating is low

    if is_card:
        catalog = [
            {
                "id": "hero_front",
                "title": "Лицевая сторона (герой)",
                "why": "основное фото в поиске и карточке",
                "prompt": (
                    f"Product photo of greeting card «{title}», front cover centered, "
                    "soft daylight, white seamless background, sharp text readable, "
                    "ecommerce catalog style, no hands, no watermark"
                ),
            },
            {
                "id": "angle_45",
                "title": "Ракурс 45°",
                "why": "показать толщину/качество бумаги",
                "prompt": (
                    f"Greeting card «{title}» at 45 degree angle on white surface, "
                    "subtle shadow, studio lighting, ecommerce"
                ),
            },
            {
                "id": "open_inside",
                "title": "Разворот / внутренняя сторона",
                "why": "покупатель видит формат и место для подписи",
                "prompt": (
                    f"Open greeting card «{title}» showing inside blank/message area, "
                    "flat lay, soft light, white background"
                ),
            },
            {
                "id": "size_context",
                "title": "Размер в контексте",
                "why": "понятный масштаб (рука/конверт без лица)",
                "prompt": (
                    f"Greeting card «{title}» next to a standard envelope for scale, "
                    "top-down, clean desk, natural light"
                ),
            },
            {
                "id": "lifestyle",
                "title": "В интерьере / подарочная сцена",
                "why": "эмоция и повод покупки",
                "prompt": (
                    f"Lifestyle scene: greeting card «{title}» with flowers or gift wrap, "
                    "cozy table, shallow depth of field, warm daylight"
                ),
            },
            {
                "id": "detail",
                "title": "Крупный план печати",
                "why": "качество печати и фактура",
                "prompt": (
                    f"Macro detail of print on greeting card «{title}», paper texture, "
                    "sharp focus, studio light"
                ),
            },
        ]
    else:
        catalog = [
            {
                "id": "hero",
                "title": "Главное фото букета",
                "why": "основное фото в каталоге",
                "prompt": (
                    f"Professional product photo of flower bouquet «{title}», front view, "
                    "white seamless background, studio softbox, ecommerce, sharp petals"
                ),
            },
            {
                "id": "angle",
                "title": "Ракурс сбоку",
                "why": "объём и высота композиции",
                "prompt": (
                    f"Side angle product photo of bouquet «{title}», white background, "
                    "studio lighting, ecommerce"
                ),
            },
            {
                "id": "top",
                "title": "Вид сверху",
                "why": "состав и форма букета",
                "prompt": (
                    f"Top-down flat lay of bouquet «{title}» on white, even light, "
                    "catalog style"
                ),
            },
            {
                "id": "detail",
                "title": "Крупный план цветов",
                "why": "свежесть и качество",
                "prompt": (
                    f"Macro close-up of blooms in bouquet «{title}», soft bokeh, "
                    "natural color, no watermark"
                ),
            },
            {
                "id": "lifestyle",
                "title": "В интерьере",
                "why": "как выглядит у получателя",
                "prompt": (
                    f"Lifestyle: bouquet «{title}» in a vase on a home table, "
                    "daylight from window, cozy interior, shallow DOF"
                ),
            },
            {
                "id": "pack",
                "title": "Упаковка / аквабокс",
                "why": "доставка и сохранность",
                "prompt": (
                    f"Bouquet «{title}» in aquabox or gift wrap ready for delivery, "
                    "clean studio shot, white background"
                ),
            },
        ]

    # Prefer shots the seller likely still lacks (skip first `have` slots).
    remaining = catalog[have:] + catalog[:have]
    out: list[dict[str, str]] = []
    for shot in remaining:
        if len(out) >= max(need, 2):
            break
        out.append(dict(shot))
    return out


def _image_to_data_url(path_or_url: str) -> str:
    """UI-friendly image: pass through http(s)/data, else file → data URL."""
    raw = (path_or_url or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://", "data:")):
        return raw
    try:
        import base64
        from pathlib import Path

        path = Path(raw)
        if not path.is_file():
            return raw
        mime = "image/png"
        suffix = path.suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif suffix == ".webp":
            mime = "image/webp"
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return raw


def generate_card_photos(
    shots: list[dict[str, str]],
    *,
    max_images: int = 2,
    reference_image_url: str = "",
) -> dict[str, Any]:
    """Generate up to ``max_images`` shots via active image_gen provider."""
    try:
        from agent.image_gen_registry import get_active_provider
    except Exception as exc:
        return {"ok": False, "images": [], "error": f"registry_unavailable: {exc}"}

    provider = get_active_provider()
    if provider is None:
        return {
            "ok": False,
            "images": [],
            "error": "image_gen_not_configured",
            "hint": "Включите image_gen.provider в config.yaml (fal/openai/…)",
        }

    images: list[dict[str, Any]] = []
    errors: list[str] = []
    ref = (reference_image_url or "").strip() or None
    for shot in (shots or [])[: max(0, max_images)]:
        prompt = str(shot.get("prompt") or "").strip()
        if not prompt:
            continue
        try:
            result = provider.generate(
                prompt,
                aspect_ratio="1:1",
                image_url=ref,
            )
        except Exception as exc:
            errors.append(f"{shot.get('id')}: {exc}")
            continue
        if not isinstance(result, dict) or not result.get("success"):
            errors.append(
                f"{shot.get('id')}: {(result or {}).get('error') or 'generate_failed'}"
            )
            continue
        image = _image_to_data_url(str(result.get("image") or ""))
        if not image:
            errors.append(f"{shot.get('id')}: empty_image")
            continue
        images.append(
            {
                "id": shot.get("id"),
                "title": shot.get("title"),
                "why": shot.get("why"),
                "prompt": prompt,
                "image": image,
                "provider": result.get("provider") or provider.name,
            }
        )
    return {
        "ok": bool(images),
        "images": images,
        "error": "; ".join(errors) if errors and not images else (errors[0] if errors else ""),
        "provider": provider.name,
    }


def improve_card_content(
    *,
    name: str,
    price: float | None = None,
    composition: str = "",
    images_count: int | None = None,
    content_rating: int | None = None,
    marketplace: str = "",
    image_url: str = "",
    generate_images: bool = True,
    max_images: int = 2,
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Concrete SEO fix pack: description variants + photo shots (+ optional gen)."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}

    draft = generate_card_draft(
        name=name,
        price=price,
        composition=composition,
        provider=provider,
        model=model,
    )
    shots = photo_shot_plan(name, images_count=images_count)
    photo_gen: dict[str, Any] = {"ok": False, "images": [], "skipped": True}
    if generate_images and max_images > 0:
        photo_gen = generate_card_photos(
            shots,
            max_images=max_images,
            reference_image_url=image_url,
        )
        photo_gen["skipped"] = False

    mp = (marketplace or "").strip().lower()
    preferred_draft = ""
    drafts = draft.get("drafts") if draft.get("ok") else {}
    if isinstance(drafts, dict):
        if mp in ("yandex", "yandex_market"):
            preferred_draft = str(drafts.get("yandex_market") or drafts.get("yandex") or "")
        elif mp == "flowwow":
            preferred_draft = str(drafts.get("flowwow") or "")
        if not preferred_draft:
            preferred_draft = str(
                drafts.get("yandex_market") or drafts.get("flowwow") or ""
            )

    return {
        "ok": True,
        "name": name,
        "price": price,
        "images_count": images_count,
        "content_rating": content_rating,
        "marketplace": marketplace,
        "description": {
            "ok": bool(draft.get("ok")),
            "error": draft.get("error") or "",
            "drafts": drafts or {},
            "preferred": preferred_draft,
            "hint": "Скопируйте текст в кабинет площадки (описание / характеристики).",
        },
        "photos": {
            "target_count": _PHOTO_TARGET,
            "have": int(images_count or 0),
            "shots": shots,
            "generated": photo_gen.get("images") or [],
            "generate_ok": bool(photo_gen.get("ok")),
            "generate_error": photo_gen.get("error") or "",
            "generate_hint": photo_gen.get("hint") or "",
            "skipped": bool(photo_gen.get("skipped")),
        },
    }