"""Telethon MTProto egress for Selectel (RU) Hermes.

Selectel VDS cannot open Telegram DCs. This service runs on Railway (non-RU IP)
and exposes the personal-account login / contacts / send surface over HTTPS.

Auth: ``Authorization: Bearer <EGRESS_TOKEN>`` or path prefix ``/t/<token>/…``.
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

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

log = logging.getLogger("telegram-user-egress")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DEFAULT_TIMEOUT = 60.0
_LOGIN_TIMEOUT = 45.0
_CONNECT_TIMEOUT = 15.0
_CONNECT_RETRIES = 5
_DIALOGS_LIMIT = 500
_PHONE_DIGITS_RE = re.compile(r"\D+")

# Telegram Desktop public keys (same as Telethon examples). Env overrides win.
_BUILTIN_API_ID = "2040"
_BUILTIN_API_HASH = "b18441a1ff607e10a989891a5462e627"

_DATA = Path(os.getenv("DATA_DIR") or "/data")
_DATA.mkdir(parents=True, exist_ok=True)
_CONFIG_PATH = _DATA / "config.json"
_CONTACTS_PATH = _DATA / "contacts.json"
_LOCK = threading.RLock()

app = FastAPI(title="telegram-user-egress", version="1.0.0")


def _token() -> str:
    return (os.getenv("EGRESS_TOKEN") or "").strip()


def _authorized(request: Request, authorization: str | None) -> bool:
    expected = _token()
    if not expected:
        return False
    path = request.url.path or ""
    if path.startswith(f"/t/{expected}/") or path == f"/t/{expected}":
        return True
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() == expected
    return False


def _require_auth(request: Request, authorization: str | None) -> None:
    if not _authorized(request, authorization):
        raise HTTPException(status_code=401, detail="unauthorized")


def _strip_token_prefix(path: str) -> str:
    expected = _token()
    prefix = f"/t/{expected}"
    if expected and path.startswith(prefix):
        rest = path[len(prefix) :] or "/"
        return rest if rest.startswith("/") else f"/{rest}"
    return path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass


def load_config() -> dict[str, Any]:
    return _read_json(_CONFIG_PATH)


def save_config(cfg: dict[str, Any]) -> None:
    _write_json(_CONFIG_PATH, cfg)


def api_credentials() -> tuple[str, str]:
    cfg = load_config()
    api_id = (os.getenv("TELEGRAM_API_ID") or "").strip() or str(cfg.get("api_id") or "").strip()
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip() or str(cfg.get("api_hash") or "").strip()
    if api_id and api_hash:
        return api_id, api_hash
    return _BUILTIN_API_ID, _BUILTIN_API_HASH


def session_string() -> str:
    return (os.getenv("TELEGRAM_USER_SESSION") or "").strip() or str(
        load_config().get("session") or ""
    ).strip()


def normalize_phone(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    digits = _PHONE_DIGITS_RE.sub("", raw)
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "7" + digits
    return "+" + digits


def _sent_code_meta(sent: Any) -> dict[str, Any]:
    type_obj = getattr(sent, "type", None)
    name = type(type_obj).__name__ if type_obj is not None else ""
    low = name.lower()
    if "app" in low:
        delivery = "telegram_app"
        hint = (
            "Код в приложении Telegram (чат «Login code»), не SMS. "
            "Откройте Telegram на этом номере или на другом устройстве с аккаунтом."
        )
    elif "sms" in low:
        delivery = "sms"
        hint = "Код отправлен SMS на этот номер."
    elif "call" in low or "flash" in low:
        delivery = "call"
        hint = "Telegram позвонит — код в голосовом сообщении."
    else:
        delivery = "unknown"
        hint = "Проверьте Telegram и SMS на этом номере."
    return {
        "code_delivery": delivery,
        "code_type": name,
        "code_delivery_hint": hint,
    }


def _err(error: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "detail": detail, **extra}


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


class _Runner:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._lock = threading.RLock()
        self.phone = ""
        self.phone_code_hash = ""

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop, args=(loop,), name="tg-egress-loop", daemon=True
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
        with self._lock:
            client = self._client
            self._client = None
            self.phone = ""
            self.phone_code_hash = ""
        if client is None:
            return
        try:
            self.submit(lambda: client.disconnect(), timeout=8.0)
        except Exception:
            log.debug("disconnect failed", exc_info=True)

    async def client(self) -> Any:
        with self._lock:
            client = self._client
        if client is not None:
            if not client.is_connected():
                await client.connect()
            return client

        from telethon import TelegramClient
        from telethon.sessions import StringSession

        api_id, api_hash = api_credentials()
        client = TelegramClient(
            StringSession(session_string() or None),
            int(api_id),
            api_hash,
            device_model="Hermes TG egress",
            app_version="1.0",
            connection_retries=_CONNECT_RETRIES,
            retry_delay=1,
            timeout=_CONNECT_TIMEOUT,
            auto_reconnect=True,
        )
        await client.connect()
        with self._lock:
            self._client = client
        return client

    def persist_session(self, client: Any) -> None:
        try:
            session = client.session.save()
        except Exception:
            return
        with _LOCK:
            cfg = load_config()
            cfg["session"] = session
            if self.phone:
                cfg["phone"] = self.phone
            save_config(cfg)


_RUNNER = _Runner()


def _call(
    factory: Callable[[], Coroutine[Any, Any, Any]],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    try:
        return _RUNNER.submit(factory, timeout=timeout)
    except TimeoutError:
        try:
            _RUNNER.reset()
        except Exception:
            pass
        return _err("timeout", f"Telegram не ответил за {timeout:.0f}s")
    except Exception as exc:
        log.warning("call failed: %s", exc)
        try:
            _RUNNER.reset()
        except Exception:
            pass
        return _err(exc.__class__.__name__, str(exc) or exc.__class__.__name__)


def user_status(*, probe: bool = True) -> dict[str, Any]:
    api_id, api_hash = api_credentials()
    cfg = load_config()
    out: dict[str, Any] = {
        "ok": True,
        "available": True,
        "gateway": True,
        "api_configured": bool(api_id and api_hash),
        "session_saved": bool(session_string()),
        "phone": str(cfg.get("phone") or "") or None,
        "authorized": False,
        "user": cfg.get("user") or None,
        "contacts_cached": len((_read_json(_CONTACTS_PATH).get("contacts") or [])),
    }
    if not probe or not out["session_saved"]:
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
        cfg = load_config()
        cfg["user"] = res["user"]
        save_config(cfg)
    return out


class LoginBody(BaseModel):
    phone: str = ""
    api_id: str = ""
    api_hash: str = ""
    force_sms: bool = False


class CodeBody(BaseModel):
    code: str = ""


class PasswordBody(BaseModel):
    password: str = ""


class SessionBody(BaseModel):
    session: str = ""
    phone: str = ""


class CredentialsBody(BaseModel):
    api_id: str = ""
    api_hash: str = ""


class SendBody(BaseModel):
    peer: str = ""
    text: str = ""


def start_login(*, phone: str, api_id: str = "", api_hash: str = "", force_sms: bool = False) -> dict[str, Any]:
    phone = normalize_phone(phone)
    if api_id.strip().isdigit() or api_hash.strip():
        cfg = load_config()
        if api_id.strip().isdigit():
            cfg["api_id"] = api_id.strip()
        if api_hash.strip():
            cfg["api_hash"] = api_hash.strip()
        save_config(cfg)
        _RUNNER.reset()
    if not phone:
        return _err("phone_missing", "Укажите номер в формате +79991234567")

    _RUNNER.reset()

    async def _start() -> dict[str, Any]:
        from telethon.errors import (
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
            sent = await client.send_code_request(phone, force_sms=bool(force_sms))
        except FloodWaitError as exc:
            return _err("flood_wait", f"Подождите {int(getattr(exc, 'seconds', 0) or 0)}s")
        except PhoneNumberInvalidError:
            return _err("phone_invalid", "Неверный номер")
        except PhoneNumberBannedError:
            return _err("phone_banned", "Номер заблокирован")
        except PhoneNumberFloodError:
            return _err("phone_flood", "Слишком много попыток")
        except ApiIdInvalidError:
            return _err("api_id_invalid", "Неверный api_id/api_hash")
        return {
            "ok": True,
            "authorized": False,
            "code_sent": True,
            "phone_code_hash": getattr(sent, "phone_code_hash", ""),
            "force_sms": bool(force_sms),
            **_sent_code_meta(sent),
        }

    res = _call(_start, timeout=_LOGIN_TIMEOUT)
    if res.get("ok") and not res.get("authorized"):
        _RUNNER.phone = phone
        _RUNNER.phone_code_hash = str(res.pop("phone_code_hash", "") or "")
        cfg = load_config()
        cfg["phone"] = phone
        if _RUNNER.phone_code_hash:
            cfg["phone_code_hash"] = _RUNNER.phone_code_hash
        save_config(cfg)
    return res


def submit_code(code: str) -> dict[str, Any]:
    code = str(code or "").strip()
    if not code:
        return _err("code_missing", "Введите код из Telegram")
    phone = _RUNNER.phone or str(load_config().get("phone") or "").strip()
    if not phone:
        return _err("login_not_started", "Сначала запросите код")
    phone_code_hash = (
        _RUNNER.phone_code_hash or str(load_config().get("phone_code_hash") or "").strip()
    )

    async def _sign_in() -> dict[str, Any]:
        from telethon.errors import SessionPasswordNeededError

        client = await _RUNNER.client()
        try:
            me = await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=phone_code_hash or None,
            )
        except SessionPasswordNeededError:
            return {"ok": True, "authorized": False, "password_required": True}
        _RUNNER.persist_session(client)
        return {"ok": True, "authorized": True, "user": _me_dict(me)}

    res = _call(_sign_in, timeout=_LOGIN_TIMEOUT)
    if res.get("authorized") and res.get("user"):
        cfg = load_config()
        cfg["user"] = res["user"]
        save_config(cfg)
    return res


def submit_password(password: str) -> dict[str, Any]:
    password = str(password or "")
    if not password:
        return _err("password_missing", "Нужен облачный пароль 2FA")

    async def _pw() -> dict[str, Any]:
        client = await _RUNNER.client()
        me = await client.sign_in(password=password)
        _RUNNER.persist_session(client)
        return {"ok": True, "authorized": True, "user": _me_dict(me)}

    res = _call(_pw, timeout=_LOGIN_TIMEOUT)
    if res.get("authorized") and res.get("user"):
        cfg = load_config()
        cfg["user"] = res["user"]
        save_config(cfg)
    return res


def save_session(*, session: str, phone: str = "") -> dict[str, Any]:
    raw = str(session or "").strip()
    if len(raw) < 30:
        return _err("session_missing", "StringSession слишком короткий")
    cfg = load_config()
    cfg["session"] = raw
    if phone:
        cfg["phone"] = normalize_phone(phone)
    save_config(cfg)
    _RUNNER.reset()

    async def _probe() -> dict[str, Any]:
        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return _err("session_unauthorized", "Сессия не авторизована")
        me = await client.get_me()
        _RUNNER.persist_session(client)
        return {"ok": True, "authorized": True, "user": _me_dict(me)}

    res = _call(_probe, timeout=_LOGIN_TIMEOUT)
    if res.get("authorized") and res.get("user"):
        cfg = load_config()
        cfg["user"] = res["user"]
        save_config(cfg)
    return res


def logout() -> dict[str, Any]:
    async def _out() -> dict[str, Any]:
        client = await _RUNNER.client()
        try:
            await client.log_out()
        except Exception:
            pass
        return {"ok": True}

    res = _call(_out, timeout=20.0)
    _RUNNER.reset()
    cfg = load_config()
    cfg.pop("session", None)
    cfg.pop("user", None)
    save_config(cfg)
    return res if res.get("ok") else {"ok": True}


def _contact_from_user(user: Any, *, source: str = "contact") -> dict[str, Any] | None:
    if user is None or getattr(user, "bot", False) or getattr(user, "deleted", False):
        return None
    if getattr(user, "is_self", False):
        return None
    uid = getattr(user, "id", None)
    if uid is None:
        return None
    first = str(getattr(user, "first_name", "") or "").strip()
    last = str(getattr(user, "last_name", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip().lstrip("@")
    name = " ".join(p for p in (first, last) if p)
    return {
        "id": str(uid),
        "tg_chat_id": str(uid),
        "tg_nick": username,
        "name": name or (f"@{username}" if username else str(uid)),
        "phone": str(getattr(user, "phone", "") or "").strip(),
        # Match hermes telegram_user cache schema (peer_source, nick without @).
        "peer_source": source,
        "source": source,
    }


def fetch_contacts(*, force: bool = True) -> dict[str, Any]:
    async def _fetch() -> dict[str, Any]:
        from telethon import functions
        from telethon.tl.types import User

        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return _err("not_authorized", "Личный Telegram не подключён")

        saved: list[Any] = []
        try:
            result = await client(functions.contacts.GetContactsRequest(hash=0))
            saved = list(getattr(result, "users", []) or [])
        except Exception as exc:
            log.warning("GetContacts failed: %s", exc)

        dialog_users: list[Any] = []
        try:
            async for dialog in client.iter_dialogs(limit=_DIALOGS_LIMIT):
                entity = getattr(dialog, "entity", None)
                if isinstance(entity, User):
                    dialog_users.append(entity)
        except Exception as exc:
            log.warning("iter_dialogs failed: %s", exc)

        _RUNNER.persist_session(client)
        return {"ok": True, "users": saved, "dialog_users": dialog_users}

    res = _call(_fetch, timeout=180.0)
    if not res.get("ok"):
        cached = _read_json(_CONTACTS_PATH).get("contacts") or []
        return {**res, "contacts": cached, "total": len(cached), "cached": True}

    by_id: dict[str, dict[str, Any]] = {}
    for user in res.get("users") or []:
        norm = _contact_from_user(user, source="contact")
        if norm:
            by_id[norm["id"]] = norm
    from_dialogs = 0
    for user in res.get("dialog_users") or []:
        norm = _contact_from_user(user, source="dialog")
        if norm and norm["id"] not in by_id:
            by_id[norm["id"]] = norm
            from_dialogs += 1
    contacts = sorted(by_id.values(), key=lambda c: (c.get("name") or "").lower())
    payload = {
        "fetched_at": time.time(),
        "contacts": contacts,
        "from_address_book": len(res.get("users") or []),
        "from_dialogs": from_dialogs,
    }
    _write_json(_CONTACTS_PATH, payload)
    return {
        "ok": True,
        "contacts": contacts,
        "total": len(contacts),
        "from_address_book": payload["from_address_book"],
        "from_dialogs": from_dialogs,
        "cached": False,
    }


_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})",
    re.IGNORECASE,
)
_PEER_ID_RE = re.compile(r"^-?\d{1,20}$")


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


def resolve_peer(*, peer: str) -> dict[str, Any]:
    """Resolve @nick / t.me / numeric id via Telethon on the egress IP."""
    raw = str(peer or "").strip()
    if not raw:
        return _err("peer_missing", "Укажите @ник, t.me/… или numeric id")
    target = _peer_arg(raw)
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
            if eid is None:
                return _err("peer_unresolved", f"Не удалось расшифровать {raw}")
            norm = {
                "id": str(eid),
                "tg_chat_id": str(eid),
                "tg_nick": "",
                "name": "",
            }
        return {
            "ok": True,
            **norm,
            "resolved_via": "mtproto_gateway",
            "via": "gateway",
        }

    return _call(_resolve, timeout=45.0)


def send_message(*, peer: str, text: str) -> dict[str, Any]:
    text = (text or "").strip()
    peer = (peer or "").strip()
    if not text:
        return _err("empty_text", "message required")
    if not peer:
        return _err("telegram_chat_missing", "Нужен @ник или chat id")
    target: Any = _peer_arg(peer)

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
            "chat_id": str(chat_id) if chat_id else str(peer).lstrip("@"),
            "via": "user_account_gateway",
        }

    return _call(_send, timeout=60.0)


def _message_ts(msg: Any) -> str:
    raw = getattr(msg, "date", None)
    if raw is None:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        return raw.isoformat()
    except Exception:
        return str(raw)


def fetch_history(*, peer: str, limit: int = 40) -> dict[str, Any]:
    """Recent private-chat messages for MoySklad inbound accounting."""
    raw = str(peer or "").strip()
    if not raw:
        return _err("peer_missing", "Укажите @ник, t.me/… или numeric id")
    try:
        lim = max(1, min(100, int(limit or 40)))
    except (TypeError, ValueError):
        lim = 40
    target: Any = _peer_arg(raw)

    async def _hist() -> dict[str, Any]:
        client = await _RUNNER.client()
        if not await client.is_user_authorized():
            return _err("not_authorized", "Личный Telegram не подключён")
        entity = await client.get_entity(target)
        rows: list[dict[str, Any]] = []
        async for msg in client.iter_messages(entity, limit=lim):
            text = str(getattr(msg, "message", None) or "").strip()
            if not text:
                continue
            outbound = bool(getattr(msg, "out", False))
            rows.append(
                {
                    "direction": "outbound" if outbound else "inbound",
                    "text": text[:4000],
                    "ts": _message_ts(msg),
                    "message_id": getattr(msg, "id", None),
                }
            )
        rows.reverse()
        eid = getattr(entity, "id", None)
        nick = str(getattr(entity, "username", None) or "").lstrip("@")
        return {
            "ok": True,
            "messages": rows,
            "tg_chat_id": str(eid) if eid is not None else "",
            "tg_nick": nick,
            "count": len(rows),
            "via": "user_account_gateway",
        }

    return _call(_hist, timeout=60.0)


@app.get("/healthz")
def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.api_route("/t/{token}/{path:path}", methods=["GET", "POST", "DELETE"])
@app.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
async def gateway(
    request: Request,
    path: str = "",
    token: str | None = None,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    # Normalize path whether called via /t/<token>/… or bare /…
    full = request.url.path
    if token is not None and token != _token():
        raise HTTPException(status_code=401, detail="unauthorized")
    _require_auth(request, authorization)
    route = _strip_token_prefix(full).rstrip("/") or "/"
    if route.startswith("/"):
        route = route[1:]

    probe = (request.query_params.get("probe") or "true").lower() not in ("0", "false", "no")
    force = (request.query_params.get("force") or "true").lower() not in ("0", "false", "no")

    if request.method == "GET" and route in ("", "status"):
        return JSONResponse(user_status(probe=probe))

    body: dict[str, Any] = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}

    if request.method == "POST" and route == "credentials":
        cfg = load_config()
        if str(body.get("api_id") or "").strip().isdigit():
            cfg["api_id"] = str(body.get("api_id")).strip()
        if str(body.get("api_hash") or "").strip():
            cfg["api_hash"] = str(body.get("api_hash")).strip()
        save_config(cfg)
        _RUNNER.reset()
        return JSONResponse({"ok": True, **user_status(probe=False)})

    if request.method == "POST" and route == "login":
        out = start_login(
            phone=str(body.get("phone") or ""),
            api_id=str(body.get("api_id") or ""),
            api_hash=str(body.get("api_hash") or ""),
            force_sms=bool(body.get("force_sms")),
        )
        return JSONResponse(out, status_code=200 if out.get("ok") else 400)

    if request.method == "POST" and route == "code":
        out = submit_code(str(body.get("code") or ""))
        return JSONResponse(out, status_code=200 if out.get("ok") else 400)

    if request.method == "POST" and route == "password":
        out = submit_password(str(body.get("password") or ""))
        return JSONResponse(out, status_code=200 if out.get("ok") else 400)

    if request.method == "POST" and route == "session":
        out = save_session(session=str(body.get("session") or ""), phone=str(body.get("phone") or ""))
        return JSONResponse(out, status_code=200 if out.get("ok") else 400)

    if request.method == "POST" and route == "logout":
        return JSONResponse(logout())

    if request.method == "POST" and route in ("contacts/refresh", "contacts"):
        out = fetch_contacts(force=force)
        return JSONResponse(out, status_code=200 if out.get("ok") else 400)

    if request.method == "POST" and route == "resolve":
        out = resolve_peer(peer=str(body.get("peer") or body.get("query") or ""))
        return JSONResponse(out, status_code=200 if out.get("ok") else 400)

    if request.method == "POST" and route == "send":
        out = send_message(peer=str(body.get("peer") or ""), text=str(body.get("text") or ""))
        return JSONResponse(out, status_code=200 if out.get("ok") else 400)

    if request.method == "POST" and route == "history":
        out = fetch_history(
            peer=str(body.get("peer") or body.get("query") or ""),
            limit=int(body.get("limit") or 40),
        )
        return JSONResponse(out, status_code=200 if out.get("ok") else 400)

    raise HTTPException(status_code=404, detail=f"unknown route: {route}")
