"""Background mass-send jobs — Рассылки leave the HTTP request thread.

``POST /campaigns/mass-send`` used to be the synchronous ``mark-sent-batch``
loop: with the anti-flood delay a 100-recipient chunk held the request for
minutes and the UI with it. Here the send loop runs in a daemon thread; the
API creates a job, polls its snapshot and can cancel it. Per-recipient rows
record which message went to which client (pending → sending → ok/failed),
so the operator sees delivery per user, not just totals.

One job runs at a time — two overlapping blasts from one Telegram account
only trade bans for speed. Jobs persist as JSON under
``<hermes home>/moysklad/mass_send_jobs/`` so a UI reload (or a process
restart) still finds the last run; a job loaded from disk that never
finished is marked ``interrupted``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from hermes_constants import get_hermes_home

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_CANCEL_FLAGS: dict[str, threading.Event] = {}
_RUNNING_ID: str | None = None

# Terminal recipient states — everything before them may still change.
TERMINAL_STATUSES = ("ok", "failed", "skipped")
_KEEP_JOBS_ON_DISK = 20
_PERSIST_EVERY_ROWS = 10
_PERSIST_MIN_INTERVAL = 2.0

SendOne = Callable[[str], dict[str, Any]]


def _jobs_dir() -> Path:
    root = get_hermes_home() / "moysklad" / "mass_send_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_path(job_id: str) -> Path:
    safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
    return _jobs_dir() / f"{safe}.json"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _persist(job: dict[str, Any]) -> None:
    try:
        path = _job_path(str(job.get("id") or ""))
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        log.warning("mass-send job persist failed", exc_info=True)


def _prune_disk() -> None:
    try:
        files = sorted(
            _jobs_dir().glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[_KEEP_JOBS_ON_DISK:]:
            stale.unlink(missing_ok=True)
    except Exception:
        log.debug("mass-send job prune failed", exc_info=True)


def summary(job: dict[str, Any]) -> dict[str, Any]:
    """Cheap snapshot without the recipient list (safe to poll every 2s)."""
    message = str(job.get("message") or "")
    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "channel": job.get("channel"),
        "via": job.get("via"),
        "deliver": bool(job.get("deliver", True)),
        "total": int(job.get("total") or 0),
        "attempted": int(job.get("attempted") or 0),
        "sent_ok": int(job.get("sent_ok") or 0),
        "sent_failed": int(job.get("sent_failed") or 0),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "cancel_requested": bool(job.get("cancel_requested")),
        "stop_on_error": bool(job.get("stop_on_error")),
        "error": job.get("error"),
        "message_preview": message[:160],
    }


def create_job(
    *,
    message: str,
    client_ids: list[str],
    channel: str = "telegram",
    via: str = "",
    deliver: bool = True,
    stop_on_error: bool = False,
) -> dict[str, Any]:
    """Register a queued job; ``start_job`` actually launches the worker."""
    job_id = f"msj-{uuid.uuid4().hex[:12]}"
    job: dict[str, Any] = {
        "id": job_id,
        "status": "queued",
        "channel": (channel or "telegram").strip().lower(),
        "via": (via or "").strip(),
        "deliver": bool(deliver),
        "stop_on_error": bool(stop_on_error),
        "message": message,
        "total": len(client_ids),
        "attempted": 0,
        "sent_ok": 0,
        "sent_failed": 0,
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "cancel_requested": False,
        "error": None,
        "recipients": [
            {
                "client_id": cid,
                "client_name": "",
                "tg_nick": "",
                "status": "pending",
                "error": None,
                "detail": None,
                "ts": None,
            }
            for cid in client_ids
        ],
    }
    with _LOCK:
        _JOBS[job_id] = job
        _CANCEL_FLAGS[job_id] = threading.Event()
    _persist(job)
    _prune_disk()
    return job


def running_job_id() -> Optional[str]:
    with _LOCK:
        return _RUNNING_ID


def _finish(job: dict[str, Any], status: str, error: str | None = None) -> None:
    global _RUNNING_ID
    with _LOCK:
        job["status"] = status
        job["finished_at"] = _now_iso()
        if error:
            job["error"] = error
        if _RUNNING_ID == job.get("id"):
            _RUNNING_ID = None
        # Persist inside the lock: anyone who observed the terminal status
        # (get_job takes the same lock) must find it on disk too.
        _persist(job)


def _run(job: dict[str, Any], send_one: SendOne, delay: float) -> None:
    job_id = str(job.get("id") or "")
    cancel = _CANCEL_FLAGS.get(job_id) or threading.Event()
    recipients: list[dict[str, Any]] = job.get("recipients") or []
    last_persist = time.time()
    rows_since_persist = 0
    try:
        for index, row in enumerate(recipients):
            if cancel.is_set():
                _finish(job, "cancelled")
                return
            # Anti-flood pause between sends, not before the first one. The
            # wait polls the cancel flag so «Остановить» acts within a second.
            if index and delay > 0 and job.get("deliver", True):
                if cancel.wait(timeout=delay):
                    _finish(job, "cancelled")
                    return
            with _LOCK:
                row["status"] = "sending"
            try:
                result = send_one(str(row.get("client_id") or ""))
            except Exception as exc:  # noqa: BLE001 — one bad row must not kill the run
                log.exception("mass-send job %s recipient failed hard", job_id)
                result = {"ok": False, "error": "exception", "detail": str(exc)}
            with _LOCK:
                row["client_name"] = str(result.get("client_name") or row["client_name"] or "")
                row["tg_nick"] = str(result.get("tg_nick") or row["tg_nick"] or "")
                row["ts"] = _now_iso()
                if result.get("skipped"):
                    row["status"] = "skipped"
                    row["error"] = result.get("error")
                    row["detail"] = result.get("detail")
                elif result.get("ok"):
                    row["status"] = "ok"
                    row["error"] = None
                    row["detail"] = None
                    job["sent_ok"] = int(job.get("sent_ok") or 0) + 1
                else:
                    row["status"] = "failed"
                    row["error"] = str(result.get("error") or "send_failed")
                    row["detail"] = str(result.get("detail") or "") or None
                    job["sent_failed"] = int(job.get("sent_failed") or 0) + 1
                job["attempted"] = index + 1
            rows_since_persist += 1
            if (
                rows_since_persist >= _PERSIST_EVERY_ROWS
                or (time.time() - last_persist) >= _PERSIST_MIN_INTERVAL
            ):
                _persist(job)
                last_persist = time.time()
                rows_since_persist = 0
            if row["status"] == "failed" and job.get("stop_on_error"):
                _finish(job, "done")
                return
        _finish(job, "done")
    except Exception as exc:  # pragma: no cover — worker must never raise
        log.exception("mass-send job %s crashed", job_id)
        _finish(job, "failed", error=str(exc))


def start_job(job_id: str, send_one: SendOne, *, delay: float = 0.0) -> bool:
    """Launch the worker thread; False when the job is unknown or taken."""
    global _RUNNING_ID
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.get("status") != "queued":
            return False
        if _RUNNING_ID is not None:
            return False
        _RUNNING_ID = job_id
        job["status"] = "running"
        job["started_at"] = _now_iso()
    _persist(job)
    thread = threading.Thread(
        target=_run,
        args=(job, send_one, max(0.0, float(delay))),
        name=f"moysklad-mass-send-{job_id}",
        daemon=True,
    )
    thread.start()
    return True


def abort_queued(job_id: str) -> None:
    """Mark a never-started job failed — otherwise a queued row that lost the
    start race would sit in memory/disk as «queued» forever."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job.get("status") == "queued":
            job["status"] = "failed"
            job["error"] = "not_started"
            job["finished_at"] = _now_iso()
            _persist(job)


def cancel_job(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.get(job_id)
        flag = _CANCEL_FLAGS.get(job_id)
        if job is None or flag is None:
            return False
        if job.get("status") not in ("queued", "running"):
            return False
        job["cancel_requested"] = True
        flag.set()
    return True


def _load_from_disk(job_id: str) -> Optional[dict[str, Any]]:
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(job, dict):
        return None
    # Alive only in memory — a disk row still «running» belongs to a dead
    # process and will never finish.
    if job.get("status") in ("queued", "running"):
        job["status"] = "interrupted"
        job["finished_at"] = job.get("finished_at") or _now_iso()
    return job


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        return job
    return _load_from_disk(job_id)


def job_snapshot(
    job_id: str,
    *,
    offset: int = 0,
    limit: int = 500,
    status: str = "all",
) -> Optional[dict[str, Any]]:
    """Summary + a recipient slice. Rows finalize in order, so the UI polls
    from its first non-terminal index and appends."""
    job = get_job(job_id)
    if job is None:
        return None
    with _LOCK:
        recipients = list(job.get("recipients") or [])
        snap = summary(job)
    want = (status or "all").strip().lower()
    if want in ("failed", "ok", "pending", "skipped"):
        rows = [dict(r) for r in recipients if str(r.get("status")) == want]
    else:
        rows = [dict(r) for r in recipients]
    lo = max(0, int(offset))
    hi = lo + max(1, min(int(limit), 2000))
    snap["recipients"] = rows[lo:hi]
    snap["results_offset"] = lo
    snap["results_total"] = len(rows)
    return snap


def latest_job_summary() -> Optional[dict[str, Any]]:
    """Most recent job — memory first, then newest file on disk."""
    with _LOCK:
        if _JOBS:
            job = max(_JOBS.values(), key=lambda j: str(j.get("created_at") or ""))
            return summary(job)
    try:
        files = sorted(
            _jobs_dir().glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return None
    for path in files[:1]:
        job = _load_from_disk(path.stem)
        if job is not None:
            return summary(job)
    return None


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """Send history: newest-first job summaries (memory ∪ disk, deduped).

    Disk keeps the last ``_KEEP_JOBS_ON_DISK`` jobs — that is the history
    depth. Summaries only; per-recipient log stays behind ``job_snapshot``.
    """
    cap = max(1, min(int(limit or 20), _KEEP_JOBS_ON_DISK))
    by_id: dict[str, dict[str, Any]] = {}
    with _LOCK:
        # Newest-inserted first so the stable sort below keeps insertion
        # recency when created_at collides within the same second.
        for job_id, job in reversed(list(_JOBS.items())):
            by_id[str(job_id)] = summary(job)
    try:
        files = sorted(
            _jobs_dir().glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        files = []
    for path in files:
        if path.stem in by_id:
            continue
        job = _load_from_disk(path.stem)
        if job is not None:
            by_id[path.stem] = summary(job)
    rows = sorted(
        by_id.values(),
        key=lambda j: str(j.get("created_at") or ""),
        reverse=True,
    )
    return rows[:cap]


def clear_for_tests() -> None:
    global _RUNNING_ID
    with _LOCK:
        _JOBS.clear()
        _CANCEL_FLAGS.clear()
        _RUNNING_ID = None
