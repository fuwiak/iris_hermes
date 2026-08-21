"""Optional Elasticsearch backend for MoySklad durable cache.

Catalog JSON must NOT be mapped as nested objects (10k counterparties would
explode the field cap). Documents store a non-indexed ``payload`` blob.

Configured via ``ELASTICSEARCH_URL`` or ``MOYSKLAD_ELASTICSEARCH_URL``.
When unset, callers skip this layer. No extra PyPI package — stdlib urllib.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger(__name__)

INDEX = "moysklad-cache"
_LOCK = threading.Lock()
_READY: bool | None = None
_READY_AT = 0.0
_INDEXED = False
_PING_TTL = 30.0


def elasticsearch_url() -> str:
    return (
        os.environ.get("ELASTICSEARCH_URL")
        or os.environ.get("MOYSKLAD_ELASTICSEARCH_URL")
        or ""
    ).strip().rstrip("/")


def enabled() -> bool:
    return bool(elasticsearch_url())


def doc_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _auth_opener(base: str) -> urllib.request.OpenerDirector:
    parsed = urllib.request.urlparse(base)
    if parsed.username or parsed.password:
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        password_mgr.add_password(None, f"{parsed.scheme}://{host}", parsed.username or "", parsed.password or "")
        return urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(password_mgr))
    return urllib.request.build_opener()


def _sanitize_base(base: str) -> str:
    parsed = urllib.request.urlparse(base)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.request.urlunparse((parsed.scheme, netloc, "", "", "", "")).rstrip("/")


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 3.0,
) -> tuple[int, dict[str, Any] | None]:
    base = elasticsearch_url()
    if not base:
        return 0, None
    clean = _sanitize_base(base)
    url = f"{clean}{path}"
    data = None if body is None else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    opener = _auth_opener(base)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return int(resp.status), json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {"error": raw[:240]}
        return int(exc.code), parsed
    except Exception as exc:
        log.warning("MoySklad Elasticsearch %s %s failed: %s", method, path, exc)
        return 0, None


def ready() -> bool:
    """Cached ping so dashboard paints do not wait on a dead cluster."""
    global _READY, _READY_AT
    if not enabled():
        return False
    now = time.time()
    with _LOCK:
        if _READY is not None and (now - _READY_AT) < _PING_TTL:
            return _READY
    status, _ = _request("GET", "/", timeout=1.5)
    ok = status == 200
    with _LOCK:
        _READY = ok
        _READY_AT = now
    return ok


def _ensure_index() -> bool:
    global _INDEXED
    if _INDEXED:
        return True
    status, _ = _request("HEAD", f"/{INDEX}", timeout=2.0)
    if status == 200:
        _INDEXED = True
        return True
    status, _ = _request(
        "PUT",
        f"/{INDEX}",
        {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "dynamic": False,
                "properties": {
                    "key": {"type": "keyword"},
                    "synced_at": {"type": "double"},
                    "kind": {"type": "keyword"},
                    "payload": {"type": "object", "enabled": False},
                },
            },
        },
        timeout=5.0,
    )
    if status in (200, 201):
        _INDEXED = True
        return True
    log.warning("MoySklad Elasticsearch index create failed status=%s", status)
    return False


def es_get(key: str) -> Optional[dict[str, Any]]:
    if not enabled() or not ready():
        return None
    status, body = _request("GET", f"/{INDEX}/_doc/{doc_id(key)}", timeout=3.0)
    if status != 200 or not isinstance(body, dict):
        return None
    src = body.get("_source")
    if not isinstance(src, dict):
        return None
    payload = src.get("payload")
    return payload if isinstance(payload, dict) else None


def es_put(key: str, envelope: dict[str, Any], *, kind: str = "catalog") -> bool:
    if not enabled() or not ready():
        return False
    if not _ensure_index():
        return False
    status, _ = _request(
        "PUT",
        f"/{INDEX}/_doc/{doc_id(key)}?refresh=false",
        {
            "key": key,
            "kind": kind,
            "synced_at": float(envelope.get("synced_at") or 0),
            "payload": envelope,
        },
        timeout=15.0,
    )
    if status not in (200, 201):
        log.warning("MoySklad Elasticsearch put failed status=%s key=%s", status, key[-24:])
        return False
    return True


def es_delete(key: str) -> None:
    if not enabled():
        return
    _request("DELETE", f"/{INDEX}/_doc/{doc_id(key)}", timeout=3.0)


def reset_for_tests() -> None:
    global _READY, _READY_AT, _INDEXED
    _READY = None
    _READY_AT = 0.0
    _INDEXED = False
