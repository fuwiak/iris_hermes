#!/usr/bin/env python3
"""Real Iris /chat E2E: password login → WS ticket → prompt.submit → answer.

This is NOT an OpenRouter urllib smoke. It drives the same path the browser
uses on https://hermes-agent-ai.ru/chat:

  POST /auth/password-login
  POST /api/auth/ws-ticket
  WS   /api/ws?ticket=…
  RPC  session.create
  RPC  prompt.submit
  wait message.complete with non-empty assistant text

Exit 0 only when the assistant reply contains ``--expect`` (default: a
token embedded in the question).

Env (or flags):
  IRIS_CHAT_BASE_URL   default http://127.0.0.1:8080
  IRIS_CHAT_USER       or HERMES_DASHBOARD_BASIC_AUTH_USERNAME
  IRIS_CHAT_PASSWORD   or HERMES_DASHBOARD_BASIC_AUTH_PASSWORD
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any

import httpx
import websockets.sync.client as ws_sync


def _env(*names: str, default: str = "") -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return default


def login(client: httpx.Client, *, username: str, password: str) -> None:
    r = client.post(
        "/auth/password-login",
        json={
            "provider": "basic",
            "username": username,
            "password": password,
            "next": "/chat",
        },
    )
    if r.status_code != 200:
        raise SystemExit(f"login HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    if not body.get("ok"):
        raise SystemExit(f"login not ok: {body}")


def mint_ws_ticket(client: httpx.Client) -> str:
    r = client.post("/api/auth/ws-ticket")
    if r.status_code != 200:
        raise SystemExit(f"ws-ticket HTTP {r.status_code}: {r.text[:300]}")
    ticket = (r.json() or {}).get("ticket")
    if not ticket:
        raise SystemExit(f"ws-ticket missing ticket: {r.text[:300]}")
    return str(ticket)


class ChatWS:
    def __init__(self, ws_url: str, *, origin: str) -> None:
        self.ws = ws_sync.connect(
            ws_url,
            open_timeout=20,
            max_size=None,
            additional_headers={"Origin": origin},
        )
        self._id = 0
        # Drain gateway.ready
        self._recv_until(
            lambda o: o.get("method") == "event"
            and ((o.get("params") or {}).get("type") == "gateway.ready"),
            timeout=20,
        )

    def _next_id(self) -> str:
        self._id += 1
        return f"e2e-{self._id}"

    def _recv_until(self, pred, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self.ws.recv(timeout=max(0.05, deadline - time.monotonic()))
            except TimeoutError:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if pred(obj):
                return obj
        raise TimeoutError("ws predicate timed out")

    def rpc(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        rid = self._next_id()
        self.ws.send(
            json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        )
        return self._recv_until(lambda o: o.get("id") == rid, timeout=timeout)

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def _event_name(obj: dict) -> str:
    if obj.get("method") != "event":
        return ""
    params = obj.get("params") or {}
    return str(params.get("type") or "")


def _event_payload(obj: dict) -> dict:
    params = obj.get("params") or {}
    payload = params.get("payload")
    if isinstance(payload, dict):
        return payload
    return {}


def run_chat_turn(
    *,
    base_url: str,
    username: str,
    password: str,
    question: str,
    expect: str,
    timeout_s: float,
) -> str:
    base = base_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=60.0, follow_redirects=False) as client:
        login(client, username=username, password=password)
        ticket = mint_ws_ticket(client)
        if base.startswith("https://"):
            ws_base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            ws_base = "ws://" + base[len("http://") :]
        else:
            ws_base = "ws://" + base
        ws_url = f"{ws_base}/api/ws?ticket={ticket}"
        # Origin must match the dashboard host for CSRF-ish WS checks.
        chat = ChatWS(ws_url, origin=base)
        try:
            created = chat.rpc(
                "session.create",
                {"cols": 80, "source": "iris-chat-e2e"},
                timeout=60,
            )
            if created.get("error"):
                raise SystemExit(f"session.create error: {created['error']}")
            result = created.get("result") or {}
            sid = result.get("session_id") or result.get("id")
            if not sid:
                raise SystemExit(f"session.create no id: {created}")

            submitted = chat.rpc(
                "prompt.submit",
                {"session_id": sid, "text": question},
                timeout=60,
            )
            if submitted.get("error"):
                raise SystemExit(f"prompt.submit error: {submitted['error']}")

            deadline = time.monotonic() + timeout_s
            chunks: list[str] = []
            final = ""
            errors: list[str] = []
            while time.monotonic() < deadline:
                try:
                    raw = chat.ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                except TimeoutError:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                name = _event_name(obj)
                data = _event_payload(obj)
                if name == "message.delta":
                    piece = data.get("text") or data.get("delta") or data.get("content") or ""
                    if piece:
                        chunks.append(str(piece))
                elif name == "message.complete":
                    final = (
                        data.get("text")
                        or data.get("content")
                        or data.get("message")
                        or "".join(chunks)
                        or ""
                    )
                    if isinstance(final, dict):
                        final = str(final.get("content") or final.get("text") or "")
                    final = str(final)
                    break
                elif name in {"error", "message.error", "agent.error"}:
                    errors.append(json.dumps(data)[:400])
                elif name == "session.info" and data.get("error"):
                    errors.append(str(data.get("error"))[:400])
            else:
                raise SystemExit(
                    "TIMEOUT waiting for message.complete; "
                    f"deltas={len(chunks)} errors={errors[:3]} partial={''.join(chunks)[:200]!r}"
                )

            text = (final or "".join(chunks)).strip()
            if not text:
                raise SystemExit(
                    f"EMPTY assistant reply; errors={errors[:3]} raw_complete_len={len(final)}"
                )
            if expect and expect.lower() not in text.lower():
                raise SystemExit(
                    f"EXPECT {expect!r} not in reply {text[:400]!r}"
                )
            return text
        finally:
            chat.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--base-url",
        default=_env("IRIS_CHAT_BASE_URL", default="http://127.0.0.1:8080"),
    )
    p.add_argument(
        "--user",
        default=_env(
            "IRIS_CHAT_USER",
            "HERMES_DASHBOARD_BASIC_AUTH_USERNAME",
        ),
    )
    p.add_argument(
        "--password",
        default=_env(
            "IRIS_CHAT_PASSWORD",
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD",
        ),
    )
    p.add_argument(
        "--question",
        default="",
        help="If empty, asks for a unique token echo",
    )
    p.add_argument("--expect", default="", help="Substring that must appear in reply")
    p.add_argument("--timeout", type=float, default=180.0)
    args = p.parse_args(argv)

    if not args.user or not args.password:
        print("✗ missing username/password env", file=sys.stderr)
        return 2

    token = uuid.uuid4().hex[:8].upper()
    question = args.question or (
        f"Reply with exactly the single token {token} and nothing else. "
        "No punctuation, no explanation."
    )
    expect = args.expect or token

    print(f"→ base={args.base_url} question={question[:80]!r}")
    text = run_chat_turn(
        base_url=args.base_url,
        username=args.user,
        password=args.password,
        question=question,
        expect=expect,
        timeout_s=args.timeout,
    )
    print(f"✓ CHAT_E2E_OK reply={text[:200]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
