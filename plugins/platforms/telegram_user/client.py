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

_PEER_ID_RE = re.compile(r"^-?\d{1,20}$")
_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})",
    re.IGNORECASE,
)


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


def api_credentials() -> tuple[str, str]:
    """Return ``(api_id, api_hash)`` from env, falling back to stored config."""
    cfg = load_config()
    api_id = (os.getenv("TELEGRAM_API_ID") or "").strip() or str(
        cfg.get("api_id") or ""
    ).strip()
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip() or str(
        cfg.get("api_hash") or ""
    ).strip()
    return api_id, api_hash


def session_string() -> str:
    return (os.getenv("TELEGRAM_USER_SESSION") or "").strip() or str(
        load_config().get("session") or ""
    ).strip()


def save_credentials(*, api_id: str = "", api_hash: str = "") -> dict[str, Any]:
    """Persist my.telegram.org app credentials. Empty values keep the old ones."""
    api_id = str(api_id or "").strip()
    api_hash = str(api_hash or "").strip()
    if api_id and not api_id.isdigit():
        return {
            "ok": False,
            "error": "api_id_invalid",
            "detail": "api_id — целое число из my.telegram.org",
        }
    with _LOCK:
        cfg = load_config()
        if api_id:
            cfg["api_id"] = api_id
        if api_hash:
            cfg["api_hash"] = api_hash
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
        return future.result(timeout)

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
            self.submit(lambda: _disconnect(client), timeout=15.0)
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

        client = TelegramClient(
            StringSession(session_string() or None),
            int(api_id),
            api_hash,
            device_model="Hermes CRM",
            app_version="1.0",
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
            "Нужны api_id / api_hash с my.telegram.org → API development tools",
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
    except asyncio.TimeoutError:
        return _err("timeout", f"Telegram не ответил за {timeout:.0f}s")
    except Exception as exc:  # pragma: no cover - network / telethon errors
        log.warning("telegram_user call failed: %s", exc)
        return _err(exc.__class__.__name__, str(exc) or exc.__class__.__name__)


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


def user_status(*, probe: bool = True) -> dict[str, Any]:
    """UI-facing account block. No secrets — only presence flags."""
    api_id, api_hash = api_credentials()
    cfg = load_config()
    env_api = bool(
        (os.getenv("TELEGRAM_API_ID") or "").strip()
        and (os.getenv("TELEGRAM_API_HASH") or "").strip()
    )
    out: dict[str, Any] = {
        "ok": True,
        "available": telethon_available(),
        "api_configured": bool(api_id and api_hash),
        "api_source": "env" if env_api else ("config" if api_id and api_hash else ""),
        "session_saved": bool(session_string()),
        "phone": str(cfg.get("phone") or "") or None,
        "authorized": False,
        "user": cfg.get("user") or None,
        "contacts_cached": len(cached_contacts()),
        "contacts_fetched_at": _read_json(_contacts_path()).get("fetched_at"),
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
    api_id, api_hash = api_credentials()
    if not api_id or not api_hash or not session_string():
        return False
    now = time.time()
    with _LOCK:
        if now - _AUTH_CACHE["checked_at"] < ttl:
            return bool(_AUTH_CACHE["authorized"])

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
    """Send the Telegram login code to ``phone``."""
    phone = str(phone or "").strip()
    if api_id or api_hash:
        saved = save_credentials(api_id=api_id, api_hash=api_hash)
        if not saved.get("ok"):
            return saved
    if not phone:
        return _err("phone_missing", "Укажите номер телефона в формате +79991234567")
    cur_id, cur_hash = api_credentials()
    if not cur_id or not cur_hash:
        return _err(
            "api_credentials_missing",
            "Нужны api_id / api_hash с my.telegram.org → API development tools",
        )

    async def _start() -> dict[str, Any]:
        client = await _RUNNER.client()
        if await client.is_user_authorized():
            me = await client.get_me()
            return {"ok": True, "authorized": True, "user": _me_dict(me)}
        sent = await client.send_code_request(phone)
        return {
            "ok": True,
            "authorized": False,
            "code_sent": True,
            "phone_code_hash": getattr(sent, "phone_code_hash", ""),
        }

    res = _call(_start, timeout=60.0)
    if res.get("ok") and not res.get("authorized"):
        _RUNNER.phone = phone
        _RUNNER.phone_code_hash = str(res.pop("phone_code_hash", "") or "")
        with _LOCK:
            cfg = load_config()
            cfg["phone"] = phone
            _save_config(cfg)
    return res


def _finish_login(client_result: Any) -> dict[str, Any]:
    return {"ok": True, "authorized": True, "user": _me_dict(client_result)}


def submit_code(code: str) -> dict[str, Any]:
    """Second login step. Returns ``password_required`` when 2FA is on."""
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
    # First contact sync right after login so the picker is populated.
    try:
        fetch_contacts(force=True)
    except Exception:  # pragma: no cover - best effort
        log.debug("initial contact sync failed", exc_info=True)


def logout(*, forget_credentials: bool = False) -> dict[str, Any]:
    """Log the session out on Telegram's side and drop local state."""

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


def cached_contacts() -> list[dict[str, Any]]:
    raw = _read_json(_contacts_path())
    items = raw.get("contacts")
    if not isinstance(items, list):
        return []
    return [c for c in items if isinstance(c, dict) and c.get("tg_chat_id")]


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
    _write_json(
        _contacts_path(),
        {"fetched_at": time.time(), "contacts": contacts},
    )
    return {
        "ok": True,
        "contacts": contacts,
        "total": len(contacts),
        "from_address_book": len(contacts) - dialog_only,
        "from_dialogs": dialog_only,
        "cached": False,
    }


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
