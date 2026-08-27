"""Always-on photo-send breadcrumb log.

Sellers kept getting «текст ушёл, фото нет» with no trail. Every hop on the
tray → Telegram path writes one JSON line to
``$HERMES_HOME/moysklad/photo_send.log`` *and* the process logger. Base64 and
raw bytes are never stored — only lengths and URL prefixes.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("moysklad.photo")

_MAX_LOG_BYTES = 2 * 1024 * 1024
_TAIL_KEEP = 256_000


def log_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "moysklad" / "photo_send.log"


def _safe(value: Any) -> Any:
    """Strip secrets / huge payloads so the log stays readable."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"_bytes": len(value)}
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("data:"):
            return f"data:[len={len(raw)}]"
        if len(raw) > 180:
            return raw[:120] + f"…[+{len(raw) - 120}]"
        return raw
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value[:20]]
    return str(value)[:180]


def photo_trace(event: str, **fields: Any) -> None:
    """Append one JSON line. Never raises — logging must not break a send."""
    row: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": str(event or "unknown"),
    }
    for key, value in fields.items():
        row[str(key)] = _safe(value)
    line = json.dumps(row, ensure_ascii=False, default=str)
    log.info("ms-photo %s", line)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
            tail = path.read_bytes()[-_TAIL_KEEP:]
            path.write_bytes(tail)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        log.debug("photo_trace file write failed", exc_info=True)


def photo_trace_tail(n: int = 80) -> dict[str, Any]:
    """Last ``n`` JSON lines for the UI / debugging endpoint."""
    path = log_path()
    lines: list[str] = []
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = [ln for ln in text.splitlines() if ln.strip()][-max(1, min(int(n or 80), 400)) :]
    except Exception as exc:
        return {"ok": False, "path": str(path), "detail": str(exc), "lines": []}
    parsed: list[Any] = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except Exception:
            parsed.append({"raw": ln[:400]})
    return {"ok": True, "path": str(path), "count": len(parsed), "lines": parsed}


def summarize_attachments(attachments: list[Any] | None) -> list[dict[str, Any]]:
    """Compact tray snapshot: has_bytes / url prefix / name. No payload."""
    out: list[dict[str, Any]] = []
    for item in list(attachments or [])[:20]:
        if isinstance(item, dict):
            b64 = str(item.get("image_base64") or item.get("base64") or "")
            url = str(item.get("image_url") or item.get("url") or "")
            name = str(item.get("image_name") or item.get("name") or "")
        else:
            b64 = str(getattr(item, "image_base64", "") or "")
            url = str(getattr(item, "image_url", "") or "")
            name = str(getattr(item, "image_name", "") or "")
        out.append(
            {
                "name": name[:80],
                "has_bytes": bool(b64.strip()),
                "b64_len": len(b64.strip()),
                "url": _safe(url),
            }
        )
    return out
