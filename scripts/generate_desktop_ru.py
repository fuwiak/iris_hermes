#!/usr/bin/env python3
"""Fast parallel RU generator for desktop i18n (Google via deep_translator)."""

from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1]
EN_JSON = Path("/tmp/desktop-en.json")
CACHE_FILE = Path("/tmp/desktop-ru-cache.json")
OUT = ROOT / "apps/desktop/src/i18n/ru.ts"
WORKERS = 20

KEEP_ENGLISH = [
    "Hermes Desktop", "Hermes Agent", "Hermes Cloud", "Hermes", "Nous Research", "Nous Portal", "Nous",
    "Finder", "File Explorer", "JSON", "OAuth", "Gateway", "API", "MCP", "Discord", "Telegram", "Slack",
    "YOLO", "GitHub", "VS Code", "SSH", "REST", "WebSocket", "IPC", "STT", "TTS", "ElevenLabs", "OpenAI",
    "Groq", "Mistral", "MiniMax", "Gemini", "Fireworks", "OpenRouter", "WhatsApp", "Matrix", "Mattermost",
    "Signal", "BlueBubbles", "QQ", "Email", "Docker", "Modal", "Daytona", "Singularity", "Ollama", "vLLM",
    "llama.cpp", "JetBrains Mono", "Powerlevel10k", "Nerd Fonts", "MesloLGS NF", "SOUL.md", "MEMORY.md",
    "USER.md", "IDEA.md", "SKILL.md", "mcp.json", "desktop.log", "API_SERVER_KEY", "ELEVENLABS_API_KEY",
    "OPENAI_API_KEY", "VOICE_TOOLS_OPENAI_KEY", "HERMES_DESKTOP_REMOTE_URL", "HERMES_DESKTOP_REMOTE_TOKEN",
    "HERMES_HOME", "Ctrl", "Cmd", "Shift", "Enter", "Esc", "macOS", "Windows", "Linux", "WSL", "AppImage",
    "Pro", "stdio", "HTTP", "HTML", "SVG", "PNG", "JPG", "WebP", "GIF", "MIME", "Vite", "React", "YouTube",
    "DevTools", "Kanban", "petdex", "specifier", "gh", "sudo", "ssh-agent", "ssh-add", "ssh-keygen",
    "IdentityFile", "Method Not Allowed", "invalid_api_key", "provider:model", "CommandOrControl", "xterm.js",
    "Ink", "cronjob",
]

MANUAL: dict[str, str] = {
    "Apply": "Применить", "Back": "Назад", "Save": "Сохранить", "Saving…": "Сохранение…",
    "Cancel": "Отмена", "Change": "Изменить", "Choose": "Выбрать", "Clear": "Очистить", "Close": "Закрыть",
    "Collapse": "Свернуть", "Confirm": "Подтвердить", "Connect": "Подключить", "Connecting": "Подключение",
    "Continue": "Продолжить", "Copied": "Скопировано", "Copy": "Копировать", "Copy failed": "Не удалось скопировать",
    "Delete": "Удалить", "Docs": "Документация", "Done": "Готово", "Error": "Ошибка", "Expand": "Развернуть",
    "Failed": "Не удалось", "Format JSON": "Форматировать JSON", "Free": "Бесплатно", "Loading…": "Загрузка…",
    "Not set": "Не задано", "Refresh": "Обновить", "Remove": "Удалить", "Replace": "Заменить", "Retry": "Повторить",
    "Run": "Запустить", "Send": "Отправить", "Set": "Задать", "Skip": "Пропустить", "Update": "Обновить",
    "On": "Вкл.", "Off": "Выкл.", "Prev": "Назад", "Next": "Далее", "Search": "Поиск", "Settings": "Настройки",
    "None": "Нет", "Optional": "Необязательно", "New Chat": "Новый чат", "Chats": "Чаты",
    "How can I help you today?": "Чем могу помочь сегодня?",
}

cache: dict[str, str] = dict(MANUAL)
if CACHE_FILE.exists():
    cache.update(json.loads(CACHE_FILE.read_text(encoding="utf-8")))

_tls_translator: GoogleTranslator | None = None


def get_translator() -> GoogleTranslator:
    global _tls_translator
    if _tls_translator is None:
        _tls_translator = GoogleTranslator(source="en", target="ru")
    return _tls_translator


def save_cache() -> None:
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")


def protect(text: str) -> tuple[str, list[tuple[str, str]]]:
    repl: list[tuple[str, str]] = []
    out = text
    for i, term in enumerate(sorted(set(KEEP_ENGLISH), key=len, reverse=True)):
        if term in out:
            tok = f"__K{i}__"
            out = out.replace(term, tok)
            repl.append((tok, term))
    return out, repl


def restore(text: str, repl: list[tuple[str, str]]) -> str:
    for tok, term in repl:
        text = text.replace(tok, term)
    return text


def translate_one(s: str) -> tuple[str, str]:
    if s in cache:
        return s, cache[s]
    if not s.strip() or re.fullmatch(r"[\W\d_]+", s):
        return s, s
    protected, repl = protect(s)
    for attempt in range(4):
        try:
            ru = get_translator().translate(protected) or s
            return s, restore(ru, repl)
        except Exception:
            time.sleep(0.2 * (attempt + 1))
    return s, s


def collect_strings(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        if "__fn" in obj:
            for m in re.finditer(r"(['\"`])(.*?)\1", obj["__fn"], flags=re.DOTALL):
                content = m.group(2)
                if content.strip():
                    out.add(content)
            return
        for v in obj.values():
            collect_strings(v, out)
    elif isinstance(obj, list):
        for x in obj:
            collect_strings(x, out)
    elif isinstance(obj, str):
        out.add(obj)


def parallel_translate(strings: list[str]) -> None:
    pending = [s for s in strings if s not in cache]
    print(f"Translating {len(pending)} pending of {len(strings)} total with {WORKERS} workers…", file=sys.stderr)
    if not pending:
        return
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(translate_one, s) for s in pending]
        for fut in as_completed(futures):
            src, ru = fut.result()
            cache[src] = ru
            done += 1
            if done % 50 == 0 or done == len(pending):
                save_cache()
                print(f"  {done}/{len(pending)}", file=sys.stderr)


def tr(s: str) -> str:
    return cache.get(s, s)


def ts_string(s: str) -> str:
    val = tr(s)
    if "\n" in val:
        inner = val.replace("\\", "\\\\").replace("'", "\\'")
        return "(\n      '" + inner + "'\n    )"
    return "'" + val.replace("\\", "\\\\").replace("'", "\\'") + "'"


def emit_key(k: str) -> str:
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", k):
        return k
    return json.dumps(k, ensure_ascii=False)


def emit(obj: Any, indent: int) -> str:
    sp = "  " * indent
    if isinstance(obj, dict):
        if "__fn" in obj:
            # Keep English function bodies — nested templates break MT.
            return obj["__fn"]
        if not obj:
            return "{}"
        lines = ["{"]
        for k, v in obj.items():
            lines.append(f"{sp}  {emit_key(k)}: {emit(v, indent + 1)},")
        lines.append(f"{sp}}}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(isinstance(x, str) for x in obj):
            inner = ",\n".join(f"{sp}    {ts_string(x)}" for x in obj)
            return f"[\n{inner}\n{sp}  ]"
        return "[" + ", ".join(emit(x, indent + 1) for x in obj) + "]"
    if isinstance(obj, str):
        return ts_string(obj)
    return json.dumps(obj)


def emit_field_copy(obj: dict[str, Any], indent: int) -> str:
    sp = "  " * indent
    lines = ["defineFieldCopy({"]
    for k, v in obj.items():
        if isinstance(v, dict):
            lines.append(f"{sp}  {emit_key(k)}: {{")
            for sk, sv in v.items():
                if isinstance(sv, dict):
                    lines.append(f"{sp}    {sk}: {{")
                    for tsk, tsv in sv.items():
                        lines.append(f"{sp}      {tsk}: {ts_string(str(tsv))},")
                    lines.append(f"{sp}    }},")
                else:
                    lines.append(f"{sp}    {sk}: {ts_string(str(sv))},")
            lines.append(f"{sp}  }},")
        else:
            lines.append(f"{sp}  {emit_key(k)}: {ts_string(str(v))},")
    lines.append(f"{sp}}})")
    return "\n".join(lines)


def emit_root(data: dict[str, Any], indent: int = 1) -> str:
    sp = "  " * indent
    lines: list[str] = ["{"]
    for k, v in data.items():
        if k == "settings" and isinstance(v, dict):
            lines.append(f"{sp}  settings: {{")
            for sk, sv in v.items():
                if sk == "fieldLabels" and isinstance(sv, dict):
                    lines.append(f"{sp}    fieldLabels: {emit_field_copy(sv, indent + 2)},")
                elif sk == "fieldDescriptions" and isinstance(sv, dict):
                    lines.append(f"{sp}    fieldDescriptions: {emit_field_copy(sv, indent + 2)},")
                else:
                    lines.append(f"{sp}    {sk}: {emit(sv, indent + 2)},")
            lines.append(f"{sp}  }},")
        else:
            lines.append(f"{sp}  {k}: {emit(v, indent + 1)},")
    lines.append(f"{sp}}}")
    return "\n".join(lines)


def main() -> None:
    data: dict[str, Any] = json.loads(EN_JSON.read_text(encoding="utf-8"))
    all_strings: set[str] = set()
    collect_strings(data, all_strings)
    parallel_translate(sorted(all_strings, key=len))
    header = (
        "import { defineFieldCopy } from '@/app/settings/field-copy'\n\n"
        "import { defineLocale } from './define-locale'\n\n"
        "export const ru = defineLocale("
    )
    content = header + emit_root(data) + ")\n"
    OUT.write_text(content, encoding="utf-8")
    save_cache()
    print(f"Wrote {OUT}: {len(content.splitlines())} lines, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
