"""Навык генерации сообщений — operator-approved example messages.

When the operator polishes a draft in the chat and sends it, the final text
can be saved here. Saved examples are injected into the outreach system
prompt as few-shot style anchors, so every next draft starts closer to what
the operator actually sends.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MAX_SKILLS = 100
_PROMPT_EXAMPLES = 5
_PROMPT_EXAMPLE_CHARS = 400


def _store_path() -> Path:
    root = get_hermes_home() / "moysklad"
    root.mkdir(parents=True, exist_ok=True)
    return root / "outreach_skills.json"


def _load() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw.get("items") if isinstance(raw, dict) else None
    return [i for i in (items or []) if isinstance(i, dict)]


def _save(items: list[dict[str, Any]]) -> None:
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"items": items[-_MAX_SKILLS:]}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def list_skills() -> list[dict[str, Any]]:
    with _LOCK:
        return sorted(
            _load(), key=lambda i: float(i.get("created_at") or 0), reverse=True
        )


def save_skill(*, text: str, notes: str = "") -> dict[str, Any]:
    body = (text or "").strip()
    if not body:
        raise ValueError("text required")
    item = {
        "id": f"sk-{uuid.uuid4().hex[:10]}",
        "text": body[:2000],
        "notes": (notes or "").strip()[:500],
        "created_at": time.time(),
    }
    with _LOCK:
        items = _load()
        # Same text twice adds nothing to the prompt — refresh its timestamp.
        items = [i for i in items if str(i.get("text") or "").strip() != body]
        items.append(item)
        _save(items)
    return item


def delete_skill(skill_id: str) -> bool:
    sid = (skill_id or "").strip()
    if not sid:
        return False
    with _LOCK:
        items = _load()
        kept = [i for i in items if str(i.get("id") or "") != sid]
        if len(kept) == len(items):
            return False
        _save(kept)
    return True


def prompt_examples_block() -> str:
    """Few-shot block for the outreach system prompt ('' when no skills)."""
    items = list_skills()[:_PROMPT_EXAMPLES]
    if not items:
        return ""
    lines = ["Одобренные оператором примеры стиля (навык) — пиши в этом духе,"]
    lines.append("не копируй дословно, адаптируй под клиента:")
    for i, item in enumerate(items, 1):
        text = " ".join(str(item.get("text") or "").split())[:_PROMPT_EXAMPLE_CHARS]
        lines.append(f"{i}. «{text}»")
    return "\n".join(lines) + "\n"
