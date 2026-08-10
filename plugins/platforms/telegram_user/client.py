"""Telegram personal account (MTProto / Telethon) — contacts + outbound send.

Why this exists: the Bot API cannot list your Telegram contacts and cannot
message a person who never wrote to the bot first. A *user* session can do
both, so Рассылки connects the operator's own account (phone → code → 2FA)
and sends from it.

State lives in ``<hermes home>/telegram_user/``:

* ``config.json`` (0600) — ``api_id`` / ``api_hash`` / StringSession / last me
* ``contacts.json`` — cached contact list so the pickers work offline

Env overrides (first non-empty wins over the stored config):
``TELEGRAM_API_ID``, ``TELEGRAM_API_HASH``, ``TELEGRAM_USER_SESSION``.
When neither env nor config has app credentials, built-in public keys are
used so the operator only ever types phone → code → 2FA (opt out with
``TELEGRAM_BUILTIN_API=0``).

Telethon is lazy-installed on first use via ``tools.lazy_deps`` feature
``platform.telegram_user``. Every public helper returns ``{"ok": bool, ...}``
and never raises for Telegram-side errors — same contract as the Business
client next door.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from hermes_constants import get_hermes_home

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_CONTACTS_TTL = 900.0  # seconds — refresh window for the cached contact list
_DIALOGS_LIMIT = 500  # private chats scanned on top of the saved address book
_DEFAULT_TIMEOUT = 60.0
# Login must fail fast: Selectel RU DCs often cannot open MTProto at all, and
# Telethon's default 5× retries make the UI look hung for a full minute.
_LOGIN_TIMEOUT = 22.0
_CONNECT_TIMEOUT = 8.0
_CONNECT_RETRIES = 2

_PEER_ID_RE = re.compile(r"^-?\d{1,20}$")
_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})",
    re.IGNORECASE,
)
_PHONE_DIGITS_RE = re.compile(r"\D+")

_NETWORK_HINT = (
    "Сервер не достучался до Telegram MTProto (типично для Selectel/RU IP). "
    "Задайте TELEGRAM_USER_GATEWAY_URL (Railway egress) или TELEGRAM_PROXY=socks5://…"
)


def _gateway_base() -> str:
    """HTTPS base for Railway Telethon egress, e.g. ``https://host/t/<token>``."""
    return (os.getenv("TELEGRAM_USER_GATEWAY_URL") or "").strip().rstrip("/")


def _gateway_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    base = _gateway_base()
    if not base:
        return _err("gateway_missing", "TELEGRAM_USER_GATEWAY_URL не задан")
    url = f"{base}/{path.lstrip('/')}"
    if params:
        from urllib.parse import urlencode  # noqa: PLC0415

        url = f"{url}?{urlencode({k: str(v) for k, v in params.items()})}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = (os.getenv("TELEGRAM_USER_GATEWAY_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body_bytes = None
    if json_body is not None:
        body_bytes = json.dumps(json_body).encode("utf-8")

    # Prefer httpx when present; fall back to stdlib (Selectel image may omit httpx).
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method.upper(), url, content=body_bytes, headers=headers)
        status = resp.status_code
        text = resp.text
    except ImportError:
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                status = getattr(resp, "status", 200) or 200
                text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            text = exc.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return _err("gateway_unreachable", f"Telegram gateway недоступен: {exc}")
    except Exception as exc:
        return _err("gateway_unreachable", f"Telegram gateway недоступен: {exc}")

    try:
        data = json.loads(text) if text else {}
    except Exception:
        data = {"ok": False, "error": "bad_gateway_response", "detail": text[:300]}
    if not isinstance(data, dict):
        return _err("bad_gateway_response", "gateway returned non-object JSON")
    if status >= 400 and data.get("ok") is not False:
        detail = data.get("detail") or data.get("error") or text[:300]
        return _err("gateway_http_error", str(detail), status_code=status)
    data.setdefault("via", "gateway")
    return data


def gateway_configured() -> bool:
    return bool(_gateway_base())



# ── storage ───────────────────────────────────────────────────────────────


def _home() -> Path:
    root = get_hermes_home() / "telegram_user"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _config_path() -> Path:
    return _home() / "config.json"


def _contacts_path() -> Path:
    return _home() / "contacts.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, payload: dict[str, Any], *, secret: bool = False) -> None:
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        if secret:
            try:
                path.chmod(0o600)
            except OSError:  # pragma: no cover - exotic filesystems
                log.debug("could not chmod %s", path)


def load_config() -> dict[str, Any]:
    return _read_json(_config_path())


def _save_config(cfg: dict[str, Any]) -> None:
    _write_json(_config_path(), cfg, secret=True)


# Telegram Desktop's public open-source app credentials (shipped in the
# tdesktop repo). They identify the *client application*, not the account —
# authorization is still the user's own phone → code → 2FA. With this
# fallback nobody has to visit my.telegram.org; own keys via
# TELEGRAM_API_* / config always win. Disable with TELEGRAM_BUILTIN_API=0.
_BUILTIN_API_ID = "2040"
_BUILTIN_API_HASH = "b18441a1ff607e10a989891a5462e627"


def builtin_api_enabled() -> bool:
    flag = (os.getenv("TELEGRAM_BUILTIN_API") or "").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def own_api_credentials() -> tuple[str, str]:
    """``(api_id, api_hash)`` from env / stored config only — no builtin."""
    cfg = load_config()
    api_id = (os.getenv("TELEGRAM_API_ID") or "").strip() or str(
        cfg.get("api_id") or ""
    ).strip()
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip() or str(
        cfg.get("api_hash") or ""
    ).strip()
    return api_id, api_hash


def api_credentials() -> tuple[str, str]:
    """Effective ``(api_id, api_hash)``: env → config → built-in fallback."""
    api_id, api_hash = own_api_credentials()
    if api_id and api_hash:
        return api_id, api_hash
    # A half-filled pair is useless to Telethon — fall back as a whole.
    if builtin_api_enabled():
        return _BUILTIN_API_ID, _BUILTIN_API_HASH
    return api_id, api_hash


def session_string() -> str:
    return (os.getenv("TELEGRAM_USER_SESSION") or "").strip() or str(
        load_config().get("session") or ""
    ).strip()


def normalize_login_phone(value: str) -> str:
    """Normalize to ``+<digits>`` for Telethon ``send_code_request``."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    digits = _PHONE_DIGITS_RE.sub("", raw)
    if not digits:
        return ""
    # RU national trunk ``8XXXXXXXXXX`` → ``7XXXXXXXXXX``.
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits


def _proxy_url() -> str:
    try:
        from gateway.platforms.base import resolve_proxy_url  # noqa: PLC0415
    except Exception:
        return (os.getenv("TELEGRAM_PROXY") or "").strip()
    return (
        resolve_proxy_url(
            "TELEGRAM_PROXY",
            target_hosts=["149.154.167.50", "149.154.167.91", "10.0.0.1"],
        )
        or ""
    ).strip()


def telethon_proxy_arg(url: str | None = None) -> Any | None:
    """Build Telethon ``proxy=`` value from a URL, or None."""
    raw = (url if url is not None else _proxy_url()).strip()
    if not raw:
        return None
    from urllib.parse import unquote, urlparse  # noqa: PLC0415

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    port = parsed.port
    if not host or not port:
        return None
    user = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    if scheme in ("socks5", "socks5h", "socks"):
        ptype = "socks5"
    elif scheme in ("socks4", "socks4a"):
        ptype = "socks4"
    elif scheme in ("http", "https"):
        ptype = "http"
    else:
        return None
    # (type, addr, port, rdns, username, password) — Telethon / python_socks.
    return (ptype, host, int(port), True, user, password)


def sanitize_api_id(value: str) -> str:
    """Keep only a real my.telegram.org api_id. Masks / typos → empty."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    # UI may echo masked previews (••••) or leftover junk like "admin".
    if any(ch in raw for ch in ("•", "*", "…")):
        return ""
    return raw if raw.isdigit() else ""


def sanitize_api_hash(value: str) -> str:
    """Keep a plausible api_hash; drop masks / empty / placeholders."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if any(ch in raw for ch in ("•", "*", "…")):
        return ""
    if raw.lower() in {"admin", "password", "hash", "api_hash"}:
        return ""
    return raw


def save_credentials(
    *,
    api_id: str = "",
    api_hash: str = "",
    strict: bool = False,
) -> dict[str, Any]:
    """Persist my.telegram.org app credentials. Empty values keep the old ones.

    ``strict=True`` (credentials endpoint): reject non-numeric api_id.
    ``strict=False`` (login): silently ignore junk so leftover UI state like
    ``admin`` / masked ``29••••`` does not block Telethon auth when .env
    already has real keys.
    """
    raw_id = str(api_id or "").strip()
    cleaned_id = sanitize_api_id(raw_id)
    cleaned_hash = sanitize_api_hash(api_hash)
    if strict and raw_id and not cleaned_id:
        return {
            "ok": False,
            "error": "api_id_invalid",
            "detail": "api_id — целое число из my.telegram.org",
        }
    with _LOCK:
        cfg = load_config()
        if cleaned_id:
            cfg["api_id"] = cleaned_id
        if cleaned_hash:
            cfg["api_hash"] = cleaned_hash
        _save_config(cfg)
    _RUNNER.reset()
    return {"ok": True, "configured": bool(all(api_credentials()))}


# ── telethon loading ──────────────────────────────────────────────────────


def _import_telethon(*, install: bool = True) -> tuple[Any, str]:
    """Import telethon, lazy-installing once. Returns ``(module, error)``."""
    try:
        import telethon  # noqa: PLC0415

        return telethon, ""
    except ImportError:
        pass
    if not install:
        return None, "telethon_missing"
    try:
        from tools.lazy_deps import ensure as _lazy_ensure  # noqa: PLC0415

        _lazy_ensure("platform.telegram_user", prompt=False)
    except Exception as exc:
        log.debug("telethon lazy install failed: %s", exc)
        return None, str(exc)
    try:
        import telethon  # noqa: PLC0415

        return telethon, ""
    except ImportError as exc:
        return None, str(exc)


def telethon_available() -> bool:
    mod, _ = _import_telethon(install=False)
    return mod is not None


def ensure_runtime() -> dict[str, Any]:
    """Install Telethon on demand so the UI can fix a cold environment."""
    if _gateway_base():
        return {"ok": True, "available": True, "via": "gateway"}
    mod, err = _import_telethon(install=True)
    if mod is None:
        return _err(
            "telethon_missing",
            "Не удалось установить telethon: "
            f"{err or 'unknown error'}. Поставьте вручную: "
            "`uv pip install telethon==1.44.0`",
        )
    return {"ok": True, "available": True, "version": getattr(mod, "__version__", "")}


# ── background event loop ─────────────────────────────────────────────────


class _Runner:
    """Owns one asyncio loop + one connected Telethon client on a daemon thread.

    The login flow spans several HTTP requests (phone → code → password) and
    Telegram ties ``phone_code_hash`` to the connection, so the client has to
    outlive a single request.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._lock = threading.RLock()
        self.phone = ""
        self.phone_code_hash = ""

    # -- loop plumbing --

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop,
                args=(loop,),
                name="telegram-user-loop",
                daemon=True,
            )
            thread.start()
            self._loop = loop
            self._thread = thread
            return loop

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def submit(
        self,
        factory: Callable[[], Coroutine[Any, Any, Any]],
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Any:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(factory(), loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    def reset(self) -> None:
        """Drop the cached client (credentials or session changed)."""
        with self._lock:
            client = self._client
            self._client = None
            self.phone = ""
            self.phone_code_hash = ""
        if client is None:
            return
        try:
            self.submit(lambda: _disconnect(client), timeout=8.0)
        except Exception:
            log.debug("telegram_user disconnect failed", exc_info=True)

    # -- client --

    async def client(self) -> Any:
        """Connected (not necessarily authorized) TelegramClient."""
        with self._lock:
            client = self._client
        if client is not None:
            if not client.is_connected():
                await client.connect()
            return client

        telethon, err = _import_telethon()
        if telethon is None:
            raise RuntimeError(err or "telethon_missing")
        from telethon import TelegramClient  # noqa: PLC0415
        from telethon.sessions import StringSession  # noqa: PLC0415

        api_id, api_hash = api_credentials()
        if not api_id or not api_hash:
            raise RuntimeError("api_credentials_missing")

        proxy = telethon_proxy_arg()
        if proxy is not None:
            # Ensure python-socks is present for SOCKS proxies.
            try:
                import python_socks  # noqa: F401,PLC0415
            except ImportError:
                _import_telethon(install=True)

        client = TelegramClient(
            StringSession(session_string() or None),
            int(api_id),
            api_hash,
            device_model="Hermes CRM",
            app_version="1.0",
            proxy=proxy,
            connection_retries=_CONNECT_RETRIES,
            retry_delay=1,
            timeout=_CONNECT_TIMEOUT,
            auto_reconnect=False,
        )
        await client.connect()
        with self._lock:
            self._client = client
        return client

    def persist_session(self, client: Any) -> None:
        try:
            session = client.session.save()
        except Exception:  # pragma: no cover - telethon internals
            log.debug("session save failed", exc_info=True)
            return
        with _LOCK:
            cfg = load_config()
            cfg["session"] = session
            if self.phone:
                cfg["phone"] = self.phone
            _save_config(cfg)


async def _disconnect(client: Any) -> None:
    try:
        if client.is_connected():
            await client.disconnect()
    except Exception:  # pragma: no cover - best effort
        log.debug("disconnect failed", exc_info=True)


_RUNNER = _Runner()


def _err(error: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "detail": detail, **extra}


def _runtime_error(exc: Exception) -> dict[str, Any]:
    msg = str(exc)
    if msg == "telethon_missing":
        return _err(
            "telethon_missing",
            "Не установлен telethon — `uv pip install telethon` "
            "или hermes-agent[telegram-user]",
        )
    if msg == "api_credentials_missing":
        return _err(
            "api_credentials_missing",
            "Нужны api_id / api_hash с my.telegram.org → API development tools "
            "(встроенные ключи выключены через TELEGRAM_BUILTIN_API=0)",
        )
    return _err("telegram_user_error", msg or exc.__class__.__name__)


def _call(
    factory: Callable[[], Coroutine[Any, Any, Any]],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    try:
        return _RUNNER.submit(factory, timeout=timeout)
    except RuntimeError as exc:
        return _runtime_error(exc)
    except TimeoutError:
        try:
            _RUNNER.reset()
        except Exception:
            log.debug("telegram_user reset after timeout failed", exc_info=True)
        return _err(
            "timeout",
            f"Telegram не ответил за {timeout:.0f}s. {_NETWORK_HINT}",
            network_blocked=True,
            proxy_configured=bool(_proxy_url()),
        )
    except Exception as exc:  # pragma: no cover - network / telethon errors
        log.warning("telegram_user call failed: %s", exc)
        msg = str(exc) or exc.__class__.__name__
        low = msg.lower()
        if "timed out" in low or "timeout" in low or "connection" in low:
            try:
                _RUNNER.reset()
            except Exception:
                pass
            return _err(
                "network_unreachable",
                f"{msg}. {_NETWORK_HINT}",
                network_blocked=True,
                proxy_configured=bool(_proxy_url()),
            )
        return _err(exc.__class__.__name__, msg)


def _me_dict(me: Any) -> dict[str, Any]:
    if me is None:
        return {}
    first = str(getattr(me, "first_name", "") or "").strip()
    last = str(getattr(me, "last_name", "") or "").strip()
    return {
        "id": getattr(me, "id", None),
        "username": getattr(me, "username", None),
        "name": " ".join(p for p in (first, last) if p),
        "phone": getattr(me, "phone", None),
    }


# ── status / login ────────────────────────────────────────────────────────


def mask_secret(value: str, *, keep: int = 2) -> str:
    """UI-safe mask — keep a tiny prefix, rest as bullets. Never return raw secret."""
    raw = str(value or "")
    if not raw:
        return ""
    keep_n = max(0, min(int(keep), len(raw)))
    rest = len(raw) - keep_n
    if rest <= 0:
        # Entire value fits in the keep window — still obscure it.
        return "•" * len(raw)
    bullets = "•" * min(16, max(4, rest))
    return raw[:keep_n] + bullets


def user_status(*, probe: bool = True) -> dict[str, Any]:
    """UI-facing account block. No secrets — only presence flags + masked ids."""
    if _gateway_base():
        out = _gateway_request(
            "GET",
            "status",
            params={"probe": "true" if probe else "false"},
            timeout=45.0,
        )
        out["gateway_configured"] = True
        out["available"] = True
        if out.get("ok") and out.get("phone"):
            with _LOCK:
                cfg = load_config()
                cfg["phone"] = out["phone"]
                if out.get("user"):
                    cfg["user"] = out["user"]
                _save_config(cfg)
        return out

    api_id, api_hash = api_credentials()
    cfg = load_config()
    env_api = bool(
        (os.getenv("TELEGRAM_API_ID") or "").strip()
        and (os.getenv("TELEGRAM_API_HASH") or "").strip()
    )
    own_id, own_hash = own_api_credentials()
    if env_api:
        api_source = "env"
    elif own_id and own_hash:
        api_source = "config"
    elif api_id and api_hash:
        api_source = "builtin"
    else:
        api_source = ""
    out: dict[str, Any] = {
        "ok": True,
        "available": telethon_available() or bool(_gateway_base()),
        "api_configured": bool(api_id and api_hash),
        "api_source": api_source,
        # Masked previews for the Connect form — never the raw api_hash.
        # Builtin fallback keys are not the operator's, so nothing to preview.
        "api_id_masked": mask_secret(own_id, keep=2) if own_id else "",
        "api_hash_masked": mask_secret(own_hash, keep=0) if own_hash else "",
        "session_saved": bool(session_string()),
        "phone": str(cfg.get("phone") or "") or None,
        "authorized": False,
        "user": cfg.get("user") or None,
        "contacts_cached": len(cached_contacts()),
        "contacts_fetched_at": _read_json(_contacts_path()).get("fetched_at"),
        "proxy_configured": bool(_proxy_url()),
        "gateway_configured": False,
    }
    if not probe or not out["api_configured"] or not out["session_saved"]:
        return out

    async def _probe() -> dict[str, Any]:
        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return {"ok": True, "authorized": False}
        me = await client.get_me()
        return {"ok": True, "authorized": True, "user": _me_dict(me)}

    res = _call(_probe, timeout=30.0)
    if not res.get("ok"):
        out["detail"] = res.get("detail")
        out["error"] = res.get("error")
        return out
    out["authorized"] = bool(res.get("authorized"))
    if res.get("user"):
        out["user"] = res["user"]
        with _LOCK:
            cfg = load_config()
            cfg["user"] = res["user"]
            _save_config(cfg)
    return out


_AUTH_CACHE: dict[str, float] = {"checked_at": 0.0, "authorized": 0.0}
_AUTH_TTL = 60.0


def is_authorized(*, ttl: float = _AUTH_TTL) -> bool:
    """Is the personal account usable? Cached so batch sends don't re-probe."""
    now = time.time()
    with _LOCK:
        if now - _AUTH_CACHE["checked_at"] < ttl:
            return bool(_AUTH_CACHE["authorized"])

    if _gateway_base():
        status = _gateway_request(
            "GET",
            "status",
            params={"probe": "true"},
            timeout=30.0,
        )
        authorized = bool(status.get("ok") and status.get("authorized"))
        with _LOCK:
            _AUTH_CACHE["checked_at"] = time.time()
            _AUTH_CACHE["authorized"] = 1.0 if authorized else 0.0
        return authorized

    api_id, api_hash = api_credentials()
    if not api_id or not api_hash or not session_string():
        return False

    async def _check() -> dict[str, Any]:
        client = await _RUNNER.client()
        return {"ok": True, "authorized": bool(await client.is_user_authorized())}

    res = _call(_check, timeout=20.0)
    authorized = bool(res.get("ok") and res.get("authorized"))
    with _LOCK:
        _AUTH_CACHE["checked_at"] = time.time()
        _AUTH_CACHE["authorized"] = 1.0 if authorized else 0.0
    return authorized


def _invalidate_auth_cache() -> None:
    with _LOCK:
        _AUTH_CACHE["checked_at"] = 0.0
        _AUTH_CACHE["authorized"] = 0.0


def start_login(
    *,
    phone: str,
    api_id: str = "",
    api_hash: str = "",
) -> dict[str, Any]:
    """Send the Telegram login code to ``phone`` (Telethon MTProto)."""
    phone = normalize_login_phone(phone)
    if _gateway_base():
        res = _gateway_request(
            "POST",
            "login",
            json_body={"phone": phone, "api_id": api_id, "api_hash": api_hash},
            timeout=_LOGIN_TIMEOUT + 30.0,
        )
        if res.get("ok") and phone:
            with _LOCK:
                cfg = load_config()
                cfg["phone"] = phone
                _save_config(cfg)
        return res
    # Login is non-strict: ignore masked / leftover UI junk; use server .env.
    cleaned_id = sanitize_api_id(api_id)
    cleaned_hash = sanitize_api_hash(api_hash)
    if cleaned_id or cleaned_hash:
        saved = save_credentials(api_id=cleaned_id, api_hash=cleaned_hash, strict=False)
        if not saved.get("ok"):
            return saved
    if not phone:
        return _err("phone_missing", "Укажите номер телефона в формате +79991234567")
    cur_id, cur_hash = api_credentials()
    if not cur_id or not cur_hash:
        return _err(
            "api_credentials_missing",
            "Нужны api_id / api_hash на сервере (TELEGRAM_API_ID / TELEGRAM_API_HASH) "
            "— встроенные ключи выключены через TELEGRAM_BUILTIN_API=0",
        )

    # Drop a half-open client left by a previous timed-out attempt.
    _RUNNER.reset()

    async def _start() -> dict[str, Any]:
        from telethon.errors import (  # noqa: PLC0415
            ApiIdInvalidError,
            FloodWaitError,
            PhoneNumberBannedError,
            PhoneNumberFloodError,
            PhoneNumberInvalidError,
        )

        client = await _RUNNER.client()
        if await client.is_user_authorized():
            me = await client.get_me()
            return {"ok": True, "authorized": True, "user": _me_dict(me)}
        try:
            sent = await client.send_code_request(phone)
        except FloodWaitError as exc:
            wait = int(getattr(exc, "seconds", 0) or 0)
            return _err(
                "flood_wait",
                f"Telegram просит подождать {wait}s перед новым кодом",
                wait_seconds=wait,
            )
        except PhoneNumberInvalidError:
            return _err("phone_invalid", "Неверный номер — формат +79991234567")
        except PhoneNumberBannedError:
            return _err("phone_banned", "Этот номер заблокирован в Telegram")
        except PhoneNumberFloodError:
            return _err(
                "phone_flood",
                "Слишком много попыток входа для этого номера — подождите",
            )
        except ApiIdInvalidError:
            return _err(
                "api_id_invalid",
                "Неверный api_id/api_hash — проверьте TELEGRAM_API_* / my.telegram.org",
            )
        return {
            "ok": True,
            "authorized": False,
            "code_sent": True,
            "phone_code_hash": getattr(sent, "phone_code_hash", ""),
            "proxy_configured": bool(_proxy_url()),
        }

    res = _call(_start, timeout=_LOGIN_TIMEOUT)
    if res.get("ok") and not res.get("authorized"):
        _RUNNER.phone = phone
        _RUNNER.phone_code_hash = str(res.pop("phone_code_hash", "") or "")
        with _LOCK:
            cfg = load_config()
            cfg["phone"] = phone
            _save_config(cfg)
    return res


def save_session(
    *,
    session: str,
    phone: str = "",
) -> dict[str, Any]:
    """Persist a Telethon StringSession (bypass phone code when MTProto is blocked)."""
    if _gateway_base():
        return _gateway_request(
            "POST",
            "session",
            json_body={"session": session, "phone": phone},
            timeout=_LOGIN_TIMEOUT + 30.0,
        )
    raw = str(session or "").strip()
    if len(raw) < 30:
        return _err(
            "session_missing",
            "Вставьте StringSession (строка от Telethon, обычно >30 символов)",
        )
    phone_norm = normalize_login_phone(phone) if phone else ""
    with _LOCK:
        cfg = load_config()
        cfg["session"] = raw
        if phone_norm:
            cfg["phone"] = phone_norm
        _save_config(cfg)
    _RUNNER.reset()
    _invalidate_auth_cache()

    async def _probe() -> dict[str, Any]:
        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return _err(
                "session_unauthorized",
                "Сессия сохранена, но Telegram не авторизовал её — "
                "получите новый StringSession",
            )
        me = await client.get_me()
        _RUNNER.persist_session(client)
        return {"ok": True, "authorized": True, "user": _me_dict(me)}

    res = _call(_probe, timeout=_LOGIN_TIMEOUT)
    if res.get("authorized"):
        with _LOCK:
            cfg = load_config()
            cfg["user"] = res.get("user") or {}
            _save_config(cfg)
    return res


def _finish_login(client_result: Any) -> dict[str, Any]:
    return {"ok": True, "authorized": True, "user": _me_dict(client_result)}


def submit_code(code: str) -> dict[str, Any]:
    """Second login step. Returns ``password_required`` when 2FA is on."""
    if _gateway_base():
        res = _gateway_request(
            "POST", "code", json_body={"code": code}, timeout=_LOGIN_TIMEOUT + 30.0
        )
        if res.get("authorized"):
            _persist_after_login(res)
        return res
    code = str(code or "").strip()
    if not code:
        return _err("code_missing", "Введите код из Telegram")
    phone = _RUNNER.phone or str(load_config().get("phone") or "").strip()
    if not phone:
        return _err("login_not_started", "Сначала запросите код (шаг с телефоном)")

    async def _sign_in() -> dict[str, Any]:
        from telethon.errors import SessionPasswordNeededError  # noqa: PLC0415

        client = await _RUNNER.client()
        try:
            me = await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=_RUNNER.phone_code_hash or None,
            )
        except SessionPasswordNeededError:
            return {"ok": True, "authorized": False, "password_required": True}
        return _finish_login(me)

    res = _call(_sign_in, timeout=60.0)
    if res.get("authorized"):
        _persist_after_login(res)
    return res


def submit_password(password: str) -> dict[str, Any]:
    """Third login step — cloud (2FA) password."""
    if _gateway_base():
        res = _gateway_request(
            "POST",
            "password",
            json_body={"password": password},
            timeout=_LOGIN_TIMEOUT + 30.0,
        )
        if res.get("authorized"):
            _persist_after_login(res)
        return res
    if not password:
        return _err("password_missing", "Введите облачный пароль (2FA)")

    async def _sign_in() -> dict[str, Any]:
        client = await _RUNNER.client()
        me = await client.sign_in(password=password)
        return _finish_login(me)

    res = _call(_sign_in, timeout=60.0)
    if res.get("authorized"):
        _persist_after_login(res)
    return res


def _persist_after_login(res: dict[str, Any]) -> None:
    async def _save() -> dict[str, Any]:
        client = await _RUNNER.client()
        _RUNNER.persist_session(client)
        return {"ok": True}

    _call(_save, timeout=20.0)
    with _LOCK:
        cfg = load_config()
        cfg["user"] = res.get("user") or {}
        _save_config(cfg)
    _RUNNER.phone_code_hash = ""
    _invalidate_auth_cache()
    # First contact sync right after login — in the background, so the
    # login response returns as soon as the session is saved.
    try:
        start_contacts_sync(force=True)
    except Exception:  # pragma: no cover - best effort
        log.debug("initial contact sync failed", exc_info=True)


def logout(*, forget_credentials: bool = False) -> dict[str, Any]:
    """Log the session out on Telegram's side and drop local state."""
    if _gateway_base():
        res = _gateway_request("POST", "logout", json_body={}, timeout=30.0)
        with _LOCK:
            cfg = load_config()
            cfg.pop("session", None)
            cfg.pop("user", None)
            if forget_credentials:
                cfg.pop("api_id", None)
                cfg.pop("api_hash", None)
            _save_config(cfg)
        return {"ok": True, "logged_out": bool(res.get("ok")), "via": "gateway"}

    async def _logout() -> dict[str, Any]:
        client = await _RUNNER.client()
        try:
            await client.log_out()
        except Exception:  # pragma: no cover - already dead session
            log.debug("log_out failed", exc_info=True)
        return {"ok": True}

    res = _call(_logout, timeout=30.0) if session_string() else {"ok": True}
    _RUNNER.reset()
    _invalidate_auth_cache()
    with _LOCK:
        cfg = load_config()
        cfg.pop("session", None)
        cfg.pop("user", None)
        if forget_credentials:
            cfg.pop("api_id", None)
            cfg.pop("api_hash", None)
        _save_config(cfg)
        try:
            _contacts_path().unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            log.debug("contacts cache unlink failed", exc_info=True)
    redis_cli = _redis_client()
    if redis_cli is not None:
        try:
            redis_cli.delete(_CONTACTS_REDIS_KEY)
        except Exception:  # pragma: no cover - network
            log.debug("contacts redis cleanup failed", exc_info=True)
    return {"ok": True, "logged_out": bool(res.get("ok"))}


# ── contacts ──────────────────────────────────────────────────────────────


def _contact_from_user(user: Any, *, source: str = "contact") -> Optional[dict[str, Any]]:
    uid = getattr(user, "id", None)
    if uid is None or getattr(user, "bot", False) or getattr(user, "is_self", False):
        return None
    first = str(getattr(user, "first_name", "") or "").strip()
    last = str(getattr(user, "last_name", "") or "").strip()
    name = " ".join(p for p in (first, last) if p)
    username = str(getattr(user, "username", "") or "").strip().lstrip("@")
    return {
        "id": str(uid),
        "tg_chat_id": str(uid),
        "tg_nick": username,
        "name": name or (f"@{username}" if username else str(uid)),
        "phone": str(getattr(user, "phone", "") or "").strip(),
        "peer_source": source,
    }


# Redis mirror: survives container redeploys where $HERMES_HOME is ephemeral,
# so contacts are not re-downloaded from Telegram after every restart.
_CONTACTS_REDIS_KEY = "hermes:telegram_user:contacts"
_REDIS_TRIED = False


def _redis_client() -> Any:
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as exc:
        log.debug("telegram_user redis unavailable (%s); file cache only", exc)
        return None


def _mirror_contacts_to_redis(payload: dict[str, Any]) -> None:
    client = _redis_client()
    if client is None:
        return
    try:
        client.set(_CONTACTS_REDIS_KEY, json.dumps(payload, ensure_ascii=False))
    except Exception:  # pragma: no cover - network
        log.debug("telegram_user redis mirror failed", exc_info=True)


def _hydrate_contacts_from_redis() -> None:
    """One-shot: empty file cache + Redis has a copy → restore the file."""
    global _REDIS_TRIED
    with _LOCK:
        if _REDIS_TRIED:
            return
        _REDIS_TRIED = True
    client = _redis_client()
    if client is None:
        return
    try:
        raw = client.get(_CONTACTS_REDIS_KEY)
    except Exception:  # pragma: no cover - network
        return
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return
    if isinstance(payload, dict) and isinstance(payload.get("contacts"), list):
        _write_json(_contacts_path(), payload)
        log.info(
            "telegram_user contacts restored from redis (%d)",
            len(payload["contacts"]),
        )


def cached_contacts() -> list[dict[str, Any]]:
    if not _contacts_path().is_file():
        _hydrate_contacts_from_redis()
    raw = _read_json(_contacts_path())
    items = raw.get("contacts")
    if not isinstance(items, list):
        return []
    return [c for c in items if isinstance(c, dict) and c.get("tg_chat_id")]


def _merge_contacts(new_items: list[dict[str, Any]]) -> int:
    """Merge a batch into the cache and persist. Returns the new total.

    Incremental writes keep the picker usable while a sync is still running
    and never drop what a previous (possibly interrupted) sync fetched.
    """
    with _LOCK:
        by_id = {c["id"]: c for c in cached_contacts()}
        for c in new_items:
            if not c:
                continue
            prev = by_id.get(c["id"])
            # A saved contact carries the address-book name — don't let a
            # bare dialog row overwrite it.
            if (
                prev
                and prev.get("peer_source") == "contact"
                and c.get("peer_source") == "dialog"
            ):
                continue
            by_id[c["id"]] = c
        contacts = sorted(by_id.values(), key=lambda c: (c.get("name") or "").lower())
        payload = {"fetched_at": time.time(), "contacts": contacts}
        _write_json(_contacts_path(), payload)
    _mirror_contacts_to_redis(payload)
    return len(contacts)


def contacts_stale(ttl: float = _CONTACTS_TTL) -> bool:
    raw = _read_json(_contacts_path())
    try:
        fetched = float(raw.get("fetched_at") or 0)
    except (TypeError, ValueError):
        fetched = 0.0
    return (time.time() - fetched) > ttl


def fetch_contacts(
    *,
    force: bool = False,
    ttl: float = _CONTACTS_TTL,
    dialogs_limit: int = _DIALOGS_LIMIT,
) -> dict[str, Any]:
    """Cache the address book **and** private chats. Falls back to the cache.

    ``contacts.GetContacts`` only returns *saved* contacts, so people you
    actually talk to but never added would be missing from the picker. Private
    dialogs fill that gap.
    """
    if not force and not contacts_stale(ttl):
        items = cached_contacts()
        return {"ok": True, "contacts": items, "total": len(items), "cached": True}

    if _gateway_base():
        res = _gateway_request(
            "POST",
            "contacts/refresh",
            json_body={},
            params={"force": "true" if force else "false"},
            timeout=180.0,
        )
        if res.get("ok") and isinstance(res.get("contacts"), list):
            payload = {
                "fetched_at": time.time(),
                "contacts": res["contacts"],
                "from_address_book": res.get("from_address_book"),
                "from_dialogs": res.get("from_dialogs"),
            }
            _write_json(_contacts_path(), payload)
            _mirror_contacts_to_redis(payload)
        return res

    async def _fetch() -> dict[str, Any]:
        from telethon import functions  # noqa: PLC0415
        from telethon.tl.types import User  # noqa: PLC0415

        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return _err("not_authorized", "Личный Telegram не подключён")

        saved: list[Any] = []
        try:
            result = await client(functions.contacts.GetContactsRequest(hash=0))
            saved = list(getattr(result, "users", []) or [])
        except Exception as exc:  # pragma: no cover - network
            log.warning("GetContacts failed: %s", exc)

        dialog_users: list[Any] = []
        if dialogs_limit:
            try:
                async for dialog in client.iter_dialogs(limit=dialogs_limit):
                    entity = getattr(dialog, "entity", None)
                    if isinstance(entity, User):
                        dialog_users.append(entity)
            except Exception as exc:  # pragma: no cover - network
                log.warning("iter_dialogs failed: %s", exc)

        _RUNNER.persist_session(client)
        return {"ok": True, "users": saved, "dialog_users": dialog_users}

    res = _call(_fetch, timeout=180.0)
    if not res.get("ok"):
        items = cached_contacts()
        return {**res, "contacts": items, "total": len(items), "cached": True}

    by_id: dict[str, dict[str, Any]] = {}
    for user in res.get("users") or []:
        norm = _contact_from_user(user, source="contact")
        if norm:
            by_id[norm["id"]] = norm
    dialog_only = 0
    for user in res.get("dialog_users") or []:
        norm = _contact_from_user(user, source="dialog")
        if norm and norm["id"] not in by_id:
            by_id[norm["id"]] = norm
            dialog_only += 1

    contacts = sorted(by_id.values(), key=lambda c: (c.get("name") or "").lower())
    payload = {"fetched_at": time.time(), "contacts": contacts}
    _write_json(_contacts_path(), payload)
    _mirror_contacts_to_redis(payload)
    return {
        "ok": True,
        "contacts": contacts,
        "total": len(contacts),
        "from_address_book": len(contacts) - dialog_only,
        "from_dialogs": dialog_only,
        "cached": False,
    }


# ── background incremental sync ───────────────────────────────────────────

_SYNC_BATCH = 25  # dialogs merged & persisted per chunk — picker fills live
_SYNC_STATE: dict[str, Any] = {
    "running": False,
    "phase": "",  # starting | address_book | dialogs | done | error
    "scanned": 0,
    "total": 0,
    "from_address_book": 0,
    "from_dialogs": 0,
    "started_at": 0.0,
    "finished_at": 0.0,
    "error": "",
}


def _set_sync(**fields: Any) -> None:
    with _LOCK:
        _SYNC_STATE.update(fields)


def contacts_sync_status() -> dict[str, Any]:
    with _LOCK:
        out = dict(_SYNC_STATE)
    out["ok"] = True
    out["total"] = len(cached_contacts())
    return out


def _sync_worker(dialogs_limit: int) -> None:
    async def _run() -> dict[str, Any]:
        from telethon import functions  # noqa: PLC0415
        from telethon.tl.types import User  # noqa: PLC0415

        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return _err("not_authorized", "Личный Telegram не подключён")

        # Address book first: one fast request, picker becomes useful at once.
        _set_sync(phase="address_book")
        try:
            result = await client(functions.contacts.GetContactsRequest(hash=0))
            book = [
                norm
                for norm in (
                    _contact_from_user(u, source="contact")
                    for u in getattr(result, "users", []) or []
                )
                if norm
            ]
            total = _merge_contacts(book)
            _set_sync(total=total, from_address_book=len(book))
        except Exception as exc:  # pragma: no cover - network
            log.warning("GetContacts failed: %s", exc)

        # Private dialogs stream in batches; each batch lands in the cache.
        _set_sync(phase="dialogs")
        batch: list[dict[str, Any]] = []
        scanned = 0
        dialog_rows = 0
        try:
            async for dialog in client.iter_dialogs(limit=dialogs_limit):
                scanned += 1
                entity = getattr(dialog, "entity", None)
                if isinstance(entity, User):
                    norm = _contact_from_user(entity, source="dialog")
                    if norm:
                        batch.append(norm)
                if len(batch) >= _SYNC_BATCH:
                    total = _merge_contacts(batch)
                    dialog_rows += len(batch)
                    batch = []
                    _set_sync(total=total, scanned=scanned, from_dialogs=dialog_rows)
                elif scanned % 50 == 0:
                    _set_sync(scanned=scanned)
        except Exception as exc:  # pragma: no cover - network
            log.warning("iter_dialogs failed: %s", exc)
        if batch:
            total = _merge_contacts(batch)
            dialog_rows += len(batch)
            _set_sync(total=total, from_dialogs=dialog_rows)
        _set_sync(scanned=scanned)
        _RUNNER.persist_session(client)
        return {"ok": True}

    try:
        res = _call(_run, timeout=600.0)
    except Exception as exc:  # pragma: no cover - defensive
        res = _err("sync_failed", str(exc))
    _set_sync(
        running=False,
        finished_at=time.time(),
        phase="done" if res.get("ok") else "error",
        error="" if res.get("ok") else str(res.get("detail") or res.get("error") or ""),
    )


def start_contacts_sync(
    *,
    force: bool = False,
    ttl: float = _CONTACTS_TTL,
    dialogs_limit: int = _DIALOGS_LIMIT,
) -> dict[str, Any]:
    """Kick off a background contact sync and return immediately.

    The HTTP layer never blocks on Telegram: callers poll
    ``contacts_sync_status()`` while the daemon thread merges batches into
    the cache (file + Redis mirror).
    """
    if not force and not contacts_stale(ttl):
        return {**contacts_sync_status(), "started": False, "cached": True}
    with _LOCK:
        if _SYNC_STATE["running"]:
            return {**contacts_sync_status(), "started": False, "running": True}
        _SYNC_STATE.update(
            running=True,
            phase="starting",
            scanned=0,
            from_address_book=0,
            from_dialogs=0,
            started_at=time.time(),
            finished_at=0.0,
            error="",
        )
        thread = threading.Thread(
            target=_sync_worker,
            args=(dialogs_limit,),
            name="tg-user-contacts-sync",
            daemon=True,
        )
        thread.start()
    return {**contacts_sync_status(), "started": True, "running": True}


def find_cached_contact(
    *,
    tg_nick: str = "",
    tg_chat_id: str = "",
) -> Optional[dict[str, Any]]:
    nick = str(tg_nick or "").strip().lstrip("@").lower()
    chat_id = str(tg_chat_id or "").strip()
    for c in cached_contacts():
        if chat_id and str(c.get("tg_chat_id") or "") == chat_id:
            return c
        if nick and str(c.get("tg_nick") or "").lower() == nick:
            return c
    return None


# ── peers / send ──────────────────────────────────────────────────────────


def _peer_arg(peer: str) -> Any:
    raw = str(peer or "").strip()
    m = _TME_RE.search(raw)
    if m:
        return f"@{m.group(1)}"
    if raw.lower().startswith("tg://user?id="):
        raw = raw.split("id=", 1)[-1].strip()
    if _PEER_ID_RE.fullmatch(raw):
        return int(raw)
    return raw if raw.startswith("@") else f"@{raw.lstrip('@')}"


def resolve_peer(query: str) -> dict[str, Any]:
    """Resolve @nick / t.me / numeric id through the user session."""
    target = _peer_arg(query)
    if not str(target).strip("@"):
        return _err("peer_missing", "Укажите @ник, t.me/… или numeric id")

    async def _resolve() -> dict[str, Any]:
        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return _err("not_authorized", "Личный Telegram не подключён")
        entity = await client.get_entity(target)
        norm = _contact_from_user(entity) or {}
        if not norm:
            eid = getattr(entity, "id", None)
            norm = {"id": str(eid), "tg_chat_id": str(eid), "tg_nick": "", "name": ""}
        return {"ok": True, **norm, "resolved_via": "mtproto"}

    return _call(_resolve, timeout=45.0)


def send_message(*, peer: str, text: str) -> dict[str, Any]:
    """Send ``text`` to ``peer`` from the connected personal account."""
    text = (text or "").strip()
    if not text:
        return _err("empty_text", "message required")
    if _gateway_base():
        return _gateway_request(
            "POST",
            "send",
            json_body={"peer": peer, "text": text},
            timeout=60.0,
        )
    target = _peer_arg(peer)
    if not str(target).strip("@"):
        return _err("telegram_chat_missing", "Нужен @ник или chat id")

    async def _send() -> dict[str, Any]:
        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return _err("not_authorized", "Личный Telegram не подключён")
        msg = await client.send_message(target, text)
        chat_id = getattr(getattr(msg, "peer_id", None), "user_id", None)
        _RUNNER.persist_session(client)
        return {
            "ok": True,
            "message_id": getattr(msg, "id", None),
            "chat_id": str(chat_id) if chat_id else str(target).lstrip("@"),
            "via": "user_account",
        }

    return _call(_send, timeout=60.0)
