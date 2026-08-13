"""Mass-send background jobs: worker lifecycle, statuses, cancel, snapshots."""

from __future__ import annotations

import threading
import time

import pytest

from plugins.moysklad import mass_send_jobs


@pytest.fixture(autouse=True)
def _clean_jobs():
    mass_send_jobs.clear_for_tests()
    yield
    mass_send_jobs.clear_for_tests()


def _wait_status(job_id: str, statuses: tuple[str, ...], timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = mass_send_jobs.get_job(job_id)
        assert job is not None
        if job.get("status") in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job never reached {statuses}: {job.get('status')}")


def test_job_runs_and_records_per_recipient_status():
    def send_one(client_id: str) -> dict:
        if client_id == "bad":
            return {"ok": False, "error": "chat_not_found", "detail": "нет chat id"}
        return {"ok": True, "client_name": f"Клиент {client_id}", "tg_nick": client_id}

    job = mass_send_jobs.create_job(
        message="Привет!", client_ids=["a", "bad", "c"], channel="telegram"
    )
    assert mass_send_jobs.start_job(job["id"], send_one, delay=0)
    done = _wait_status(job["id"], ("done",))

    assert done["sent_ok"] == 2
    assert done["sent_failed"] == 1
    assert done["attempted"] == 3
    statuses = {r["client_id"]: r["status"] for r in done["recipients"]}
    assert statuses == {"a": "ok", "bad": "failed", "c": "ok"}
    bad = next(r for r in done["recipients"] if r["client_id"] == "bad")
    assert bad["error"] == "chat_not_found"


def test_single_running_job_enforced():
    release = threading.Event()

    def slow_send(_cid: str) -> dict:
        release.wait(timeout=5)
        return {"ok": True}

    first = mass_send_jobs.create_job(message="x", client_ids=["1", "2"])
    assert mass_send_jobs.start_job(first["id"], slow_send, delay=0)
    assert mass_send_jobs.running_job_id() == first["id"]

    second = mass_send_jobs.create_job(message="y", client_ids=["3"])
    assert not mass_send_jobs.start_job(second["id"], slow_send, delay=0)

    release.set()
    _wait_status(first["id"], ("done",))
    assert mass_send_jobs.running_job_id() is None


def test_cancel_stops_between_recipients():
    started = threading.Event()
    block = threading.Event()

    def send_one(_cid: str) -> dict:
        started.set()
        block.wait(timeout=5)
        return {"ok": True}

    job = mass_send_jobs.create_job(message="x", client_ids=["1", "2", "3"])
    assert mass_send_jobs.start_job(job["id"], send_one, delay=0)
    assert started.wait(timeout=5)
    assert mass_send_jobs.cancel_job(job["id"])
    block.set()
    cancelled = _wait_status(job["id"], ("cancelled",))
    # First recipient finished, the rest never started.
    assert cancelled["attempted"] <= 1
    pending = [r for r in cancelled["recipients"] if r["status"] == "pending"]
    assert len(pending) >= 2


def test_snapshot_slices_and_filters():
    def send_one(cid: str) -> dict:
        return {"ok": cid != "b2", "error": None if cid != "b2" else "boom"}

    job = mass_send_jobs.create_job(message="x", client_ids=["a1", "b2", "c3", "d4"])
    assert mass_send_jobs.start_job(job["id"], send_one, delay=0)
    _wait_status(job["id"], ("done",))

    snap = mass_send_jobs.job_snapshot(job["id"], offset=1, limit=2)
    assert snap is not None
    assert [r["client_id"] for r in snap["recipients"]] == ["b2", "c3"]
    assert snap["results_total"] == 4

    failed = mass_send_jobs.job_snapshot(job["id"], status="failed")
    assert [r["client_id"] for r in failed["recipients"]] == ["b2"]


def test_job_persists_and_marks_interrupted_after_restart():
    def send_one(_cid: str) -> dict:
        return {"ok": True}

    job = mass_send_jobs.create_job(message="x", client_ids=["1"])
    assert mass_send_jobs.start_job(job["id"], send_one, delay=0)
    _wait_status(job["id"], ("done",))
    job_id = job["id"]

    # Simulate process restart: memory gone, disk file remains.
    mass_send_jobs.clear_for_tests()
    loaded = mass_send_jobs.get_job(job_id)
    assert loaded is not None
    assert loaded["status"] == "done"

    latest = mass_send_jobs.latest_job_summary()
    assert latest is not None
    assert latest["id"] == job_id


def test_stop_on_error_halts_run():
    calls: list[str] = []

    def send_one(cid: str) -> dict:
        calls.append(cid)
        return {"ok": cid != "2", "error": "boom" if cid == "2" else None}

    job = mass_send_jobs.create_job(
        message="x", client_ids=["1", "2", "3"], stop_on_error=True
    )
    assert mass_send_jobs.start_job(job["id"], send_one, delay=0)
    done = _wait_status(job["id"], ("done",))
    assert calls == ["1", "2"]
    assert done["sent_failed"] == 1
    assert done["recipients"][2]["status"] == "pending"


def test_list_jobs_newest_first_with_summaries(tmp_path, monkeypatch):
    """История отправок: newest-first summaries, no recipient payloads."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from plugins.moysklad import mass_send_jobs as msj

    msj.clear_for_tests()
    first = msj.create_job(
        message="Первая рассылка", channel="telegram", client_ids=["c1"]
    )
    second = msj.create_job(
        message="Вторая рассылка", channel="telegram", client_ids=["c2"]
    )
    jobs = msj.list_jobs()
    ids = [j.get("id") for j in jobs]
    assert ids[0] == second["id"]
    assert first["id"] in ids
    assert all("recipients" not in j for j in jobs)
    assert jobs[0].get("message_preview")
    msj.clear_for_tests()
