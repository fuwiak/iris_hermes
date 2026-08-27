"""MoySklad Clients dashboard plugin — backend API.

Mounted at /api/plugins/moysklad/ by the dashboard plugin system.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Iterator

try:
    from fastapi import APIRouter, HTTPException, Query
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover — unit tests without fastapi
    class APIRouter:  # type: ignore[no-redef]
        def get(self, *_a, **_k):
            return lambda fn: fn

        def post(self, *_a, **_k):
            return lambda fn: fn

        def put(self, *_a, **_k):
            return lambda fn: fn

        def delete(self, *_a, **_k):
            return lambda fn: fn

        def on_event(self, *_a, **_k):
            return lambda fn: fn

    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int = 500, detail: str = ""):
            self.status_code = status_code
            self.detail = detail

    def Query(default=None, **_k):  # type: ignore[misc]
        return default

    class BaseModel:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self) -> dict:
            return dict(self.__dict__)

    def Field(default=None, **_k):  # type: ignore[misc]
        return default

    class StreamingResponse:  # type: ignore[no-redef]
        def __init__(self, content, media_type: str = "application/json", headers=None):
            self.body_iterator = content
            self.media_type = media_type
            self.headers = headers or {}



from plugins.moysklad.assign_groups import (
    propose_groups_for_rows,
    push_merged_tags,
)
from plugins.moysklad.recalculate_groups import (
    assign_to_taxonomy,
    load_taxonomy,
    propose_taxonomy,
    save_taxonomy,
)
from plugins.moysklad.campaigns import (
    create_draft,
    delete_campaign,
    get_seller_settings,
    list_campaigns,
    save_seller_settings,
)
from plugins.moysklad.telegram_send import (
    business_preflight,
    preflight_recipient,
    resolve_peer_identity,
    send_delay_seconds,
    send_outreach_to_client,
    telegram_account_snapshot,
    telegram_send_mode,
    telegram_send_status,
    telegram_user_status,
)
from plugins.platforms.telegram_user import client as tg_user
from plugins.moysklad.outreach_contacts import (
    add_custom_contact,
    delete_custom_contact,
    get_contact,
    list_outreach_contacts,
)
from plugins.moysklad.catalog_cache import (
    PAGE_SNAPSHOT_ROWS,
    cache_backend_name,
    cache_key,
    cache_ttl_seconds,
    extend_page_snapshot,
    format_synced_at,
    get_cached,
    get_dashboard_summary_cached,
    get_page_snapshot,
    invalidate,
    page_snapshot_key,
    partial_cache_key,
    peek_cached,
    refresh_audience_counts,
    set_cached,
    set_dashboard_summary_cached,
    set_page_snapshot,
    slice_page_snapshot,
)
from plugins.moysklad.classify import (
    build_enriched_catalog,
    catalog_integrity,
    clients_page,
)
from plugins.moysklad.groups import ensure_group_options_by_source
from plugins.moysklad.client import MoySkladClient, MoySkladError, token_configured
from plugins.moysklad.ai_playground import (
    get_golden_client,
    list_golden_clients,
    run_playground,
)
from plugins.moysklad.client_card import (
    build_client_detail,
    find_row_in_catalog,
    generate_ai_for_detail,
)
from plugins.moysklad.conversations import (
    append_message,
    enrich_clients,
    get_thread,
    list_awaiting_replies,
    sync_client_conversation,
    sync_telegram_dialogs_into_threads,
)
from plugins.moysklad.outreach import (
    build_outreach_for_row,
    facts_panel,
    generate_outreach_message,
    iter_generate_outreach_for_row_events,
    iter_paraphrase_outreach_events,
    iter_personalize_batch_events,
    iter_rewrite_outreach_events,
    iter_suggest_bouquet_events,
    normalize_seller_fields,
    paraphrase_outreach_message,
    rewrite_outreach_message,
    sanity_check_outreach_message,
    suggest_historical_bouquet_message,
)
from plugins.moysklad.outreach_cache import (
    cache_backend_name as outreach_cache_backend_name,
    get_outreach_draft,
    invalidate_all_outreach_drafts,
    set_outreach_draft,
)
from plugins.moysklad import mass_send_jobs
from plugins.moysklad.ai_fill import (
    cache_backend_name as ai_fill_cache_backend_name,
    fill_empty_for_rows,
)
from plugins.moysklad.client_ai_cache import (
    cache_backend_name as client_ai_cache_backend_name,
    facts_fingerprint as client_ai_facts_fingerprint,
    get_client_ai,
    invalidate_client_ai,
    list_cached_client_ids,
    set_client_ai,
)

log = logging.getLogger(__name__)

router = APIRouter()

_SYNC_LOCK = threading.Lock()
_REVALIDATE_LOCK = threading.Lock()
_REVALIDATE_IN_FLIGHT: set[str] = set()
_SNAPSHOT_REFRESH_LOCK = threading.Lock()
_SNAPSHOT_REFRESH_IN_FLIGHT: set[str] = set()


def _apply_telegram_export_and_recache(
    catalog: dict[str, Any],
    *,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
    force_import: bool = False,
) -> dict[str, Any]:
    """Import/stamp Telegram export onto catalog rows and rewrite durable cache."""
    from plugins.moysklad.telegram_export import (
        cache_backend_name as tg_cache_backend_name,
        ensure_export_imported,
        import_export_into_catalog,
        stamp_catalog_rows_from_overlay,
    )

    rows = list(catalog.get("rows") or [])
    if not rows:
        return {
            "ok": False,
            "error": "catalog_empty",
            "matched": 0,
            "stamped_rows": 0,
            "cache_backend": tg_cache_backend_name(),
        }
    if force_import:
        result = import_export_into_catalog(rows, force=True)
    else:
        result = ensure_export_imported(rows)
    # Always stamp from cache (Redis/file) so warm restarts still fill rows.
    stamped = stamp_catalog_rows_from_overlay(rows)
    catalog["rows"] = rows
    # Rewrite durable cache only when the overlay changed rows — this helper
    # runs on every /clients full-path request, and an unconditional
    # set_cached re-deduped + re-serialized the ~20MB catalog per click.
    changed = bool(
        force_import
        or stamped > 0
        or int((result or {}).get("stamped_rows") or 0) > 0
    )
    # Never persist a partial catalog to the main key — it would clobber the
    # last complete one (partials live on partial_cache_key).
    if changed and not _catalog_is_partial(catalog):
        key = cache_key(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
        )
        try:
            set_cached(key, catalog, synced_at=float(time.time()))
        except Exception:
            log.warning(
                "moysklad catalog recache after tg-export failed", exc_info=True
            )
    out = dict(result or {})
    out["stamped_rows"] = max(int(out.get("stamped_rows") or 0), stamped)
    out["cache_backend"] = out.get("cache_backend") or tg_cache_backend_name()
    return out


def _schedule_snapshot_refresh(
    snap_key: str,
    *,
    catalog: dict[str, Any] | None,
    sales_filter: str,
    group: str,
    q: str,
    group_source: str,
    channel_kind: str,
    require_phone: bool,
    require_telegram: bool,
    vip_only: bool,
    birthday_soon: bool,
    days_before_event: int,
    event_date_from: str,
    event_date_to: str,
    stage: str,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
    entity_type: str = "all",
    loyalty_only: bool = False,
) -> bool:
    """Rebuild first-100 snapshot + telegram export in a daemon thread."""
    if catalog is None or not isinstance(catalog, dict):
        return False
    with _SNAPSHOT_REFRESH_LOCK:
        if snap_key in _SNAPSHOT_REFRESH_IN_FLIGHT:
            return False
        _SNAPSHOT_REFRESH_IN_FLIGHT.add(snap_key)

    def _worker() -> None:
        try:
            try:
                _apply_telegram_export_and_recache(
                    catalog,
                    max_orders=max_orders,
                    max_counterparties=max_counterparties,
                    include_archived=include_archived,
                    force_import=False,
                )
            except Exception:
                log.warning("moysklad telegram export (bg) failed", exc_info=True)
            page = clients_page(
                _client(),
                sales_filter=sales_filter,
                group=group,
                q=q,
                channel_kind=channel_kind,
                require_phone=require_phone,
                require_telegram=require_telegram,
                vip_only=vip_only,
                birthday_soon=birthday_soon,
                group_source=group_source,
                days_before_event=days_before_event,
                event_date_from=event_date_from,
                event_date_to=event_date_to,
                stage=stage,
                entity_type=entity_type,
                loyalty_only=loyalty_only,
                limit=PAGE_SNAPSHOT_ROWS,
                offset=0,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
                catalog=catalog,
            )
            page["clients"] = enrich_clients(list(page.get("clients") or []))
            set_page_snapshot(
                snap_key,
                _strip_internal(page),
                synced_at=float(catalog.get("synced_at") or time.time()),
            )
        except Exception:
            log.warning("moysklad snapshot bg refresh failed", exc_info=True)
        finally:
            with _SNAPSHOT_REFRESH_LOCK:
                _SNAPSHOT_REFRESH_IN_FLIGHT.discard(snap_key)

    threading.Thread(
        target=_worker,
        name=f"moysklad-snap-{snap_key[-12:]}",
        daemon=True,
    ).start()
    return True


def _catalog_meta(
    envelope: dict[str, Any],
    *,
    cached: bool,
    stale: bool = False,
    revalidating: bool = False,
    counts_refreshed: bool = False,
) -> dict[str, Any]:
    synced_at = float(envelope.get("synced_at") or 0)
    return {
        "cached": cached,
        "stale": stale,
        "revalidating": revalidating,
        "counts_refreshed": counts_refreshed,
        "synced_at": synced_at,
        "synced_at_label": format_synced_at(synced_at),
        "cache_ttl_seconds": int(envelope.get("ttl_seconds") or cache_ttl_seconds()),
        "cache_backend": cache_backend_name(),
    }


def _catalog_is_partial(catalog: dict[str, Any] | None) -> bool:
    return bool(isinstance(catalog, dict) and catalog.get("partial"))


def _peek_partial_env(key: str) -> dict[str, Any] | None:
    """Progressive rebuild bytes from the side-channel partial key (if any)."""
    pkey = partial_cache_key(key)
    env = get_cached(pkey) or peek_cached(pkey)
    if env is not None and isinstance(env.get("catalog"), dict):
        return env
    return None


def _envelope_catalog_complete(envelope: dict[str, Any] | None) -> bool:
    if not isinstance(envelope, dict):
        return False
    cat = envelope.get("catalog")
    return isinstance(cat, dict) and not cat.get("partial")


def _rebuild_catalog(
    key: str,
    *,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """MoySklad rebuild with progressive Redis/file flushes (no long lock).

    Counterparties stream into durable cache every ~500 rows so ``/clients``
    load-more can serve partial catalogs while the rest downloads.
    """
    client = _client()

    def _on_partial(partial: dict[str, Any]) -> None:
        # Side-channel key: the last COMPLETE catalog on the main key stays
        # intact, so a restart mid-rebuild serves full (stale) data instantly
        # instead of a 15-of-152 partial.
        try:
            set_cached(partial_cache_key(key), dict(partial), merge=False)
            log.info(
                "moysklad catalog partial flush key=%s rows=%s",
                key[-24:],
                len(partial.get("rows") or []),
            )
        except Exception:
            log.warning("moysklad catalog partial flush failed", exc_info=True)

    catalog = build_enriched_catalog(
        client,
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
        on_partial=_on_partial,
        flush_every=500,
    )
    catalog["partial"] = False
    envelope = set_cached(key, catalog, merge=False)
    try:
        invalidate(partial_cache_key(key))
    except Exception:
        log.debug("moysklad partial key cleanup failed", exc_info=True)
    return catalog, _catalog_meta(envelope, cached=False)


_KEEPWARM_LOCK = threading.Lock()
_KEEPWARM_THREAD: threading.Thread | None = None
_KEEPWARM_BOOT_DELAY_SECONDS = 5.0


def _keepwarm_interval_seconds() -> int:
    """Keep-warm cadence; ``0`` disables the thread entirely."""
    raw = (os.environ.get("MOYSKLAD_CATALOG_KEEPWARM_SECONDS") or "").strip()
    if not raw:
        return 30 * 60
    try:
        value = int(raw)
    except ValueError:
        return 30 * 60
    if value <= 0:
        return 0
    return max(300, value)


def catalog_keepwarm_tick() -> str:
    """One keep-warm pass: rebuild in background unless a fresh full catalog
    is already durable. Returns the action taken (for logs/tests)."""
    if not token_configured():
        return "no-token"
    key = cache_key(max_orders=25000, max_counterparties=0, include_archived=False)
    if _envelope_catalog_complete(get_cached(key)):
        return "fresh"
    scheduled = _schedule_catalog_revalidate(
        key, max_orders=25000, max_counterparties=0, include_archived=False
    )
    return "scheduled" if scheduled else "in-flight"


_TG_DIALOG_SYNC_LOCK = threading.Lock()
_TG_DIALOG_SYNC_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "stats": None,
    "error": None,
}


def _tg_dialog_sync_peers_per_run() -> int:
    """Peers per bulk TG dialog sync run; ``0`` disables the keep-warm hook."""
    raw = (os.environ.get("MOYSKLAD_TG_DIALOG_SYNC_PEERS") or "").strip()
    if not raw:
        return 40
    try:
        return max(0, int(raw))
    except ValueError:
        return 40


def _tg_dialog_sync_worker(max_peers: int) -> None:
    error: str | None = None
    stats: dict[str, Any] | None = None
    try:
        catalog, _meta = _get_catalog(
            force=False, blocking=False, refresh_counts=False
        )
        rows = list((catalog or {}).get("rows") or [])
        if not rows:
            error = "catalog_empty"
        else:
            stats = sync_telegram_dialogs_into_threads(rows, max_peers=max_peers)
            if not stats.get("ok"):
                error = str(stats.get("error") or "sync_failed")
    except Exception as exc:  # pragma: no cover — network path
        log.exception("moysklad telegram dialog sync failed")
        error = str(exc)
    finally:
        with _TG_DIALOG_SYNC_LOCK:
            _TG_DIALOG_SYNC_STATE.update(
                running=False,
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                stats=stats,
                error=error,
            )


def start_telegram_dialog_sync(max_peers: int = 40) -> bool:
    """Single-flight background sync of the operator's TG dialogs into the
    «TG conversation» column. Returns False when a run is already active."""
    with _TG_DIALOG_SYNC_LOCK:
        if _TG_DIALOG_SYNC_STATE.get("running"):
            return False
        _TG_DIALOG_SYNC_STATE.update(
            running=True,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            finished_at=None,
            stats=None,
            error=None,
        )
    threading.Thread(
        target=_tg_dialog_sync_worker,
        args=(max_peers,),
        name="moysklad-tg-dialog-sync",
        daemon=True,
    ).start()
    return True


def _maybe_kick_telegram_dialog_sync() -> None:
    peers = _tg_dialog_sync_peers_per_run()
    if peers <= 0:
        return
    try:
        from plugins.platforms.telegram_user import client as tg_user

        if not tg_user.is_authorized():
            return
    except Exception:
        return
    start_telegram_dialog_sync(max_peers=peers)


# Per-run wall-clock budget for the slow per-nick fallback: without it a
# 400-row live pass could hold the worker for hours behind MTProto waits.
_TG_VERIFY_LIVE_BUDGET_SECONDS = 600.0

_TG_PHONE_VERIFY_LOCK = threading.Lock()
_TG_PHONE_VERIFY_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "stats": None,
    "error": None,
}


def _tg_phone_verify_worker(*, live: bool, limit: int, delay_ms: int) -> None:
    error: str | None = None
    stats: dict[str, Any] = {}
    try:
        from plugins.moysklad.catalog_cache import invalidate_all_page_snapshots
        from plugins.moysklad.tg_verify import (
            mark_active_from_threads,
            match_catalog_phones_to_contacts,
            overlay_for_client,
            reset_inactive_entries,
            row_has_contact_for_tg_check,
            stamp_catalog_rows_from_verify,
            verify_catalog_row,
            verify_rows_by_phone_bulk,
        )

        catalog, _meta = _get_catalog(
            force=False, blocking=True, refresh_counts=False
        )
        rows = list((catalog or {}).get("rows") or [])
        if live:
            # Old runs wrote hard «нет» on privacy misses. Drop them so this
            # pass can mark the numbers New Contact actually sees.
            stats["reset_inactive"] = reset_inactive_entries()
        cache_stats = match_catalog_phones_to_contacts(rows)
        stats["cache"] = cache_stats
        # Live TG threads prove reachability better than any probe.
        stats["from_history"] = mark_active_from_threads(rows)
        stamped = stamp_catalog_rows_from_verify(rows)
        stats["stamped"] = stamped

        # Prefer IRbots Number Checker when keyed — definitive active/inactive
        # for every phone, with by-phone credit cache (100k limit).
        try:
            from plugins.moysklad.irbots_checker import (
                api_key_configured,
                verify_rows_via_irbots,
                write_full_report,
            )

            if api_key_configured():
                with _TG_PHONE_VERIFY_LOCK:
                    _TG_PHONE_VERIFY_STATE["progress"] = {
                        "phase": "irbots",
                        "queued": len(rows),
                        "checked": 0,
                    }
                irbots_stats = verify_rows_via_irbots(
                    rows,
                    only_unchecked=True,
                    force=False,
                )
                stats["irbots"] = irbots_stats
                try:
                    report_path = write_full_report(rows)
                    stats["report_path"] = str(report_path)
                except Exception:
                    log.warning("IRbots status report write failed", exc_info=True)
                with _TG_PHONE_VERIFY_LOCK:
                    _TG_PHONE_VERIFY_STATE["progress"] = {
                        "phase": "irbots_done",
                        "checked": int(irbots_stats.get("written") or 0),
                        "active": int(irbots_stats.get("active") or 0),
                        "inactive": int(irbots_stats.get("inactive") or 0),
                    }
        except Exception as exc:
            log.warning("IRbots phone verify skipped: %s", exc)
            stats["irbots_error"] = str(exc)

        if live:
            remaining = [
                r
                for r in rows
                if isinstance(r, dict)
                and row_has_contact_for_tg_check(r)
                and not overlay_for_client(
                    str(r.get("_moysklad_id") or r.get("id") or "")
                )
            ]
            cap = max(0, int(limit or 0))
            if cap:
                remaining = remaining[:cap]
            stats["live_queued"] = len(remaining)
            with _TG_PHONE_VERIFY_LOCK:
                _TG_PHONE_VERIFY_STATE["progress"] = {
                    "queued": len(remaining),
                    "checked": 0,
                    "phase": "phones",
                }

            # Phones first, in batches: one importContacts per chunk. Probing
            # per client burned the contact-import budget and stalled for
            # hours in FLOOD_WAIT, so «TG активен» stayed almost empty.
            # When IRbots already covered everyone, remaining is empty.
            phone_stats = verify_rows_by_phone_bulk(remaining)
            stats["phones"] = phone_stats
            with _TG_PHONE_VERIFY_LOCK:
                _TG_PHONE_VERIFY_STATE["progress"] = {
                    "queued": len(remaining),
                    "checked": int(phone_stats.get("checked") or 0),
                    "active": int(phone_stats.get("active") or 0),
                    "phase": "nicks",
                }

            # Whatever the phone probe could not settle (no phone, flood wait)
            # falls back to per-row @nick / chat-id resolve.
            leftover = [
                r
                for r in remaining
                if not overlay_for_client(
                    str(r.get("_moysklad_id") or r.get("id") or "")
                )
            ]
            active = inactive = skipped = 0
            delay = max(0, int(delay_ms or 0)) / 1000.0
            deadline = time.time() + _TG_VERIFY_LIVE_BUDGET_SECONDS
            for i, row in enumerate(leftover):
                if time.time() > deadline:
                    stats["live_timeboxed"] = len(leftover) - i
                    break
                result = verify_catalog_row(row)
                if not result.get("checked"):
                    skipped += 1
                elif result.get("active"):
                    active += 1
                else:
                    inactive += 1
                if (i + 1) % 10 == 0:
                    with _TG_PHONE_VERIFY_LOCK:
                        _TG_PHONE_VERIFY_STATE["progress"] = {
                            "queued": len(leftover),
                            "checked": i + 1,
                            "active": active,
                            "phase": "nicks",
                        }
                if delay and i + 1 < len(leftover):
                    time.sleep(delay)
            stamp_catalog_rows_from_verify(rows)
            stats["live"] = {
                "queued": len(leftover),
                "active": active,
                "inactive": inactive,
                "skipped": skipped,
            }
        try:
            stats["snapshots_cleared"] = invalidate_all_page_snapshots()
        except Exception:
            log.warning("page snapshot invalidate after tg-verify failed", exc_info=True)
        with _TG_PHONE_VERIFY_LOCK:
            _TG_PHONE_VERIFY_STATE["stats"] = stats
    except Exception as exc:
        log.exception("moysklad telegram phone verify failed")
        error = str(exc)
    finally:
        with _TG_PHONE_VERIFY_LOCK:
            _TG_PHONE_VERIFY_STATE.update(
                running=False,
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                error=error,
            )


def start_telegram_phone_verify(
    *, live: bool = True, limit: int = 400, delay_ms: int = 400
) -> bool:
    with _TG_PHONE_VERIFY_LOCK:
        if _TG_PHONE_VERIFY_STATE.get("running"):
            return False
        _TG_PHONE_VERIFY_STATE.update(
            running=True,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            finished_at=None,
            stats=None,
            error=None,
        )
    threading.Thread(
        target=_tg_phone_verify_worker,
        kwargs={"live": live, "limit": limit, "delay_ms": delay_ms},
        name="moysklad-tg-phone-verify",
        daemon=True,
    ).start()
    return True


def _tg_verify_auto_enabled() -> bool:
    raw = (os.environ.get("MOYSKLAD_TG_VERIFY_AUTO") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _maybe_kick_telegram_phone_verify() -> None:
    """Обработчик «каждый клиент проверен на ТГ»: every keep-warm tick verifies
    the next slice of unchecked phones so the «Telegram» filter converges.

    Prefer IRbots when ``IRBOTS_API_KEY`` is set (definitive + phone credit
    cache). Else fall back to batched importContacts (needs user session).
    """
    if not _tg_verify_auto_enabled():
        return
    try:
        from plugins.moysklad.irbots_checker import api_key_configured

        if api_key_configured():
            # Full pass is cheap when phone cache is warm; limit=0 = all misses.
            start_telegram_phone_verify(live=True, limit=0, delay_ms=400)
            return
    except Exception:
        pass
    try:
        from plugins.platforms.telegram_user import client as tg_user

        if not tg_user.is_authorized():
            return
    except Exception:
        return
    start_telegram_phone_verify(live=True, limit=400, delay_ms=400)


def _catalog_keepwarm_loop() -> None:
    # Let the web server finish booting before the first MoySklad pull.
    time.sleep(_KEEPWARM_BOOT_DELAY_SECONDS)
    while True:
        try:
            action = catalog_keepwarm_tick()
            if action != "fresh":
                log.info("moysklad catalog keepwarm: %s", action)
        except Exception:
            log.warning("moysklad catalog keepwarm tick failed", exc_info=True)
        try:
            # Personal-TG dialogs → «TG conversation» column, same cadence.
            _maybe_kick_telegram_dialog_sync()
        except Exception:
            log.warning("moysklad tg dialog keepwarm kick failed", exc_info=True)
        try:
            # «TG активен» for every client, slice per tick, flood-safe.
            _maybe_kick_telegram_phone_verify()
        except Exception:
            log.warning("moysklad tg verify keepwarm kick failed", exc_info=True)
        interval = _keepwarm_interval_seconds()
        if interval <= 0:
            return
        time.sleep(interval)


def start_catalog_keepwarm() -> bool:
    """Boot-time warmer + periodic refresher (no Celery needed: daemon thread
    + the existing Redis/file cache). Post-deploy restarts rebuild the catalog
    BEFORE the first operator click instead of during it."""
    global _KEEPWARM_THREAD
    if _keepwarm_interval_seconds() <= 0:
        return False
    with _KEEPWARM_LOCK:
        if _KEEPWARM_THREAD is not None and _KEEPWARM_THREAD.is_alive():
            return False
        _KEEPWARM_THREAD = threading.Thread(
            target=_catalog_keepwarm_loop,
            name="moysklad-catalog-keepwarm",
            daemon=True,
        )
        _KEEPWARM_THREAD.start()
    return True


@router.on_event("startup")
def _keepwarm_on_startup() -> None:  # pragma: no cover — exercised by the app
    # Runs only when the dashboard app actually starts (never on test import).
    start_catalog_keepwarm()


def _schedule_catalog_revalidate(
    key: str,
    *,
    max_orders: int,
    max_counterparties: int,
    include_archived: bool,
) -> bool:
    """Kick a single background rebuild for ``key``. Returns True if scheduled."""
    with _REVALIDATE_LOCK:
        if key in _REVALIDATE_IN_FLIGHT:
            return False
        # Complete catalog already durable — skip. Partial must continue.
        if _envelope_catalog_complete(get_cached(key)):
            return False
        _REVALIDATE_IN_FLIGHT.add(key)

    def _worker() -> None:
        try:
            # Do NOT hold _SYNC_LOCK across MoySklad I/O — readers need
            # progressive Redis flushes for load-more.
            fresh = get_cached(key)
            if _envelope_catalog_complete(fresh):
                return
            _rebuild_catalog(
                key,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
            )
        except Exception:
            log.exception("moysklad catalog background revalidate failed key=%s", key)
        finally:
            with _REVALIDATE_LOCK:
                _REVALIDATE_IN_FLIGHT.discard(key)

    threading.Thread(
        target=_worker,
        name=f"moysklad-revalidate-{key[-12:]}",
        daemon=True,
    ).start()
    return True


def _get_catalog(
    *,
    max_orders: int = 25000,
    max_counterparties: int = 0,
    include_archived: bool = False,
    force: bool = False,
    blocking: bool = True,
    refresh_counts: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return ``(catalog, meta)`` where meta has sync/cache fields.

    CDN-style stale-while-revalidate:
    - Fresh durable cache → serve (no MoySklad)
    - Expired but peekable → serve stale + background refresh
    - Missing + ``blocking=False`` → ``(None, meta)`` + background refresh
    - Missing / ``force=True`` + ``blocking=True`` → blocking rebuild
    """
    key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    if not force:
        envelope = get_cached(key)
        if envelope is not None:
            catalog = envelope["catalog"]
            if _catalog_is_partial(catalog):
                scheduled = _schedule_catalog_revalidate(
                    key,
                    max_orders=max_orders,
                    max_counterparties=max_counterparties,
                    include_archived=include_archived,
                )
                return catalog, _catalog_meta(
                    envelope,
                    cached=True,
                    stale=True,
                    revalidating=scheduled or key in _REVALIDATE_IN_FLIGHT,
                    counts_refreshed=False,
                )
            synced_at = float(envelope.get("synced_at") or 0)
            counts_rewritten = False
            if refresh_counts:
                catalog, counts_rewritten = _refresh_cached_catalog_counts(
                    key, catalog, synced_at=synced_at
                )
            return catalog, _catalog_meta(
                envelope, cached=True, counts_refreshed=counts_rewritten
            )

        # Logical TTL expired — still serve durable bytes if present.
        stale_env = peek_cached(key)
        if stale_env is not None and isinstance(stale_env.get("catalog"), dict):
            catalog = stale_env["catalog"]
            # Skip O(n) counts rewrite on stale path — keep first paint fast.
            scheduled = _schedule_catalog_revalidate(
                key,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
            )
            return catalog, _catalog_meta(
                stale_env,
                cached=True,
                stale=True,
                revalidating=scheduled or key in _REVALIDATE_IN_FLIGHT,
                counts_refreshed=False,
            )

        if not blocking:
            scheduled = _schedule_catalog_revalidate(
                key,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
            )
            # No full catalog at all — progressive rebuild bytes are better
            # than nothing for scroll/load-more.
            partial_env = _peek_partial_env(key)
            if partial_env is not None:
                return partial_env["catalog"], _catalog_meta(
                    partial_env,
                    cached=True,
                    stale=True,
                    revalidating=scheduled or key in _REVALIDATE_IN_FLIGHT,
                    counts_refreshed=False,
                )
            return None, {
                "cached": False,
                "stale": True,
                "revalidating": scheduled or key in _REVALIDATE_IN_FLIGHT,
                "counts_refreshed": False,
                "synced_at": 0.0,
                "synced_at_label": "",
                "cache_ttl_seconds": cache_ttl_seconds(),
                "cache_backend": cache_backend_name(),
                "snapshot": False,
            }

    with _SYNC_LOCK:
        # Another request may have filled the cache while we waited.
        if not force:
            envelope = get_cached(key)
            if envelope is not None and _envelope_catalog_complete(envelope):
                catalog = envelope["catalog"]
                synced_at = float(envelope.get("synced_at") or 0)
                counts_rewritten = False
                if refresh_counts:
                    catalog, counts_rewritten = _refresh_cached_catalog_counts(
                        key, catalog, synced_at=synced_at
                    )
                return catalog, _catalog_meta(
                    envelope, cached=True, counts_refreshed=counts_rewritten
                )
            stale_env = peek_cached(key)
            if stale_env is not None and isinstance(stale_env.get("catalog"), dict):
                catalog = stale_env["catalog"]
                if _catalog_is_partial(catalog) and not blocking:
                    scheduled = _schedule_catalog_revalidate(
                        key,
                        max_orders=max_orders,
                        max_counterparties=max_counterparties,
                        include_archived=include_archived,
                    )
                    return catalog, _catalog_meta(
                        stale_env,
                        cached=True,
                        stale=True,
                        revalidating=scheduled or key in _REVALIDATE_IN_FLIGHT,
                        counts_refreshed=False,
                    )
                if not _catalog_is_partial(catalog):
                    scheduled = _schedule_catalog_revalidate(
                        key,
                        max_orders=max_orders,
                        max_counterparties=max_counterparties,
                        include_archived=include_archived,
                    )
                    return catalog, _catalog_meta(
                        stale_env,
                        cached=True,
                        stale=True,
                        revalidating=scheduled or key in _REVALIDATE_IN_FLIGHT,
                        counts_refreshed=False,
                    )
            if not blocking:
                scheduled = _schedule_catalog_revalidate(
                    key,
                    max_orders=max_orders,
                    max_counterparties=max_counterparties,
                    include_archived=include_archived,
                )
                # Prefer any bytes already flushed — full first, then the
                # progressive partial side-channel.
                partial_env = get_cached(key) or peek_cached(key)
                if partial_env is None or not isinstance(
                    partial_env.get("catalog"), dict
                ):
                    partial_env = _peek_partial_env(key)
                if partial_env is not None and isinstance(
                    partial_env.get("catalog"), dict
                ):
                    return partial_env["catalog"], _catalog_meta(
                        partial_env,
                        cached=True,
                        stale=True,
                        revalidating=scheduled or key in _REVALIDATE_IN_FLIGHT,
                        counts_refreshed=False,
                    )
                return None, {
                    "cached": False,
                    "stale": True,
                    "revalidating": scheduled or key in _REVALIDATE_IN_FLIGHT,
                    "counts_refreshed": False,
                    "synced_at": 0.0,
                    "synced_at_label": "",
                    "cache_ttl_seconds": cache_ttl_seconds(),
                    "cache_backend": cache_backend_name(),
                    "snapshot": False,
                }

        # Only one blocking rebuild at a time; release lock before MoySklad I/O
        # by scheduling when another rebuild is already in flight.
        if key in _REVALIDATE_IN_FLIGHT:
            partial_env = get_cached(key) or peek_cached(key)
            if partial_env is None or not isinstance(partial_env.get("catalog"), dict):
                partial_env = _peek_partial_env(key)
            if partial_env is not None and isinstance(partial_env.get("catalog"), dict):
                return partial_env["catalog"], _catalog_meta(
                    partial_env,
                    cached=True,
                    stale=True,
                    revalidating=True,
                    counts_refreshed=False,
                )

    # Rebuild outside the brief decision lock so progressive flushes are readable.
    with _REVALIDATE_LOCK:
        already = key in _REVALIDATE_IN_FLIGHT
        if not already:
            _REVALIDATE_IN_FLIGHT.add(key)
    try:
        if already:
            # Wait briefly for progressive bytes rather than a second MoySklad pull.
            for _ in range(40):
                time.sleep(0.25)
                env = get_cached(key)
                if env is None or not isinstance(env.get("catalog"), dict):
                    env = _peek_partial_env(key)
                if env is not None and isinstance(env.get("catalog"), dict):
                    return env["catalog"], _catalog_meta(
                        env,
                        cached=True,
                        stale=_catalog_is_partial(env.get("catalog")),
                        revalidating=key in _REVALIDATE_IN_FLIGHT,
                        counts_refreshed=False,
                    )
            raise HTTPException(
                status_code=503,
                detail="catalog rebuilding; retry shortly",
            )
        return _rebuild_catalog(
            key,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
        )
    finally:
        if not already:
            with _REVALIDATE_LOCK:
                _REVALIDATE_IN_FLIGHT.discard(key)


class AiFillBody(BaseModel):
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    ids: list[str] = Field(default_factory=list)
    limit: int = 100
    use_llm: bool = True
    force: bool = False
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class AssignBody(BaseModel):
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    ids: list[str] = Field(default_factory=list)
    dry_run: bool = True
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class PushBody(BaseModel):
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    only_changed: bool = True


class StageTagBody(BaseModel):
    """Mark «не состоялся» clients with a MoySklad tag. Dry-run by default."""

    sales_filter: str = "all"
    q: str = ""
    ids: list[str] = Field(default_factory=list)
    dry_run: bool = True
    tag: str = ""
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class RecalculateProposeBody(BaseModel):
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    group_source: str = "any"
    days_before_event: int = 0
    event_date_from: str = ""
    event_date_to: str = ""
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class RecalculateApplyBody(BaseModel):
    groups: list[str] = Field(default_factory=list)
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    group_source: str = "any"
    days_before_event: int = 0
    event_date_from: str = ""
    event_date_to: str = ""
    dry_run: bool = True
    push: bool = False
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class CampaignCreateBody(BaseModel):
    title: str = "Рассылка"
    channel: str = "telegram"
    mode: str = "manual"
    offer: str = ""
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    group_source: str = "any"
    days_before_event: int = 0
    event_date_from: str = ""
    event_date_to: str = ""
    last_contact_from: str = ""
    last_contact_to: str = ""
    personalize: bool = False
    client_id: str = ""
    include_preview: bool = True
    generate_ai: bool = False
    seller_name: str = ""
    seller_facts: str = ""
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachGenerateBody(BaseModel):
    client_id: str = ""
    channel: str = "telegram"
    refresh_ai: bool = True
    seller_name: str = ""
    seller_facts: str = ""
    provider: str = ""
    model: str = ""
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachRewriteBody(BaseModel):
    message: str = ""
    channel: str = "telegram"
    client_id: str = ""
    seller_name: str = ""
    seller_facts: str = ""
    provider: str = ""
    model: str = ""
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachSanityBody(BaseModel):
    message: str = ""
    channel: str = "telegram"
    client_id: str = ""
    seller_name: str = ""
    seller_facts: str = ""
    apply_revision: bool = True
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachPersonalizeBody(BaseModel):
    """Batch personalize for current audience filters (parallel LLM)."""

    channel: str = "telegram"
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    group_source: str = "any"
    days_before_event: int = 0
    event_date_from: str = ""
    event_date_to: str = ""
    seller_name: str = ""
    seller_facts: str = ""
    provider: str = ""
    model: str = ""
    limit: int = 20
    max_workers: int = 3
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class ConversationAppendBody(BaseModel):
    text: str = ""
    direction: str = "outbound"
    channel: str = "telegram"
    label: str = ""
    source: str = "manual"
    open_deep_link: bool = False
    #: Optional photo (data-URL or bare base64) — delivered via sendPhoto.
    image_base64: str = ""
    image_name: str = "photo.jpg"
    #: Or a remote photo URL (e.g. marketplace card image) — Telegram fetches it.
    image_url: str = ""


class ConversationSyncBody(BaseModel):
    """Optional overrides for Sync Telegram (+ AI refresh)."""

    refresh_ai: bool = True
    provider: str = ""
    model: str = ""


class ClientAiBody(BaseModel):
    """Optional model override for client-card summary / recommendation."""

    provider: str = ""
    model: str = ""
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


class OutreachChatBody(BaseModel):
    """One chat turn refining the outreach draft."""

    client_id: str = ""
    channel: str = "telegram"
    draft: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    seller_name: str = ""
    seller_facts: str = ""


class SkillSaveBody(BaseModel):
    text: str
    notes: str = ""


class MarkSentBody(BaseModel):
    message: str = ""
    channel: str = "telegram"
    client_id: str = ""
    open_deep_link: bool = True
    # When true (default for telegram), attempt Bot API send via Business bot.
    deliver: bool = True
    #: ``bot`` | ``user`` | ``auto`` — overrides MOYSKLAD_TELEGRAM_SEND_VIA.
    via: str = ""
    #: Optional photo (data-URL or bare base64) — delivered via sendPhoto.
    image_base64: str = ""
    image_name: str = "photo.jpg"
    #: Or a remote photo URL (e.g. marketplace card image) — Telegram fetches it.
    image_url: str = ""


# Per HTTP request — UI chunks larger audiences into several calls.
MASS_SEND_BATCH_MAX = 100
MASS_AUDIENCE_IDS_MAX = 10_000


class MarkSentBatchBody(BaseModel):
    """Send one draft to many audience clients (same text, sequential deliver)."""

    message: str = ""
    channel: str = "telegram"
    client_ids: list[str] = Field(default_factory=list)
    open_deep_link: bool = False
    deliver: bool = True
    #: ``bot`` | ``user`` | ``auto`` — overrides MOYSKLAD_TELEGRAM_SEND_VIA.
    via: str = ""
    #: Stop the batch as soon as one send fails (default: keep going).
    stop_on_error: bool = False


class CollectRepliesBody(BaseModel):
    """Pull inbound replies for a mass-send cohort (TG conversation inbox)."""

    client_ids: list[str] = Field(default_factory=list)
    #: Live-sync gateway + personal MTProto before listing (default on).
    sync: bool = True
    limit: int = 200


class MassSendStartBody(BaseModel):
    """Start a background mass send — the whole audience in one job."""

    message: str = ""
    channel: str = "telegram"
    client_ids: list[str] = Field(default_factory=list)
    deliver: bool = True
    #: ``bot`` | ``user`` | ``auto`` — overrides MOYSKLAD_TELEGRAM_SEND_VIA.
    via: str = ""
    #: Stop the job as soon as one send fails (default: keep going).
    stop_on_error: bool = False
    #: Optional photo (data-URL or bare base64) attached to every message.
    image_base64: str = ""
    image_name: str = "photo.jpg"
    #: Or a remote photo URL (e.g. marketplace card image) — Telegram fetches it.
    image_url: str = ""


class OutreachContactBody(BaseModel):
    name: str = ""
    tg_nick: str = ""
    tg_chat_id: str = ""
    # Free-form: @nick, t.me/…, or numeric id — resolved via Bot API getChat.
    query: str = ""
    resolve: bool = True


class OutreachResolveBody(BaseModel):
    query: str = ""
    tg_nick: str = ""
    tg_chat_id: str = ""


class TelegramUserLoginBody(BaseModel):
    """Personal-account login step 1 — phone (+ optional my.telegram.org app)."""

    phone: str = ""
    api_id: str = ""
    api_hash: str = ""
    force_sms: bool = False


class TelegramUserCodeBody(BaseModel):
    code: str = ""


class TelegramUserPasswordBody(BaseModel):
    password: str = ""


class TelegramUserSessionBody(BaseModel):
    """Paste a Telethon StringSession when the VDS cannot reach Telegram DCs."""

    session: str = ""
    phone: str = ""


class SellerSettingsBody(BaseModel):
    seller_name: str = ""
    seller_facts: str = ""
    # None = leave unchanged; "" = clear stored business connection id.
    telegram_business_connection_id: str | None = None


class PlaygroundRunBody(BaseModel):
    """AI playground: golden client id and/or editable facts JSON."""

    client_id: str = ""
    input_json: str = ""
    run_llm: bool = False


class OutreachDraftCacheBody(BaseModel):
    client_id: str = ""
    channel: str = "telegram"
    message: str = ""
    grounding_notes: str = ""
    source: str = ""
    status: str = ""
    client_name: str = ""
    title: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    sanity: dict[str, Any] | None = None


def _resolve_seller(body_name: str = "", body_facts: str = "") -> tuple[str, str]:
    """Body fields win; empty body falls back to persisted shop settings."""
    stored = get_seller_settings()
    name = (body_name or "").strip() or stored.get("seller_name") or ""
    facts = (body_facts or "").strip() or stored.get("seller_facts") or ""
    return normalize_seller_fields(name, facts)


def _ndjson_lines(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    """Serialize outreach stream events as NDJSON (skip internal frames)."""
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "_raw":
            continue
        # Drop non-JSON-serializable accidental keys
        safe = {
            k: v
            for k, v in ev.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }
        yield json.dumps(safe, ensure_ascii=False) + "\n"


def _ndjson_response(events: Iterator[dict[str, Any]]) -> Any:
    return StreamingResponse(
        _ndjson_lines(events),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _persist_outreach_draft_from_done(
    ev: dict[str, Any],
    *,
    client_id: str,
    channel: str,
    status: str = "",
) -> dict[str, Any]:
    """Write Redis/file draft cache when a stream finishes with a message."""
    cid = (client_id or "").strip() or str(ev.get("client_id") or "").strip()
    msg = str(ev.get("message") or "").strip()
    if not cid or not msg:
        return ev
    try:
        set_outreach_draft(
            cid,
            channel or str(ev.get("channel") or "telegram"),
            {
                "message": msg,
                "grounding_notes": ev.get("grounding_notes") or "",
                "source": ev.get("source") or "",
                "status": status
                or "AI сгенерировал креативный текст — можно править вручную.",
                "client_name": ev.get("client_name") or "",
                "title": (
                    f"Черновик · {ev['client_name']}"
                    if ev.get("client_name")
                    else ""
                ),
                "facts": ev.get("facts") if isinstance(ev.get("facts"), dict) else {},
                "sanity": ev.get("sanity") if isinstance(ev.get("sanity"), dict) else None,
            },
        )
        return {
            **ev,
            "cached": True,
            "cache_backend": outreach_cache_backend_name(),
        }
    except Exception as exc:  # pragma: no cover
        log.warning("moysklad outreach draft cache write failed: %s", exc)
        return {**ev, "cached": False, "cache_error": str(exc)}


def _client() -> MoySkladClient:
    if not token_configured():
        raise HTTPException(
            status_code=503,
            detail="MOYSKLAD_API_TOKEN missing. Add it to ~/.hermes/.env.",
        )
    try:
        return MoySkladClient()
    except MoySkladError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _refresh_cached_catalog_counts(
    key: str,
    catalog: dict[str, Any],
    *,
    synced_at: float,
) -> tuple[dict[str, Any], bool]:
    """Recompute tab counts on cache hit; persist when they changed."""
    if not isinstance(catalog, dict):
        return catalog, False
    before = dict(catalog.get("counts") or {})
    after = refresh_audience_counts(catalog)
    if before == after:
        return catalog, False
    try:
        set_cached(key, catalog, synced_at=synced_at or time.time())
    except Exception:
        log.warning("moysklad cache counts rewrite failed", exc_info=True)
        return catalog, True
    return catalog, True


def _invalidate_cache(
    *,
    max_orders: int = 25000,
    max_counterparties: int = 0,
    include_archived: bool = False,
) -> None:
    invalidate(
        cache_key(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
        )
    )


def _is_contact_id(client_id: str) -> bool:
    """Outreach-only peer (``custom:…`` / ``tg:…``) with no MoySklad catalog row."""
    return str(client_id or "").startswith(("custom:", "tg:"))


def _outreach_target_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """TG coordinates from a catalog row — no ``build_client_detail`` (no MoySklad I/O).

    ``build_client_detail`` pulls order compositions from MoySklad and routinely
    burns 15–60s; mark-sent / mass-send only need phone + nick + chat id, so
    the send path stays under the client timeout and actually delivers.
    """
    from plugins.moysklad.client_card import messaging_links

    phone = str(row.get("Телефон") or row.get("phone") or "")
    tg_nick = str(row.get("ТГ ник") or row.get("tg_nick") or "")
    tg_conversation = str(
        row.get("TG conversation") or row.get("tg_conversation") or ""
    )
    tg_chat_id = str(
        row.get("ТГ chat id") or row.get("tg_chat_id") or ""
    )
    client_name = str(row.get("Наименование") or row.get("name") or "")
    msg = messaging_links(
        phone=phone, tg_nick=tg_nick, tg_conversation=tg_conversation
    )
    return {
        "phone": phone,
        "tg_nick": tg_nick,
        "tg_conversation": tg_conversation,
        "tg_chat_id": tg_chat_id,
        "client_name": client_name,
        "telegram_url": str(msg.get("telegram_url") or ""),
        "whatsapp_url": str(msg.get("whatsapp_url") or ""),
    }


def _contact_row(client_id: str) -> dict[str, Any] | None:
    """Synthesize a catalog-shaped row for a peer that has no MoySklad card.

    Personal Telegram / custom contacts have a name and a TG handle and no
    order history — enough for the card, AI draft and send paths to run
    instead of 404-ing the whole Рассылки flow.
    """
    contact = get_contact(client_id)
    if contact is None:
        return None
    return {
        "_moysklad_id": client_id,
        "Наименование": str(contact.get("name") or contact.get("label") or ""),
        "ТГ ник": str(contact.get("tg_nick") or ""),
        "ТГ chat id": str(contact.get("tg_chat_id") or ""),
        "_orders_context": [],
        "_contact_only": True,
    }


def _row_or_contact(catalog: Any, client_id: str) -> dict[str, Any] | None:
    """Catalog row, falling back to a contact-only row for outreach peers."""
    row = find_row_in_catalog(catalog, client_id)
    if row is not None:
        return row
    return _contact_row(client_id)


def _strip_internal(page: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in page.items() if not k.startswith("_")}


def _attach_cache_meta(out: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    out.update(meta)
    return out


@router.get("/health")
def health() -> dict[str, Any]:
    if not token_configured():
        return {"ok": False, "error": "MOYSKLAD_API_TOKEN missing"}
    try:
        return MoySkladClient().health()
    except MoySkladError as exc:
        return {"ok": False, "error": str(exc), "status_code": exc.status_code}


@router.get("/clients")
def get_clients(
    sales_filter: str = Query("all"),
    group: str = Query(""),
    q: str = Query(""),
    channel_kind: str = Query(""),
    require_phone: bool = Query(False),
    require_telegram: bool = Query(False),
    vip_only: bool = Query(False),
    birthday_soon: bool = Query(False),
    group_source: str = Query("any"),
    days_before_event: int = Query(0, ge=0, le=365),
    event_date_from: str = Query(""),
    event_date_to: str = Query(""),
    last_contact_from: str = Query(""),
    last_contact_to: str = Query(""),
    stage: str = Query("all"),
    entity_type: str = Query("all"),
    loyalty_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    max_orders: int = Query(25000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    snap_key = page_snapshot_key(
        sales_filter=sales_filter,
        group=group,
        q=q,
        group_source=group_source,
        channel_kind=channel_kind,
        require_phone=require_phone,
        require_telegram=require_telegram,
        vip_only=vip_only,
        birthday_soon=birthday_soon,
        days_before_event=days_before_event,
        event_date_from=event_date_from,
        event_date_to=event_date_to,
        stage=stage,
        entity_type=entity_type,
        loyalty_only=loyalty_only,
    )
    catalog_key = cache_key(
        max_orders=max_orders,
        max_counterparties=max_counterparties,
        include_archived=include_archived,
    )
    try:
        want_fast = (not refresh) and offset == 0
        # Calendar / lead-window filters must recompute against live order dates —
        # empty poisoned snapshots (pre-literal-order) stuck as «Клиенты · 0».
        calendar_filter_active = bool(
            (event_date_from or "").strip()
            or (event_date_to or "").strip()
            or (last_contact_from or "").strip()
            or (last_contact_to or "").strip()
            or int(days_before_event or 0) > 0
        )

        # Instant paint / scroll: serve Redis/file page window when it covers
        # this offset (grows via extend_page_snapshot after each successful page).
        if not refresh and not calendar_filter_active:
            snap_env = get_page_snapshot(snap_key)
            if snap_env is not None:
                sliced = slice_page_snapshot(
                    snap_env, limit=limit, offset=offset
                )
                if sliced is not None:
                    try:
                        sliced["clients"] = enrich_clients(
                            list(sliced.get("clients") or [])
                        )
                    except Exception:
                        log.debug("snapshot re-enrich failed", exc_info=True)
                    fresh = get_cached(catalog_key)
                    cat = (
                        fresh.get("catalog")
                        if isinstance(fresh, dict)
                        else None
                    )
                    revalidating = False
                    if fresh is None or _catalog_is_partial(
                        cat if isinstance(cat, dict) else None
                    ):
                        revalidating = _schedule_catalog_revalidate(
                            catalog_key,
                            max_orders=max_orders,
                            max_counterparties=max_counterparties,
                            include_archived=include_archived,
                        ) or catalog_key in _REVALIDATE_IN_FLIGHT
                    else:
                        revalidating = catalog_key in _REVALIDATE_IN_FLIGHT
                        if offset == 0:
                            _schedule_snapshot_refresh(
                                snap_key,
                                catalog=cat if isinstance(cat, dict) else None,
                                sales_filter=sales_filter,
                                group=group,
                                q=q,
                                group_source=group_source,
                                channel_kind=channel_kind,
                                require_phone=require_phone,
                                require_telegram=require_telegram,
                                vip_only=vip_only,
                                birthday_soon=birthday_soon,
                                days_before_event=days_before_event,
                                event_date_from=event_date_from,
                                event_date_to=event_date_to,
                                stage=stage,
                                entity_type=entity_type,
                                loyalty_only=loyalty_only,
                                max_orders=max_orders,
                                max_counterparties=max_counterparties,
                                include_archived=include_archived,
                            )
                    out_meta = {
                        "cached": True,
                        "stale": bool(
                            revalidating
                            or fresh is None
                            or _catalog_is_partial(
                                cat if isinstance(cat, dict) else None
                            )
                        ),
                        "revalidating": bool(revalidating),
                        "snapshot": True,
                        "synced_at": float(snap_env.get("synced_at") or 0),
                        "synced_at_label": format_synced_at(
                            float(snap_env.get("synced_at") or 0)
                        ),
                        "cache_ttl_seconds": cache_ttl_seconds(),
                        "cache_backend": cache_backend_name(),
                        "counts_refreshed": False,
                    }
                    return _attach_cache_meta(
                        ensure_group_options_by_source(_strip_internal(sliced)),
                        out_meta,
                    )

        # offset>0 must NOT block on MoySklad — serve partial Redis / retry.
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=refresh,
            blocking=False if offset > 0 else (not want_fast),
            refresh_counts=False if offset > 0 else (not want_fast),
        )

        # Explicit Sync (?refresh=true) drops stale DeepSeek summaries so the
        # next card open regenerates from fresh order facts — not old cache.
        if refresh and offset == 0:
            try:
                invalidate_client_ai("")
                invalidate_all_outreach_drafts()
            except Exception:
                log.debug("AI cache clear on clients refresh failed", exc_info=True)

        if catalog is None and want_fast:
            # True cold start — must block once to seed catalog + snapshot.
            catalog, meta = _get_catalog(
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
                force=False,
                blocking=True,
                refresh_counts=True,
            )

        if catalog is None:
            # Load-more while rebuild still warming — ask UI to retry.
            _schedule_catalog_revalidate(
                catalog_key,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
            )
            raise HTTPException(
                status_code=503,
                detail="catalog rebuilding; retry shortly",
            )

        # One-shot Telegram Desktop export → conversations / ТГ ник (+ Redis/file).
        try:
            _apply_telegram_export_and_recache(
                catalog,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
                force_import=False,
            )
        except Exception:
            log.warning("moysklad telegram export hook failed", exc_info=True)

        page = clients_page(
            _client(),
            sales_filter=sales_filter,
            group=group,
            q=q,
            channel_kind=channel_kind,
            require_phone=require_phone,
            require_telegram=require_telegram,
            vip_only=vip_only,
            birthday_soon=birthday_soon,
            group_source=group_source,
            days_before_event=days_before_event,
            event_date_from=event_date_from,
            event_date_to=event_date_to,
            stage=stage,
            entity_type=entity_type,
            loyalty_only=loyalty_only,
            limit=limit,
            offset=offset,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            catalog=catalog,
            last_contact_from=last_contact_from,
            last_contact_to=last_contact_to,
        )
        page["clients"] = enrich_clients(list(page.get("clients") or []))

        # Partial catalog may undercount — keep scrolling until rebuild finishes.
        if _catalog_is_partial(catalog):
            page["has_more"] = True
            meta = dict(meta)
            meta["revalidating"] = True
            meta["stale"] = True

        # Grow Redis/file window so next scroll hits snapshot (fast path).
        # Calendar / lead-window queries never read snapshots (see above) —
        # skip the write too. The 2500-row window rebuild runs in a daemon
        # thread: building + enriching it inline made every first filter
        # click take 30–90s and the UI hung at «0 · обновляем…».
        try:
            if calendar_filter_active:
                pass
            elif offset == 0:
                if int(limit) >= PAGE_SNAPSHOT_ROWS:
                    set_page_snapshot(
                        snap_key,
                        _strip_internal(page),
                        synced_at=float(meta.get("synced_at") or time.time()),
                    )
                else:
                    _schedule_snapshot_refresh(
                        snap_key,
                        catalog=catalog,
                        sales_filter=sales_filter,
                        group=group,
                        q=q,
                        group_source=group_source,
                        channel_kind=channel_kind,
                        require_phone=require_phone,
                        require_telegram=require_telegram,
                        vip_only=vip_only,
                        birthday_soon=birthday_soon,
                        days_before_event=days_before_event,
                        event_date_from=event_date_from,
                        event_date_to=event_date_to,
                        stage=stage,
                        entity_type=entity_type,
                        loyalty_only=loyalty_only,
                        max_orders=max_orders,
                        max_counterparties=max_counterparties,
                        include_archived=include_archived,
                    )
            else:
                extend_page_snapshot(
                    snap_key,
                    list(page.get("clients") or []),
                    matched_total=int(page.get("matched_total") or 0),
                    counts=page.get("counts")
                    if isinstance(page.get("counts"), dict)
                    else None,
                    synced_at=float(meta.get("synced_at") or time.time()),
                )
        except Exception:
            log.warning("moysklad page snapshot write failed", exc_info=True)

        meta = dict(meta)
        meta["snapshot"] = False
        meta["revalidating"] = bool(meta.get("revalidating"))
        return _attach_cache_meta(
            ensure_group_options_by_source(_strip_internal(page)),
            meta,
        )
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/clients/ids")
def get_clients_ids(
    sales_filter: str = Query("all"),
    group: str = Query(""),
    q: str = Query(""),
    channel_kind: str = Query(""),
    require_phone: bool = Query(False),
    require_telegram: bool = Query(False),
    vip_only: bool = Query(False),
    birthday_soon: bool = Query(False),
    group_source: str = Query("any"),
    days_before_event: int = Query(0, ge=0, le=365),
    event_date_from: str = Query(""),
    event_date_to: str = Query(""),
    last_contact_from: str = Query(""),
    last_contact_to: str = Query(""),
    stage: str = Query("all"),
    entity_type: str = Query("all"),
    loyalty_only: bool = Query(False),
    limit: int = Query(MASS_AUDIENCE_IDS_MAX, ge=1, le=MASS_AUDIENCE_IDS_MAX),
    offset: int = Query(0, ge=0),
    max_orders: int = Query(25000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
) -> dict[str, Any]:
    """Ids-only audience slice for «Выбрать всех» / 10k mass send.

    Skips conversation enrich + public row hydrate — cheapest request that
    still respects the same filters as ``GET /clients``.
    """
    try:
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=False,
            blocking=True,
            refresh_counts=False,
        )
        if catalog is None:
            raise HTTPException(status_code=503, detail="catalog unavailable")
        page = clients_page(
            _client(),
            sales_filter=sales_filter,
            group=group,
            q=q,
            channel_kind=channel_kind,
            require_phone=require_phone,
            require_telegram=require_telegram,
            vip_only=vip_only,
            birthday_soon=birthday_soon,
            group_source=group_source,
            days_before_event=days_before_event,
            event_date_from=event_date_from,
            event_date_to=event_date_to,
            stage=stage,
            entity_type=entity_type,
            loyalty_only=loyalty_only,
            limit=limit,
            offset=offset,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            catalog=catalog,
            last_contact_from=last_contact_from,
            last_contact_to=last_contact_to,
        )
        rows = list(page.get("_rows") or [])
        ids: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid = str(
                row.get("_moysklad_id")
                or row.get("id")
                or row.get("moysklad_id")
                or ""
            ).strip()
            if cid:
                ids.append(cid)
        payload = {
            "ok": True,
            "ids": ids,
            "matched_total": int(page.get("matched_total") or 0),
            "returned": len(ids),
            "offset": offset,
            "limit": limit,
            "has_more": bool(page.get("has_more")),
            "next_offset": page.get("next_offset"),
            "cap": MASS_AUDIENCE_IDS_MAX,
            "counts": page.get("counts"),
        }
        return _attach_cache_meta(payload, meta if isinstance(meta, dict) else {})
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/ids failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/clients/integrity")
def get_clients_integrity(
    max_orders: int = Query(25000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    """Проверка таблицы: tab arithmetic + concrete data defects with samples."""
    try:
        from plugins.moysklad.integrity import audit_catalog

        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=refresh,
            blocking=True,
        )
        if catalog is None:
            raise HTTPException(status_code=503, detail="catalog unavailable")
        report = catalog_integrity(catalog)
        report["audit"] = audit_catalog(catalog)
        return _attach_cache_meta(report, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/integrity failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# Literal route MUST precede /clients/{client_id}, else the wildcard eats it
# and the UI polls «client not found in catalog» instead of verify progress.
@router.get("/clients/telegram-verify")
def get_clients_telegram_verify() -> dict[str, Any]:
    with _TG_PHONE_VERIFY_LOCK:
        return {"ok": True, "state": dict(_TG_PHONE_VERIFY_STATE)}


@router.get("/clients/{client_id}")
def get_client_detail(
    client_id: str,
    max_orders: int = Query(25000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    ai: bool = Query(False),
) -> dict[str, Any]:
    """Client card from durable catalog cache (orders + stats + messaging).

    Pass ``ai=true`` to run a fresh DeepSeek summary/recommendation.
    Without it: serve fingerprint-fresh cached DeepSeek if present, else
    deterministic heuristic (never a stale cached summary).
    """
    try:
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=False,
        )
        row = _row_or_contact(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        detail = build_client_detail(row)
        fp = client_ai_facts_fingerprint(detail)
        if ai:
            detail["ai"] = generate_ai_for_detail(detail)
            try:
                set_client_ai(client_id, detail["ai"], fingerprint=fp)
            except Exception:
                log.debug("client AI cache write failed", exc_info=True)
        else:
            cached = get_client_ai(client_id, fingerprint=fp)
            if cached:
                detail["ai"] = {**cached, "from_cache": True}
            # else keep heuristic from build_client_detail
        detail["ai_facts_fingerprint"] = fp
        detail["ai_cache_backend"] = client_ai_cache_backend_name()
        return _attach_cache_meta(detail, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id} failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/clients/{client_id}/conversation")
def get_client_conversation(client_id: str) -> dict[str, Any]:
    """Local TG/WA thread for a client (MVP store; gateway sync later)."""
    try:
        catalog, meta = _get_catalog(force=False)
        row = find_row_in_catalog(catalog, client_id)
        if row is None:
            # Still allow lookup by id alone (orphaned local thread).
            thread = get_thread(client_id=client_id)
            return {"ok": True, "conversation": thread}
        detail = build_client_detail(row)
        return _attach_cache_meta(
            {"ok": True, "conversation": detail.get("conversation") or {}},
            meta,
        )
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id}/conversation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/{client_id}/conversation")
def post_client_conversation(
    client_id: str, body: ConversationAppendBody
) -> dict[str, Any]:
    """Append a message (outbound/inbound) to the local client thread.

    Outbound Telegram from the client card (``source=client_card_send``) also
    attempts Bot API delivery via the Business outreach bot.
    """
    try:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        catalog, _meta = _get_catalog(force=False)
        row = find_row_in_catalog(catalog, client_id)
        phone = ""
        tg_nick = ""
        tg_conversation = ""
        tg_chat_id = ""
        client_name = ""
        deep_link = ""
        if row is not None:
            detail = build_client_detail(row)
            client = detail.get("client") or {}
            phone = str(client.get("phone") or "")
            tg_nick = str(client.get("tg_nick") or "")
            tg_conversation = str(client.get("tg_conversation") or "")
            tg_chat_id = str(client.get("tg_chat_id") or "")
            client_name = str(client.get("name") or "")
            msg = detail.get("messaging") or {}
            ch = (body.channel or "telegram").strip().lower()
            if ch == "whatsapp":
                deep_link = str(msg.get("whatsapp_url") or "")
            else:
                deep_link = str(msg.get("telegram_url") or "")
        else:
            # Outreach contact without a catalog row — resolve the TG peer
            # from the contact registry so the reply box actually delivers.
            contact = get_contact(client_id)
            if contact is not None:
                phone = str(contact.get("phone") or "")
                tg_nick = str(contact.get("tg_nick") or "")
                tg_chat_id = str(contact.get("tg_chat_id") or "")
                client_name = str(contact.get("name") or "")
                if tg_nick:
                    deep_link = f"https://t.me/{tg_nick}"

        channel = (body.channel or "telegram").strip().lower()
        direction = (body.direction or "outbound").strip().lower()
        source = body.source or "manual"
        delivery: dict[str, Any] = {"ok": False, "skipped": True}
        if (
            direction == "outbound"
            and channel.startswith("telegram")
            and source.startswith(("client_card_send", "campaign"))
        ):
            delivery = send_outreach_to_client(
                text=text,
                tg_nick=tg_nick,
                tg_conversation=tg_conversation,
                tg_chat_id=tg_chat_id,
                image_base64=body.image_base64,
                image_name=body.image_name or "photo.jpg",
                image_url=body.image_url,
            )
            if delivery.get("ok"):
                source = "client_card_telegram_bot"

        thread = append_message(
            client_id=client_id,
            text=text,
            direction=direction,
            channel=channel,
            label=body.label or "",
            phone=phone,
            tg_nick=tg_nick,
            tg_chat_id=str(delivery.get("chat_id") or tg_chat_id or ""),
            client_name=client_name,
            source=source,
        )
        open_link = bool(body.open_deep_link) and not delivery.get("ok")
        return {
            "ok": True,
            "conversation": thread,
            "deep_link": deep_link if open_link else "",
            "delivery": delivery,
            "telegram": telegram_send_status(),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad POST /clients/{id}/conversation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/mark-sent")
def post_campaign_mark_sent(body: MarkSentBody) -> dict[str, Any]:
    """Deliver outreach via Telegram Business bot (when configured) and record thread.

    WhatsApp still returns a deep-link only. Telegram: Bot API send first; on
    failure or missing target, optionally open deep-link for manual send.
    ``client_id`` may be a MoySklad id or ``custom:<id>`` from outreach contacts.
    """
    try:
        text = (body.message or "").strip()
        client_id = (body.client_id or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="message required")
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")

        channel = (body.channel or "telegram").strip().lower()
        deep_link = ""
        phone = ""
        tg_nick = ""
        tg_conversation = ""
        tg_chat_id = ""
        client_name = ""
        facts_payload: dict[str, Any] | None = None
        meta: dict[str, Any] = {}

        if _is_contact_id(client_id):
            contact = get_contact(client_id)
            if contact is None:
                raise HTTPException(status_code=404, detail="custom contact not found")
            tg_nick = str(contact.get("tg_nick") or "")
            tg_chat_id = str(contact.get("tg_chat_id") or "")
            client_name = str(contact.get("name") or "")
            if tg_nick:
                deep_link = f"https://t.me/{tg_nick}"
        else:
            catalog, meta = _get_catalog(force=False)
            row = find_row_in_catalog(catalog, client_id)
            if row is None:
                # Overlay-only peer without catalog row — still allow send.
                contact = get_contact(client_id)
                if contact is None:
                    raise HTTPException(status_code=404, detail="client not found in catalog")
                tg_nick = str(contact.get("tg_nick") or "")
                tg_chat_id = str(contact.get("tg_chat_id") or "")
                client_name = str(contact.get("name") or "")
                if tg_nick:
                    deep_link = f"https://t.me/{tg_nick}"
            else:
                # Fast path — never build_client_detail here (composition I/O
                # hung «Отправляю…» for tens of seconds / past the client timeout).
                target = _outreach_target_from_row(row)
                phone = str(target.get("phone") or "")
                tg_nick = str(target.get("tg_nick") or "")
                tg_conversation = str(target.get("tg_conversation") or "")
                tg_chat_id = str(target.get("tg_chat_id") or "")
                client_name = str(target.get("client_name") or "")
                if channel == "whatsapp":
                    deep_link = str(target.get("whatsapp_url") or "")
                else:
                    deep_link = str(target.get("telegram_url") or "")
                facts_payload = None  # filled after append

        delivery: dict[str, Any] = {"ok": False, "skipped": True}
        if body.deliver and channel.startswith("telegram"):
            delivery = send_outreach_to_client(
                text=text,
                tg_nick=tg_nick,
                tg_conversation=tg_conversation,
                tg_chat_id=tg_chat_id,
                via=body.via,
                image_base64=body.image_base64,
                image_name=body.image_name or "photo.jpg",
                image_url=body.image_url,
            )

        source = "campaign_send"
        if delivery.get("ok"):
            source = "campaign_telegram_bot"

        thread = append_message(
            client_id=client_id,
            text=text,
            direction="outbound",
            channel=channel,
            label="",
            phone=phone,
            tg_nick=tg_nick,
            tg_chat_id=str(
                delivery.get("chat_id")
                or tg_chat_id
                or ""
            ),
            client_name=client_name,
            source=source,
        )
        open_link = bool(body.open_deep_link) and not delivery.get("ok")
        payload: dict[str, Any] = {
            "ok": True,
            "conversation": thread,
            "deep_link": deep_link if open_link else "",
            "channel": channel,
            "delivery": delivery,
            "telegram": telegram_send_status(),
        }
        # Do NOT rebuild facts via build_client_detail — that was the second
        # hang after the actual Telegram send. UI already has the card; the
        # conversation thread alone is enough to refresh the pane.
        if not _is_contact_id(client_id):
            try:
                catalog, meta = _get_catalog(force=False)
                return _attach_cache_meta(payload, meta)
            except Exception:
                pass
        return payload
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mark-sent failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/mark-sent-batch")
def post_campaign_mark_sent_batch(body: MarkSentBatchBody) -> dict[str, Any]:
    """Deliver one message to many clients via Business bot; record each thread."""
    try:
        text = (body.message or "").strip()
        ids = [str(x or "").strip() for x in (body.client_ids or []) if str(x or "").strip()]
        # De-dupe, preserve order
        seen: set[str] = set()
        client_ids: list[str] = []
        for cid in ids:
            if cid in seen:
                continue
            seen.add(cid)
            client_ids.append(cid)
        if not text:
            raise HTTPException(status_code=400, detail="message required")
        if not client_ids:
            raise HTTPException(status_code=400, detail="client_ids required")
        if len(client_ids) > MASS_SEND_BATCH_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"max {MASS_SEND_BATCH_MAX} clients per batch — UI chunks larger sends",
            )

        catalog, meta = _get_catalog(force=False)
        channel = (body.channel or "telegram").strip().lower()
        deliver_telegram = bool(body.deliver) and channel.startswith("telegram")
        # One account-level check for the whole batch — a dead connection should
        # not be discovered 40 messages in.
        account: dict[str, Any] = {}
        if deliver_telegram and (body.via or telegram_send_mode()) != "user":
            account = business_preflight()
            if not account.get("ok") and telegram_send_mode() == "bot":
                raise HTTPException(
                    status_code=400,
                    detail=str(account.get("detail") or account.get("error")),
                )
        delay = send_delay_seconds()
        results: list[dict[str, Any]] = []
        sent_ok = 0
        sent_failed = 0
        stopped_early = False
        for index, client_id in enumerate(client_ids):
            tg_nick = ""
            tg_conversation = ""
            tg_chat_id = ""
            client_name = ""
            phone = ""
            deep_link = ""

            if _is_contact_id(client_id):
                contact = get_contact(client_id)
                if contact is None:
                    results.append(
                        {
                            "client_id": client_id,
                            "ok": False,
                            "error": "client_not_found",
                            "delivery": {"ok": False},
                        }
                    )
                    continue
                tg_nick = str(contact.get("tg_nick") or "")
                tg_chat_id = str(contact.get("tg_chat_id") or "")
                client_name = str(contact.get("name") or "")
                if tg_nick:
                    deep_link = f"https://t.me/{tg_nick}"
            else:
                row = find_row_in_catalog(catalog, client_id)
                if row is None:
                    contact = get_contact(client_id)
                    if contact is None:
                        results.append(
                            {
                                "client_id": client_id,
                                "ok": False,
                                "error": "client_not_found",
                                "delivery": {"ok": False},
                            }
                        )
                        continue
                    tg_nick = str(contact.get("tg_nick") or "")
                    tg_chat_id = str(contact.get("tg_chat_id") or "")
                    client_name = str(contact.get("name") or "")
                    if tg_nick:
                        deep_link = f"https://t.me/{tg_nick}"
                else:
                    detail = build_client_detail(row)
                    client = detail.get("client") or {}
                    msg = detail.get("messaging") or {}
                    phone = str(client.get("phone") or "")
                    tg_nick = str(client.get("tg_nick") or "")
                    tg_conversation = str(client.get("tg_conversation") or "")
                    tg_chat_id = str(
                        client.get("tg_chat_id")
                        or row.get("ТГ chat id")
                        or row.get("tg_chat_id")
                        or ""
                    )
                    client_name = str(client.get("name") or "")
                    if channel == "whatsapp":
                        deep_link = str(msg.get("whatsapp_url") or "")
                    else:
                        deep_link = str(msg.get("telegram_url") or "")

            delivery: dict[str, Any] = {"ok": False, "skipped": True}
            if deliver_telegram:
                if index and delay:
                    time.sleep(delay)
                delivery = send_outreach_to_client(
                    text=text,
                    tg_nick=tg_nick,
                    tg_conversation=tg_conversation,
                    tg_chat_id=tg_chat_id,
                    via=body.via,
                )

            source = "campaign_send_batch"
            if delivery.get("ok"):
                source = "campaign_telegram_bot"
                sent_ok += 1
            elif deliver_telegram:
                sent_failed += 1

            thread = append_message(
                client_id=client_id,
                text=text,
                direction="outbound",
                channel=channel,
                label="",
                phone=phone,
                tg_nick=tg_nick,
                tg_chat_id=str(
                    delivery.get("chat_id")
                    or tg_chat_id
                    or ""
                ),
                client_name=client_name,
                source=source,
            )
            open_link = bool(body.open_deep_link) and not delivery.get("ok")
            results.append(
                {
                    "client_id": client_id,
                    # ok = the message actually left, not merely «row processed».
                    "ok": bool(delivery.get("ok")) if deliver_telegram else True,
                    "client_name": client_name,
                    "error": None if delivery.get("ok") else delivery.get("error"),
                    "detail": None if delivery.get("ok") else delivery.get("detail"),
                    "delivery": delivery,
                    "conversation": thread,
                    "deep_link": deep_link if open_link else "",
                }
            )
            if deliver_telegram and not delivery.get("ok") and body.stop_on_error:
                stopped_early = True
                break

        # Persist into История отправок (same store as background mass-send).
        history_job = None
        if results:
            try:
                history_job = mass_send_jobs.record_completed_job(
                    message=text,
                    results=results,
                    channel=channel,
                    via=body.via or telegram_send_mode(),
                    deliver=body.deliver,
                )
            except Exception:
                log.warning("mark-sent-batch history persist failed", exc_info=True)

        payload = {
            "ok": sent_failed == 0,
            "channel": channel,
            "total": len(client_ids),
            "attempted": len(results),
            "sent_ok": sent_ok,
            "sent_failed": sent_failed,
            "stopped_early": stopped_early,
            "send_via": (body.via or telegram_send_mode()),
            "account": account or None,
            "results": results,
            "telegram": telegram_send_status(),
            "job": mass_send_jobs.summary(history_job) if history_job else None,
        }
        return _attach_cache_meta(payload, meta)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mark-sent-batch failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _resolve_mass_send_target(
    catalog: dict[str, Any] | None, client_id: str
) -> dict[str, Any] | None:
    """Contact / catalog row → TG coordinates for one mass-send recipient."""
    if _is_contact_id(client_id):
        contact = get_contact(client_id)
        if contact is None:
            return None
        return {
            "tg_nick": str(contact.get("tg_nick") or ""),
            "tg_conversation": "",
            "tg_chat_id": str(contact.get("tg_chat_id") or ""),
            "client_name": str(contact.get("name") or ""),
            "phone": "",
        }
    row = find_row_in_catalog(catalog, client_id) if catalog else None
    if row is None:
        contact = get_contact(client_id)
        if contact is None:
            return None
        return {
            "tg_nick": str(contact.get("tg_nick") or ""),
            "tg_conversation": "",
            "tg_chat_id": str(contact.get("tg_chat_id") or ""),
            "client_name": str(contact.get("name") or ""),
            "phone": "",
        }
    # Same lightweight path as mark-sent — never build_client_detail per recipient.
    target = _outreach_target_from_row(row)
    return {
        "tg_nick": str(target.get("tg_nick") or ""),
        "tg_conversation": str(target.get("tg_conversation") or ""),
        "tg_chat_id": str(target.get("tg_chat_id") or ""),
        "client_name": str(target.get("client_name") or ""),
        "phone": str(target.get("phone") or ""),
    }


@router.post("/campaigns/mass-send")
def post_campaign_mass_send(body: MassSendStartBody) -> dict[str, Any]:
    """Fire a mass Рассылка as a background job — the request returns at once.

    The worker walks recipients sequentially (anti-flood delay between
    sends), records each thread, and the UI polls
    ``GET /campaigns/mass-send/{id}`` for per-recipient statuses.
    """
    try:
        text = (body.message or "").strip()
        ids = [str(x or "").strip() for x in (body.client_ids or []) if str(x or "").strip()]
        seen: set[str] = set()
        client_ids = [c for c in ids if not (c in seen or seen.add(c))]
        if not text:
            raise HTTPException(status_code=400, detail="message required")
        if not client_ids:
            raise HTTPException(status_code=400, detail="client_ids required")
        if len(client_ids) > MASS_AUDIENCE_IDS_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"max {MASS_AUDIENCE_IDS_MAX} clients per mass send",
            )
        running = mass_send_jobs.running_job_id()
        if running:
            raise HTTPException(
                status_code=409,
                detail=f"mass send {running} already running — wait or cancel it",
            )

        channel = (body.channel or "telegram").strip().lower()
        deliver_telegram = bool(body.deliver) and channel.startswith("telegram")
        if deliver_telegram and (body.via or telegram_send_mode()) != "user":
            account = business_preflight()
            if not account.get("ok") and telegram_send_mode() == "bot":
                raise HTTPException(
                    status_code=400,
                    detail=str(account.get("detail") or account.get("error")),
                )

        catalog, _meta = _get_catalog(force=False)
        via = body.via

        def _send_one(client_id: str) -> dict[str, Any]:
            target = _resolve_mass_send_target(catalog, client_id)
            if target is None:
                return {"ok": False, "error": "client_not_found"}
            delivery: dict[str, Any] = {"ok": False, "skipped": True}
            if deliver_telegram:
                delivery = send_outreach_to_client(
                    text=text,
                    tg_nick=target["tg_nick"],
                    tg_conversation=target["tg_conversation"],
                    tg_chat_id=target["tg_chat_id"],
                    via=via,
                    image_base64=body.image_base64,
                    image_name=body.image_name or "photo.jpg",
                    image_url=body.image_url,
                )
            source = "campaign_send_mass"
            if delivery.get("ok"):
                source = "campaign_telegram_bot"
            append_message(
                client_id=client_id,
                text=text,
                direction="outbound",
                channel=channel,
                label="",
                phone=target["phone"],
                tg_nick=target["tg_nick"],
                tg_chat_id=str(delivery.get("chat_id") or target["tg_chat_id"] or ""),
                client_name=target["client_name"],
                source=source,
            )
            return {
                # ok = the message actually left, not merely «row processed».
                "ok": bool(delivery.get("ok")) if deliver_telegram else True,
                "client_name": target["client_name"],
                "tg_nick": target["tg_nick"],
                "error": None if delivery.get("ok") else delivery.get("error"),
                "detail": None if delivery.get("ok") else delivery.get("detail"),
            }

        job = mass_send_jobs.create_job(
            message=text,
            client_ids=client_ids,
            channel=channel,
            via=via,
            deliver=body.deliver,
            stop_on_error=body.stop_on_error,
        )
        delay = send_delay_seconds() if deliver_telegram else 0.0
        if not mass_send_jobs.start_job(job["id"], _send_one, delay=delay):
            mass_send_jobs.abort_queued(job["id"])
            raise HTTPException(status_code=409, detail="mass send already running")
        return {
            "ok": True,
            "job": mass_send_jobs.summary(job),
            "telegram": telegram_send_status(),
        }
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mass-send failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/campaigns/mass-send/latest")
def get_campaign_mass_send_latest() -> dict[str, Any]:
    """Most recent job — lets a reloaded UI reattach to a running blast."""
    try:
        return {"ok": True, "job": mass_send_jobs.latest_job_summary()}
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mass-send/latest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# NOTE: literal routes must stay above /campaigns/mass-send/{job_id}.
@router.get("/campaigns/mass-send/history")
def get_campaign_mass_send_history(
    limit: int = Query(20, ge=1, le=20),
) -> dict[str, Any]:
    """История отправок: newest-first job summaries (message preview, counts,
    status). Per-recipient log — GET /campaigns/mass-send/{job_id}.

    Merges background ``mass_send_jobs`` with conversation-derived blasts
    (identical исходящие in Facts / TG conversation — Telegram export and
    older mark-sent-batch sends that never wrote a job file).
    """
    try:
        from plugins.moysklad.conversations import list_outbound_blasts, recency_epoch

        cap = max(1, min(int(limit or 20), 20))
        jobs = list(mass_send_jobs.list_jobs(limit=cap))
        blasts = list_outbound_blasts(limit=cap)
        by_id: dict[str, dict[str, Any]] = {}
        for row in jobs + blasts:
            jid = str(row.get("id") or "").strip()
            if jid and jid not in by_id:
                by_id[jid] = row
        merged = sorted(
            by_id.values(),
            key=lambda j: recency_epoch(j.get("created_at")),
            reverse=True,
        )
        return {"ok": True, "jobs": merged[:cap]}
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mass-send/history failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _yandex_reconciliation(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """«МС vs кабинет Яндекса» — real cabinet numbers next to computed ones.

    MoySklad orders carry pre-discount list prices, so the yandex_market
    channel runs above what buyers actually paid; this block makes the gap
    explicit instead of leaving the dashboard silently off the reference.
    """
    try:
        from plugins.moysklad.report_backtest import extract_month_report
        from plugins.moysklad.yandex_stats import (
            build_reconciliation,
            yandex_monthly_stats_cached,
        )

        stats = yandex_monthly_stats_cached(months=3)
        if not stats:
            return []
        month_report = extract_month_report(summary.get("analytics") or {})
        return build_reconciliation(month_report, stats)
    except Exception:  # pragma: no cover — reconciliation must never break дашборд
        log.warning("yandex reconciliation failed", exc_info=True)
        return []


@router.get("/dashboard")
def get_dashboard(recompute: bool = Query(False)) -> dict[str, Any]:
    """Дашборд: CRM-виталс + аналитика Вереск (день/неделя/месяц/Флау).

    Serves Elasticsearch / Redis / file cache first. MoySklad is only
    contacted when the catalog envelope is missing (or SWR refresh).
    Computed analytics are cached against catalog ``synced_at``.
    """
    try:
        from plugins.moysklad.dashboard_stats import build_dashboard_summary

        catalog, meta = _get_catalog(force=False, blocking=True)
        synced = float(meta.get("synced_at") or 0)
        if not recompute:
            cached_summary = get_dashboard_summary_cached(synced)
            if cached_summary:
                return _attach_cache_meta(
                    {
                        "ok": True,
                        "analytics_cached": True,
                        **cached_summary,
                        "yandex_reconciliation": _yandex_reconciliation(cached_summary),
                    },
                    meta,
                )
        rows = list((catalog or {}).get("rows") or [])
        summary = build_dashboard_summary(
            rows, last_job=mass_send_jobs.latest_job_summary()
        )
        set_dashboard_summary_cached(summary, catalog_synced_at=synced)
        return _attach_cache_meta(
            {
                "ok": True,
                "analytics_cached": False,
                **summary,
                "yandex_reconciliation": _yandex_reconciliation(summary),
            },
            meta,
        )
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /dashboard failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/campaigns/sent-history")
def get_campaign_sent_history(
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Каждое исходящее (одиночные + массовые) из стора переписок: кому,
    что, когда, статус delivered/recorded. Дополняет mass-send/history,
    которая показывает только группы ≥2 получателей."""
    try:
        from plugins.moysklad.sent_history import list_sent_messages

        return {"ok": True, "messages": list_sent_messages(limit=limit)}
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/sent-history failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/campaigns/mass-send/{job_id}")
def get_campaign_mass_send_job(
    job_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    status: str = Query("all"),
) -> dict[str, Any]:
    """Job summary + recipient slice (statuses finalize in send order)."""
    try:
        snap = mass_send_jobs.job_snapshot(
            job_id, offset=offset, limit=limit, status=status
        )
        if snap is None:
            from plugins.moysklad.conversations import (
                get_outbound_blast,
                is_blast_history_id,
            )

            if is_blast_history_id(job_id):
                snap = get_outbound_blast(
                    job_id, offset=offset, limit=limit, status=status
                )
        if snap is None:
            raise HTTPException(status_code=404, detail="mass send job not found")
        return {"ok": True, "job": snap}
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mass-send/{id} failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/mass-send/{job_id}/cancel")
def post_campaign_mass_send_cancel(job_id: str) -> dict[str, Any]:
    """Ask the worker to stop after the current recipient."""
    try:
        ok = mass_send_jobs.cancel_job(job_id)
        if not ok:
            raise HTTPException(
                status_code=409, detail="job is not running (already finished?)"
            )
        snap = mass_send_jobs.job_snapshot(job_id, offset=0, limit=1)
        return {"ok": True, "job": snap}
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/mass-send/{id}/cancel failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/replies/collect")
def post_campaign_replies_collect(body: CollectRepliesBody) -> dict[str, Any]:
    """Sync + list clients who replied after a mass Рассылка (awaiting operator)."""
    try:
        ids = [str(x or "").strip() for x in (body.client_ids or []) if str(x or "").strip()]
        seen: set[str] = set()
        client_ids = [c for c in ids if not (c in seen or seen.add(c))]
        synced = 0
        inbound_imported = 0
        if body.sync and client_ids:
            catalog, _meta = _get_catalog(force=False)
            # Cap live sync work per click — UI can re-run for the rest.
            for client_id in client_ids[: min(len(client_ids), 200)]:
                row = find_row_in_catalog(catalog, client_id) if catalog else None
                phone = ""
                tg_nick = ""
                tg_chat_id = ""
                client_name = ""
                if row is not None:
                    detail = build_client_detail(row)
                    client = detail.get("client") or {}
                    phone = str(client.get("phone") or "")
                    tg_nick = str(client.get("tg_nick") or "")
                    tg_chat_id = str(client.get("tg_chat_id") or "")
                    client_name = str(client.get("name") or "")
                thread = sync_client_conversation(
                    client_id=client_id,
                    phone=phone,
                    tg_nick=tg_nick,
                    tg_chat_id=tg_chat_id,
                    client_name=client_name,
                )
                synced += 1
                sync_meta = thread.get("sync") or {}
                inbound_imported += int(sync_meta.get("inbound_imported") or 0)

        replies = list_awaiting_replies(client_ids or None, limit=body.limit)
        return {
            "ok": True,
            "synced": synced,
            "inbound_imported": inbound_imported,
            "awaiting": len(replies),
            "replies": replies,
            "batch_max": MASS_SEND_BATCH_MAX,
        }
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/replies/collect failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class PreflightBody(BaseModel):
    """Who in this audience can the Business bot actually reach?"""

    client_ids: list[str] = Field(default_factory=list)


@router.post("/campaigns/telegram/preflight")
def post_campaign_preflight(body: PreflightBody) -> dict[str, Any]:
    """Resolve every recipient before sending — no half-delivered рассылка."""
    try:
        account = business_preflight()
        ids = [str(x or "").strip() for x in (body.client_ids or []) if str(x or "").strip()]
        seen: set[str] = set()
        client_ids = [c for c in ids if not (c in seen or seen.add(c))]

        catalog, meta = _get_catalog(force=False)
        recipients: list[dict[str, Any]] = []
        ready = 0
        for client_id in client_ids:
            tg_nick = ""
            tg_conversation = ""
            tg_chat_id = ""
            name = ""
            row = None if _is_contact_id(client_id) else find_row_in_catalog(catalog, client_id)
            if row is not None:
                detail = build_client_detail(row)
                client = detail.get("client") or {}
                name = str(client.get("name") or "")
                tg_nick = str(client.get("tg_nick") or "")
                tg_conversation = str(client.get("tg_conversation") or "")
                tg_chat_id = str(client.get("tg_chat_id") or row.get("tg_chat_id") or "")
            else:
                contact = get_contact(client_id)
                if contact is None:
                    recipients.append({
                        "client_id": client_id,
                        "ok": False,
                        "error": "client_not_found",
                        "detail": "Нет ни карточки, ни контакта",
                    })
                    continue
                name = str(contact.get("name") or "")
                tg_nick = str(contact.get("tg_nick") or "")
                tg_chat_id = str(contact.get("tg_chat_id") or "")

            check = preflight_recipient(
                tg_nick=tg_nick,
                tg_conversation=tg_conversation,
                tg_chat_id=tg_chat_id,
            )
            if check.get("ok"):
                ready += 1
            recipients.append({
                "client_id": client_id,
                "name": name,
                "tg_nick": tg_nick,
                "ok": bool(check.get("ok")),
                "chat_id": check.get("chat_id") or "",
                "resolved_via": check.get("resolved_via") or "",
                "error": None if check.get("ok") else check.get("error"),
                "detail": None if check.get("ok") else check.get("detail"),
            })

        payload = {
            "ok": bool(account.get("ok")) and ready == len(recipients),
            "account": account,
            "total": len(recipients),
            "ready": ready,
            "blocked": len(recipients) - ready,
            "send_via": telegram_send_mode(),
            "delay_seconds": send_delay_seconds(),
            "recipients": recipients,
        }
        return _attach_cache_meta(payload, meta)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("moysklad /campaigns/telegram/preflight failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/campaigns/telegram-user")
def get_telegram_user_account(probe: bool = Query(True)) -> dict[str, Any]:
    """Personal Telegram account status (no secrets) + active send mode."""
    return {**telegram_user_status(probe=probe), "send_mode": telegram_send_mode()}


@router.post("/campaigns/telegram-user/install")
def post_telegram_user_install() -> dict[str, Any]:
    """Lazy-install Telethon (MTProto runtime) and report the result."""
    out = tg_user.ensure_runtime()
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=str(out.get("detail") or out.get("error")))
    return {**out, **telegram_user_status(probe=False)}


@router.post("/campaigns/telegram-user/credentials")
def post_telegram_user_credentials(body: TelegramUserLoginBody) -> dict[str, Any]:
    """Store my.telegram.org api_id / api_hash without starting a login."""
    out = tg_user.save_credentials(api_id=body.api_id, api_hash=body.api_hash, strict=True)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=str(out.get("detail") or out.get("error")))
    return {**out, **telegram_user_status(probe=False)}


@router.post("/campaigns/telegram-user/login")
def post_telegram_user_login(body: TelegramUserLoginBody) -> dict[str, Any]:
    """Step 1 — store api_id/api_hash if given and request the login code."""
    out = tg_user.start_login(
        phone=body.phone,
        api_id=body.api_id,
        api_hash=body.api_hash,
        force_sms=body.force_sms,
    )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=str(out.get("detail") or out.get("error")))
    return out


@router.post("/campaigns/telegram-user/code")
def post_telegram_user_code(body: TelegramUserCodeBody) -> dict[str, Any]:
    """Step 2 — the code Telegram sent. ``password_required`` when 2FA is on."""
    out = tg_user.submit_code(body.code)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=str(out.get("detail") or out.get("error")))
    return out


@router.post("/campaigns/telegram-user/password")
def post_telegram_user_password(body: TelegramUserPasswordBody) -> dict[str, Any]:
    """Step 3 — cloud (2FA) password."""
    out = tg_user.submit_password(body.password)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=str(out.get("detail") or out.get("error")))
    return out


@router.post("/campaigns/telegram-user/session")
def post_telegram_user_session(body: TelegramUserSessionBody) -> dict[str, Any]:
    """Import a Telethon StringSession (workaround when MTProto is blocked on VDS)."""
    out = tg_user.save_session(session=body.session, phone=body.phone)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=str(out.get("detail") or out.get("error")))
    return {**out, **telegram_user_status(probe=False)}


@router.post("/campaigns/telegram-user/logout")
def post_telegram_user_logout() -> dict[str, Any]:
    return tg_user.logout()


@router.post("/campaigns/telegram-user/contacts/refresh")
def post_telegram_user_contacts_refresh() -> dict[str, Any]:
    """Start a background contact sync; returns at once. Poll .../contacts/sync."""
    return tg_user.start_contacts_sync(force=True)


@router.get("/campaigns/telegram-user/contacts/sync")
def get_telegram_user_contacts_sync() -> dict[str, Any]:
    """Progress of the background contact sync (running/phase/total/error)."""
    return tg_user.contacts_sync_status()


# Catalog rows for the contact picker. clients_page over the full catalog
# recomputes audience counts on every call (CPU-bound, tens of seconds on a
# big base with orders context) — that must NEVER run inside the dropdown
# request. The request serves this cache; a daemon thread rebuilds it.
_PICKER_CATALOG: dict[str, Any] = {"rows": [], "built_at": 0.0, "attempt_at": 0.0}
_PICKER_CATALOG_LOCK = threading.Lock()
_PICKER_CATALOG_TTL = 300.0
_PICKER_CATALOG_RETRY = 60.0


def _picker_catalog_rows(limit: int) -> list[dict[str, Any]]:
    """Cached catalog clients (with TG handles) for the picker — non-blocking."""
    now = time.monotonic()
    with _PICKER_CATALOG_LOCK:
        rows = list(_PICKER_CATALOG["rows"])
        stale = (now - _PICKER_CATALOG["built_at"]) > _PICKER_CATALOG_TTL
        may_attempt = (now - _PICKER_CATALOG["attempt_at"]) > _PICKER_CATALOG_RETRY
        if stale and may_attempt:
            _PICKER_CATALOG["attempt_at"] = now
        else:
            return rows[:limit]

    def _rebuild() -> None:
        try:
            t0 = time.monotonic()
            catalog, _meta = _get_catalog(force=False, blocking=False, refresh_counts=False)
            if catalog is None:
                return  # background catalog sync started; next attempt picks it up
            page = clients_page(
                _client(),
                sales_filter="all",
                require_telegram=True,
                limit=200,
                offset=0,
                catalog=catalog,
            )
            new_rows = list(page.get("clients") or [])
            with _PICKER_CATALOG_LOCK:
                _PICKER_CATALOG["rows"] = new_rows
                _PICKER_CATALOG["built_at"] = time.monotonic()
            log.info(
                "picker catalog rebuilt: %d rows in %.2fs",
                len(new_rows),
                time.monotonic() - t0,
            )
        except Exception:
            log.warning("picker catalog rebuild failed", exc_info=True)

    threading.Thread(target=_rebuild, name="ms-picker-catalog", daemon=True).start()
    return rows[:limit]


@router.get("/campaigns/telegram-contacts")
def get_campaign_telegram_contacts(
    q: str = Query(""),
    limit: int = Query(200, ge=1, le=500),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    """Dropdown contacts: personal Telegram + custom + export overlay + catalog."""
    try:
        t0 = time.monotonic()
        catalog_clients = _picker_catalog_rows(min(limit, 200))
        t_catalog = time.monotonic() - t0
        # Stale personal-account cache refreshes itself in the background.
        # No is_authorized() here: in gateway mode that probes the egress over
        # HTTPS (up to 30s) and the dropdown request must stay local-only —
        # the sync worker checks authorization itself and no-ops if logged out.
        want_refresh = bool(refresh)
        if not want_refresh:
            try:
                want_refresh = tg_user.contacts_stale()
            except Exception:
                want_refresh = False
        t1 = time.monotonic()
        contacts = list_outreach_contacts(
            catalog_clients=catalog_clients,
            q=q,
            limit=limit,
            refresh=want_refresh,
        )
        t_merge = time.monotonic() - t1
        sources: dict[str, int] = {}
        for c in contacts:
            src = str(c.get("source") or "?")
            sources[src] = sources.get(src, 0) + 1
        total_s = time.monotonic() - t0
        log_fn = log.warning if total_s > 2.0 else log.info
        log_fn(
            "telegram-contacts: %d rows %s (tg cache=%d, refresh=%s, "
            "catalog=%.2fs, merge=%.2fs, total=%.2fs)",
            len(contacts),
            sources,
            len(tg_user.cached_contacts()),
            want_refresh,
            t_catalog,
            t_merge,
            total_s,
        )
        return {
            "ok": True,
            "contacts": contacts,
            "total": len(contacts),
            "sources": sources,
            "timings_ms": {
                "catalog": int(t_catalog * 1000),
                "merge": int(t_merge * 1000),
                "total": int(total_s * 1000),
            },
        }
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad GET /campaigns/telegram-contacts failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/telegram-contacts")
def post_campaign_telegram_contact(body: OutreachContactBody) -> dict[str, Any]:
    """Add a custom outreach contact (@nick / t.me / chat id), resolving via Bot API."""
    try:
        contact = add_custom_contact(
            name=body.name,
            tg_nick=body.tg_nick,
            tg_chat_id=body.tg_chat_id,
            query=body.query,
            resolve=bool(body.resolve),
        )
        return {"ok": True, "contact": contact}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad POST /campaigns/telegram-contacts failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/telegram-contacts/resolve")
def post_campaign_telegram_contact_resolve(body: OutreachResolveBody) -> dict[str, Any]:
    """Resolve @nick / t.me / numeric id via getChat + local export overlay."""
    try:
        out = resolve_peer_identity(
            query=body.query,
            tg_nick=body.tg_nick,
            tg_chat_id=body.tg_chat_id,
        )
        if not out.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=str(out.get("detail") or out.get("error") or "resolve failed"),
            )
        return {"ok": True, **out}
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad POST /campaigns/telegram-contacts/resolve failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/{client_id}/telegram-check")
def post_client_telegram_check(client_id: str) -> dict[str, Any]:
    """Есть ли клиент в Telegram — независимо от истории переписки и ника.

    Личный MTProto резолвит @ник / chat id / телефон; fallback — Business
    bot getChat. Возвращает exists + резолвнутый peer; пишет в tg_verify overlay.
    """
    from plugins.moysklad.tg_verify import (
        drop_inactive_entry,
        save_verify_result,
        verify_client_peers,
    )

    try:
        phone = ""
        tg_nick = ""
        tg_chat_id = ""
        tg_conversation = ""
        catalog, _meta = _get_catalog(force=False)
        row = find_row_in_catalog(catalog, client_id) if catalog else None
        if row is not None:
            detail = build_client_detail(row)
            client = detail.get("client") or {}
            phone = str(client.get("phone") or "")
            tg_nick = str(client.get("tg_nick") or "")
            tg_chat_id = str(client.get("tg_chat_id") or "")
            tg_conversation = str(client.get("tg_conversation") or "")
        else:
            contact = get_contact(client_id)
            if contact is None:
                raise HTTPException(status_code=404, detail="client not found")
            phone = str(contact.get("phone") or "")
            tg_nick = str(contact.get("tg_nick") or "")
            tg_chat_id = str(contact.get("tg_chat_id") or "")

        result = verify_client_peers(
            client_id=client_id,
            phone=phone,
            tg_nick=tg_nick,
            tg_chat_id=tg_chat_id,
            tg_conversation=tg_conversation,
        )
        if result.get("checked") and result.get("active"):
            save_verify_result(client_id, result)
        else:
            drop_inactive_entry(client_id)
        return {
            "ok": True,
            "exists": True if result.get("active") else False,
            "checked": bool(result.get("checked") and result.get("active")),
            "chat_id": result.get("chat_id"),
            "tg_nick": result.get("resolved_nick") or tg_nick.strip().lstrip("@") or None,
            "via": result.get("via"),
            "detail": result.get("detail"),
            "error": result.get("error"),
        }
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id}/telegram-check failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/campaigns/telegram-contacts/{contact_id}")
def delete_campaign_telegram_contact(contact_id: str) -> dict[str, Any]:
    cid = (contact_id or "").strip()
    if not cid.startswith("custom:"):
        raise HTTPException(status_code=400, detail="only custom contacts can be deleted")
    if not delete_custom_contact(cid):
        raise HTTPException(status_code=404, detail="contact not found")
    return {"ok": True, "deleted": cid}


@router.post("/clients/{client_id}/ai")
def post_client_ai(
    client_id: str,
    body: ClientAiBody | None = None,
    max_orders: int = Query(25000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    provider: str = Query(""),
    model: str = Query(""),
) -> dict[str, Any]:
    """Generate AI summary + sales recommendation for a client card.

    Pass ``provider`` / ``model`` (body or query) to try different LLMs.
    """
    try:
        body = body or ClientAiBody()
        max_orders = body.max_orders or max_orders
        max_counterparties = body.max_counterparties or max_counterparties
        include_archived = bool(body.include_archived or include_archived)
        provider = (body.provider or provider or "").strip() or "openrouter"
        model = (body.model or model or "").strip() or "deepseek/deepseek-chat"
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=False,
        )
        row = _row_or_contact(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        detail = build_client_detail(row)
        # Prefer live TG thread (export overlay / local store) before LLM —
        # otherwise recommendations ignore chats that the Clients table shows.
        try:
            from plugins.moysklad.conversations import conversation_for_detail
            from plugins.moysklad.telegram_export import apply_export_overlay_to_public

            client_pub = detail.get("client") or {}
            detail["client"] = apply_export_overlay_to_public(dict(client_pub))
            detail["conversation"] = conversation_for_detail(detail)
        except Exception:
            log.debug("moysklad AI conversation enrich failed", exc_info=True)
        ai_block = generate_ai_for_detail(
            detail,
            provider=provider or None,
            model=model or None,
        )
        try:
            set_client_ai(
                client_id,
                ai_block,
                fingerprint=client_ai_facts_fingerprint(detail),
            )
        except Exception:
            log.debug("client AI cache write failed", exc_info=True)
        return _attach_cache_meta(
            {
                "ok": True,
                "client_id": client_id,
                "ai": ai_block,
                "data_thin": detail.get("data_thin"),
                "provider": provider,
                "model": model,
                "ai_cache_backend": client_ai_cache_backend_name(),
            },
            meta,
        )
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id}/ai failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/{client_id}/conversation/sync")
def post_client_conversation_sync(
    client_id: str,
    body: ConversationSyncBody | None = None,
    refresh_ai: bool = Query(
        True,
        description="Regenerate summary/recommendation after sync (default on).",
    ),
    provider: str = Query(""),
    model: str = Query(""),
) -> dict[str, Any]:
    """Pull Telegram history (gateway + personal MTProto) into the local thread.

    When new messages land (especially inbound), regenerates AI recommendation
    so the card reflects the latest chat — not a stale heuristic.
    """
    try:
        body = body or ConversationSyncBody()
        want_ai = bool(body.refresh_ai if body.refresh_ai is not None else refresh_ai)
        # Query params still win when body left defaults empty for provider/model.
        provider_name = (body.provider or provider or "").strip() or "openrouter"
        model_name = (body.model or model or "").strip() or "deepseek/deepseek-chat"
        # Explicit query false overrides body true.
        if refresh_ai is False:
            want_ai = False
        catalog, meta = _get_catalog(force=False)
        row = find_row_in_catalog(catalog, client_id) if catalog else None
        phone = ""
        tg_nick = ""
        tg_chat_id = ""
        client_name = ""
        detail = None
        if row is not None:
            detail = build_client_detail(row)
            client = detail.get("client") or {}
            phone = str(client.get("phone") or "")
            tg_nick = str(client.get("tg_nick") or "")
            tg_chat_id = str(client.get("tg_chat_id") or "")
            client_name = str(client.get("name") or "")
        else:
            # Outreach contact (custom:… or overlay-only peer) — the catalog
            # has no row, but the contact registry knows the TG peer. Without
            # this, sync reports no_tg_nick_or_phone and inbound never lands.
            contact = get_contact(client_id)
            if contact is not None:
                phone = str(contact.get("phone") or "")
                tg_nick = str(contact.get("tg_nick") or "")
                tg_chat_id = str(contact.get("tg_chat_id") or "")
                client_name = str(contact.get("name") or "")
        thread = sync_client_conversation(
            client_id=client_id,
            phone=phone,
            tg_nick=tg_nick,
            tg_chat_id=tg_chat_id,
            client_name=client_name,
        )
        payload: dict[str, Any] = {"ok": True, "conversation": thread}
        sync_meta = thread.get("sync") or {}
        imported = int(sync_meta.get("imported") or 0)
        inbound_imported = int(sync_meta.get("inbound_imported") or 0)
        # Always regen on Sync when refresh_ai=True and client known — seller
        # expects «новые рекомендации» after accounting for the thread.
        if want_ai and row is not None:
            try:
                if detail is None:
                    detail = build_client_detail(row)
                detail = dict(detail)
                detail["conversation"] = thread
                payload["ai"] = generate_ai_for_detail(
                    detail,
                    provider=provider_name or None,
                    model=model_name or None,
                )
                try:
                    set_client_ai(
                        client_id,
                        payload["ai"],
                        fingerprint=client_ai_facts_fingerprint(detail),
                    )
                except Exception:
                    log.debug("client AI cache write after sync failed", exc_info=True)
                payload["ai_refreshed"] = True
                payload["ai_reason"] = (
                    "inbound"
                    if inbound_imported > 0
                    else "imported"
                    if imported > 0
                    else "sync"
                )
            except Exception:
                log.exception("moysklad conversation sync AI refresh failed")
                payload["ai_refreshed"] = False
        else:
            payload["ai_refreshed"] = False
        return _attach_cache_meta(payload, meta)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/{id}/conversation/sync failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/conversations/telegram-sync")
def post_clients_conversations_telegram_sync(
    limit: int = Query(100, ge=1, le=300),
) -> dict[str, Any]:
    """Bulk-sync operator's personal TG dialogs into «TG conversation».

    Runs in the background (single-flight); poll the GET twin for progress.
    Matched peers = cached TG contacts/dialogs ∩ catalog rows (chat id /
    @nick / phone). Fresh threads (<6h) are skipped.
    """
    try:
        started = start_telegram_dialog_sync(max_peers=limit)
        with _TG_DIALOG_SYNC_LOCK:
            state = dict(_TG_DIALOG_SYNC_STATE)
        return {"ok": True, "started": started, "state": state}
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/conversations/telegram-sync failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/clients/conversations/telegram-sync")
def get_clients_conversations_telegram_sync() -> dict[str, Any]:
    """State of the background TG dialog sync (running flag + last stats)."""
    with _TG_DIALOG_SYNC_LOCK:
        return {"ok": True, "state": dict(_TG_DIALOG_SYNC_STATE)}


@router.post("/clients/telegram-verify")
def post_clients_telegram_verify(
    live: bool = Query(True),
    limit: int = Query(0, ge=0, le=100_000),
    delay_ms: int = Query(400, ge=0, le=5000),
    reset_inactive: bool = Query(
        False,
        description="Сбросить прежние «не найден» и перепроверить их заново.",
    ),
) -> dict[str, Any]:
    """Check колонка Телефон against Telegram (IRbots when keyed, else ImportContacts).

    Writes ``tg_active`` for Клиенты column «TG активен» and Рассылки filter.
    ``limit=0`` = all unchecked phones (IRbots phone cache skips duplicates).
    """
    try:
        if reset_inactive:
            from plugins.moysklad.tg_verify import reset_inactive_entries

            reset_inactive_entries()
        started = start_telegram_phone_verify(
            live=live, limit=limit, delay_ms=delay_ms
        )
        with _TG_PHONE_VERIFY_LOCK:
            state = dict(_TG_PHONE_VERIFY_STATE)
        return {"ok": True, "started": started, "state": state}
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/telegram-verify failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# (GET twin lives above GET /clients/{client_id} — literal beats the wildcard.)


@router.post("/clients/telegram-export/import")
def post_telegram_export_import(
    force: bool = Query(True),
    max_orders: int = Query(25000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
) -> dict[str, Any]:
    """Map ``telegram_export.json`` chats onto clients by Наименование / phone.

    Fills the **TG conversation** column + conversation store used as AI
    context on the client card. Not a separate menu — lives on Клиенты.
    """
    try:
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=False,
            blocking=True,
            refresh_counts=False,
        )
        if catalog is None:
            raise HTTPException(status_code=503, detail="catalog unavailable")
        result = _apply_telegram_export_and_recache(
            catalog,
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force_import=force,
        )
        return _attach_cache_meta(result, meta)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad telegram-export import failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class SegmentBody(BaseModel):
    """Named list of clients — a saved audience filter recipe, not a snapshot."""

    id: str = ""
    name: str = ""
    sales_filter: str = "all"
    group: str = ""
    q: str = ""
    group_source: str = "any"
    channel_kind: str = ""
    require_phone: bool = False
    require_telegram: bool = False
    vip_only: bool = False
    birthday_soon: bool = False
    days_before_event: int = 0
    event_date_from: str = ""
    event_date_to: str = ""
    last_contact_from: str = ""
    last_contact_to: str = ""
    stage: str = "all"
    entity_type: str = "all"
    loyalty_only: bool = False


@router.get("/segments")
def get_segments() -> dict[str, Any]:
    from plugins.moysklad.segments import list_segments

    return {"ok": True, "segments": list_segments()}


@router.post("/segments")
def post_segment(body: SegmentBody) -> dict[str, Any]:
    """Save the current Рассылки filter combo as a named, reusable list."""
    from plugins.moysklad.segments import FILTER_FIELDS, save_segment

    try:
        filters = {k: getattr(body, k) for k in FILTER_FIELDS}
        catalog, _meta = _get_catalog(force=False)
        page = clients_page(_client(), catalog=catalog, limit=1, offset=0, **filters)
        segment = save_segment(
            segment_id=body.id,
            name=body.name,
            filters=filters,
            matched_total=page.get("matched_total"),
        )
        return {"ok": True, "segment": segment}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad POST /segments failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/segments/{segment_id}")
def delete_segment_endpoint(segment_id: str) -> dict[str, Any]:
    from plugins.moysklad.segments import delete_segment

    return {"ok": delete_segment(segment_id)}


@router.get("/segments/{segment_id}/clients")
def get_segment_clients(
    segment_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Re-run a saved filter against the live catalog — never a stale id list."""
    from plugins.moysklad.segments import get_segment

    segment = get_segment(segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="segment not found")
    try:
        catalog, meta = _get_catalog(force=False)
        page = clients_page(
            _client(),
            catalog=catalog,
            limit=limit,
            offset=offset,
            **(segment.get("filters") or {}),
        )
        page["clients"] = enrich_clients(list(page.get("clients") or []))
        payload = {"ok": True, "segment": segment, **_strip_internal(page)}
        return _attach_cache_meta(payload, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad GET /segments/{id}/clients failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/telegram/archive")
def get_telegram_archive(
    q: str = Query(""),
    state: str = Query("all"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """ТГ архив: every personal chat from the export, matched to a client or not."""
    try:
        from plugins.moysklad.telegram_archive import list_chats

        return list_chats(q=q, state=state, limit=limit, offset=offset)
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("moysklad telegram archive list failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/telegram/archive/{chat_id}")
def get_telegram_archive_chat(chat_id: str) -> dict[str, Any]:
    """One archived chat with its full stored thread."""
    try:
        from plugins.moysklad.telegram_archive import get_chat

        result = get_chat(chat_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail="chat not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("moysklad telegram archive chat failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/telegram/archive/rebuild")
def post_telegram_archive_rebuild(
    max_orders: int = Query(25000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
) -> dict[str, Any]:
    """Re-read the export: re-match clients and re-index every chat."""
    try:
        from plugins.moysklad.telegram_archive import rebuild

        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=False,
            blocking=True,
            refresh_counts=False,
        )
        rows = list((catalog or {}).get("rows") or [])
        result = rebuild(rows, force=True)
        if catalog is not None and rows:
            # Overlay just changed — stamp the new ТГ fields back onto the cache.
            _apply_telegram_export_and_recache(
                catalog,
                max_orders=max_orders,
                max_counterparties=max_counterparties,
                include_archived=include_archived,
                force_import=False,
            )
        return _attach_cache_meta(result, meta)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("moysklad telegram archive rebuild failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/ai-fill")
def post_clients_ai_fill(body: AiFillBody) -> dict[str, Any]:
    """Fill empty CRM fields (Группы, Статус, Пол, …) via AI + heuristics.

    Stamps ``ai_fields`` for green AI markers in the Clients table
    (same idea as client_segmentation ``.ai-cell-new``).

    Pass ``ids`` for lazy evaluation of the visible page; Redis/file cache
    skips LLM on repeat unless ``force=true``.
    """
    try:
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        # When ids are provided (lazy page fill), scan the full catalog rows
        # so we can resolve those counterparties without a huge filtered page.
        if body.ids:
            rows = list(catalog.get("rows") or [])
        else:
            page = clients_page(
                _client(),
                sales_filter=body.sales_filter,
                group=body.group,
                q=body.q,
                limit=500,
                offset=0,
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                catalog=catalog,
            )
            rows = list(page.get("_rows") or catalog.get("rows") or [])
        result = fill_empty_for_rows(
            rows,
            client_ids=list(body.ids or []),
            limit=body.limit,
            use_llm=bool(body.use_llm),
            force=bool(body.force),
        )
        result["ai_fill_cache_backend"] = ai_fill_cache_backend_name()
        return _attach_cache_meta(result, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/ai-fill failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/eval/golden-clients")
def get_eval_golden_clients() -> dict[str, Any]:
    """List ~20 golden eval clients for the AI playground."""
    try:
        return list_golden_clients()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /eval/golden-clients failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/eval/golden-clients/{client_id}")
def get_eval_golden_client(client_id: str) -> dict[str, Any]:
    """Raw golden fixture row + playground stages (heuristic only)."""
    try:
        golden = get_golden_client(client_id)
        trace = run_playground(client_id=client_id, run_llm=False)
        return {"ok": True, "client": golden, **trace}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /eval/golden-clients/{id} failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/eval/playground/run")
def post_eval_playground_run(body: PlaygroundRunBody) -> dict[str, Any]:
    """Run AI playground stages (optional LLM) from golden id or edited JSON."""
    try:
        return run_playground(
            client_id=body.client_id or "",
            input_json=body.input_json or "",
            run_llm=bool(body.run_llm),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /eval/playground/run failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sync")
def post_sync(
    max_orders: int = Query(25000, ge=0, le=100_000),
    max_counterparties: int = Query(0, ge=0, le=100_000),
    include_archived: bool = Query(False),
    clear_ai: bool = Query(
        True,
        description="Also drop stale DeepSeek card summaries + outreach draft AI facts.",
    ),
) -> dict[str, Any]:
    """Force re-download from MoySklad and refresh durable cache.

    By default also clears client-card DeepSeek summaries and outreach draft
    AI caches so «Саммари AI · DeepSeek» is regenerated from fresh facts.
    """
    try:
        catalog, meta = _get_catalog(
            max_orders=max_orders,
            max_counterparties=max_counterparties,
            include_archived=include_archived,
            force=True,
        )
        ai_cleared: dict[str, Any] = {}
        if clear_ai:
            ai_cleared = {
                "client_ai": invalidate_client_ai(""),
                "outreach_drafts": invalidate_all_outreach_drafts(),
            }
        return {
            "ok": True,
            "synced": True,
            "counterparties_scanned": catalog.get("counterparties_scanned", 0),
            "orders_scanned": catalog.get("orders_scanned", 0),
            "counts": catalog.get("counts") or {},
            "ai_cleared": ai_cleared,
            **meta,
        }
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /sync failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class AiRefreshAllBody(BaseModel):
    """Invalidate and optionally regenerate DeepSeek card summaries."""

    regenerate: bool = False
    ids: list[str] = Field(default_factory=list)
    limit: int = 50
    provider: str = ""
    model: str = ""
    max_orders: int = 25000
    max_counterparties: int = 0
    include_archived: bool = False


@router.post("/clients/ai/refresh-all")
def post_clients_ai_refresh_all(body: AiRefreshAllBody | None = None) -> dict[str, Any]:
    """Drop all cached «Саммари AI · DeepSeek»; optionally regenerate a batch.

    Default: invalidate only (next card open / Обновить AI rebuilds).
    With ``regenerate=true``, re-runs DeepSeek for ``ids`` or previously
    cached client ids (capped by ``limit``) — not the full 9k catalog.
    """
    body = body or AiRefreshAllBody()
    # Capture ids before wipe — invalidate clears the file layer.
    prior_ids = list_cached_client_ids()
    cleared = {
        "client_ai": invalidate_client_ai(""),
        "outreach_drafts": invalidate_all_outreach_drafts(),
    }
    regenerated: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if body.regenerate:
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        id_filter = [str(i).strip() for i in (body.ids or []) if str(i).strip()]
        targets = id_filter or prior_ids
        # Cap to avoid surprise OpenRouter bills on full-catalog oops.
        cap = max(1, min(int(body.limit or 50), 200))
        provider = (body.provider or "").strip() or "openrouter"
        model = (body.model or "").strip() or "deepseek/deepseek-chat"
        for cid in targets[:cap]:
            row = _row_or_contact(catalog, cid)
            if row is None:
                errors.append({"id": cid, "error": "not_found"})
                continue
            try:
                detail = build_client_detail(row)
                ai_block = generate_ai_for_detail(
                    detail, provider=provider, model=model
                )
                set_client_ai(
                    cid,
                    ai_block,
                    fingerprint=client_ai_facts_fingerprint(detail),
                )
                regenerated.append(
                    {
                        "id": cid,
                        "source": ai_block.get("source"),
                        "model": ai_block.get("model"),
                    }
                )
            except Exception as exc:  # pragma: no cover
                log.warning("AI refresh-all failed for %s: %s", cid, exc)
                errors.append({"id": cid, "error": str(exc)})
        return {
            "ok": True,
            "cleared": cleared,
            "regenerated": regenerated,
            "regenerated_count": len(regenerated),
            "errors": errors,
            "cache_backend": client_ai_cache_backend_name(),
            **{
                k: meta.get(k)
                for k in ("cached", "synced_at", "synced_at_label")
                if k in meta
            },
        }
    return {
        "ok": True,
        "cleared": cleared,
        "regenerated": [],
        "regenerated_count": 0,
        "cache_backend": client_ai_cache_backend_name(),
    }


@router.post("/groups/assign")
def post_groups_assign(body: AssignBody) -> dict[str, Any]:
    try:
        catalog, _meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter,
            group=body.group,
            q=body.q,
            limit=500,
            offset=0,
            catalog=catalog,
        )
        # Assign against the current filter (matched rows), not only the page slice.
        rows = list(page.get("_rows") or [])
        proposals = propose_groups_for_rows(rows, counterparty_ids=body.ids or None)
        changed = [p for p in proposals if p.get("changed")]
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": bool(body.dry_run),
            "total": len(proposals),
            "changed": len(changed),
            "assignments": proposals if body.ids else changed,
        }
        if not body.dry_run:
            push = push_merged_tags(_client(), changed, only_changed=True)
            result["push"] = push
            if push.get("pushed"):
                _invalidate_cache(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                )
        return result
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /groups/assign failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/groups/push")
def post_groups_push(body: PushBody) -> dict[str, Any]:
    try:
        if not body.assignments:
            raise HTTPException(status_code=400, detail="assignments required")
        push = push_merged_tags(
            _client(),
            body.assignments,
            only_changed=body.only_changed,
        )
        if push.get("pushed"):
            _invalidate_cache()
        return push
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /groups/push failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/clients/stage/failed-tag")
def post_clients_failed_stage_tag(body: StageTagBody) -> dict[str, Any]:
    """Tag «не состоялся» clients in MoySklad. ``dry_run=true`` только показывает.

    Never replaces the tag list — the existing tags are merged with the stage
    tag, same as «Предложить группы».
    """
    try:
        from plugins.moysklad.order_status import FAILED_STAGE_TAG

        tag = (body.tag or FAILED_STAGE_TAG).strip()
        if not tag:
            raise HTTPException(status_code=400, detail="tag required")

        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            blocking=True,
        )
        if catalog is None:
            raise HTTPException(status_code=503, detail="catalog unavailable")
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter,
            q=body.q,
            stage="failed",
            limit=0,
            offset=0,
            catalog=catalog,
        )
        rows = list(page.get("_rows") or [])
        wanted = {str(i).strip() for i in (body.ids or []) if str(i).strip()}
        assignments: list[dict[str, Any]] = []
        for row in rows:
            cp_id = str(row.get("_moysklad_id") or "").strip()
            if not cp_id or (wanted and cp_id not in wanted):
                continue
            existing = [
                str(t).strip()
                for t in (row.get("_moysklad_tags") or [])
                if str(t).strip()
            ]
            already = any(t.lower() == tag.lower() for t in existing)
            assignments.append({
                "id": cp_id,
                "name": row.get("Наименование") or "",
                "existing": existing,
                "merged": existing if already else [*existing, tag],
                "changed": not already,
                "stage": row.get("client_stage") or row.get("Тип клиента") or "",
                "reason": row.get("client_stage_reason") or "",
                "order_count": int(row.get("order_count") or 0),
            })
        changed = [a for a in assignments if a["changed"]]
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": bool(body.dry_run),
            "tag": tag,
            "total": len(assignments),
            "changed": len(changed),
            "assignments": assignments if wanted else changed,
        }
        if not body.dry_run:
            push = push_merged_tags(_client(), changed, only_changed=True)
            result["push"] = push
            result["ok"] = bool(push.get("ok"))
            if push.get("pushed"):
                _invalidate_cache(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                )
        return _attach_cache_meta(result, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /clients/stage/failed-tag failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/groups/taxonomy")
def get_groups_taxonomy() -> dict[str, Any]:
    return {"ok": True, "groups": load_taxonomy()}


@router.post("/groups/recalculate/propose")
def post_groups_recalculate_propose(body: RecalculateProposeBody) -> dict[str, Any]:
    """LLM (or heuristic) proposes a new group taxonomy for the current audience."""
    try:
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter,
            group=body.group,
            q=body.q,
            channel_kind=body.channel_kind,
            require_phone=body.require_phone,
            require_telegram=body.require_telegram,
            vip_only=body.vip_only,
            birthday_soon=body.birthday_soon,
            group_source=getattr(body, 'group_source', 'any') or 'any',
            days_before_event=int(getattr(body, 'days_before_event', 0) or 0),
            event_date_from=str(getattr(body, 'event_date_from', '') or ''),
            event_date_to=str(getattr(body, 'event_date_to', '') or ''),
            limit=0,
            offset=0,
            catalog=catalog,
        )
        rows = list(page.get("_rows") or [])
        proposal = propose_taxonomy(rows, sales_filter=body.sales_filter)
        proposal["cached"] = meta.get("cached")
        return proposal
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /groups/recalculate/propose failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/groups/recalculate/apply")
def post_groups_recalculate_apply(body: RecalculateApplyBody) -> dict[str, Any]:
    """Persist taxonomy and reassign labels; optional push to MoySklad."""
    try:
        if not body.groups:
            raise HTTPException(status_code=400, detail="groups required")
        saved = save_taxonomy(body.groups)
        catalog, _meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter,
            group=body.group,
            q=body.q,
            channel_kind=body.channel_kind,
            require_phone=body.require_phone,
            require_telegram=body.require_telegram,
            vip_only=body.vip_only,
            birthday_soon=body.birthday_soon,
            group_source=getattr(body, 'group_source', 'any') or 'any',
            days_before_event=int(getattr(body, 'days_before_event', 0) or 0),
            event_date_from=str(getattr(body, 'event_date_from', '') or ''),
            event_date_to=str(getattr(body, 'event_date_to', '') or ''),
            limit=0,
            offset=0,
            catalog=catalog,
        )
        rows = list(page.get("_rows") or [])
        assignments = assign_to_taxonomy(rows, saved)
        changed = [a for a in assignments if a.get("changed")]
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": bool(body.dry_run),
            "groups": saved,
            "total": len(assignments),
            "changed": len(changed),
            "assignments": changed[:200],
        }
        if not body.dry_run and body.push:
            push = push_merged_tags(_client(), changed, only_changed=True)
            result["push"] = push
            if push.get("pushed"):
                _invalidate_cache(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                )
        elif not body.dry_run:
            # Taxonomy saved; invalidate so facet counts refresh on next read.
            _invalidate_cache(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
            )
        return result
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /groups/recalculate/apply failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/campaigns")
def get_campaigns() -> dict[str, Any]:
    return {"ok": True, "campaigns": list_campaigns()}


@router.get("/campaigns/seller-settings")
def get_campaign_seller_settings() -> dict[str, Any]:
    settings = get_seller_settings()
    return {
        "ok": True,
        **settings,
        "telegram": telegram_send_status(),
        "telegram_account": telegram_account_snapshot(
            settings.get("telegram_business_connection_id") or None
        ),
    }


@router.put("/campaigns/seller-settings")
def put_campaign_seller_settings(body: SellerSettingsBody) -> dict[str, Any]:
    saved = save_seller_settings(
        seller_name=body.seller_name,
        seller_facts=body.seller_facts,
        telegram_business_connection_id=body.telegram_business_connection_id,
    )
    return {
        "ok": True,
        **saved,
        "telegram": telegram_send_status(),
        "telegram_account": telegram_account_snapshot(
            saved.get("telegram_business_connection_id") or None
        ),
    }


@router.get("/campaigns/telegram-account")
def get_campaign_telegram_account() -> dict[str, Any]:
    """Probe Business connection for campaigns UI (account @nick / rights)."""
    settings = get_seller_settings()
    snap = telegram_account_snapshot(
        settings.get("telegram_business_connection_id") or None
    )
    return {
        "ok": True,
        **snap,
        "send_mode": telegram_send_mode(),
        "telegram_user": telegram_user_status(probe=False),
    }


@router.get("/campaigns/draft-cache")
def get_campaign_draft_cache(
    client_id: str = Query(""),
    channel: str = Query("telegram"),
) -> dict[str, Any]:
    """Load durable outreach draft (Redis → file) for a client + channel."""
    cid = (client_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="client_id required")
    draft = get_outreach_draft(cid, channel)
    return {
        "ok": True,
        "hit": draft is not None,
        "draft": draft,
        "cache_backend": outreach_cache_backend_name(),
    }


@router.put("/campaigns/draft-cache")
def put_campaign_draft_cache(body: OutreachDraftCacheBody) -> dict[str, Any]:
    """Persist outreach draft to Redis/file (manual save / button results)."""
    cid = (body.client_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="client_id required")
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    try:
        envelope = set_outreach_draft(
            cid,
            body.channel,
            {
                "message": message,
                "grounding_notes": body.grounding_notes,
                "source": body.source,
                "status": body.status,
                "client_name": body.client_name,
                "title": body.title,
                "facts": body.facts or {},
                "sanity": body.sanity,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "draft": envelope.get("draft"),
        "saved_at": envelope.get("saved_at"),
        "cache_backend": outreach_cache_backend_name(),
    }


@router.post("/campaigns/chat")
def post_campaign_chat(body: OutreachChatBody) -> dict[str, Any]:
    """Чат доработки черновика: та же фактология, что у generate, плюс
    история переписки оператора с ассистентом. Возвращает reply + message."""
    try:
        client_id = (body.client_id or "").strip()
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")
        catalog, meta = _get_catalog(force=False)
        row = _row_or_contact(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        detail = build_client_detail(row)
        seller_name, seller_facts = _resolve_seller(
            body.seller_name, body.seller_facts
        )
        from plugins.moysklad.outreach import chat_refine_message

        out = chat_refine_message(
            detail,
            channel=body.channel,
            draft=body.draft,
            chat=list(body.messages or []),
            provider=body.provider,
            model=body.model,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        if not out.get("ok"):
            raise HTTPException(
                status_code=502, detail=str(out.get("error") or "chat failed")
            )
        return _attach_cache_meta({"ok": True, **out}, meta)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/chat failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/campaigns/skills")
def get_campaign_skills() -> dict[str, Any]:
    """Сохранённые примеры навыка (few-shot для генерации)."""
    from plugins.moysklad.outreach_skills import list_skills

    return {"ok": True, "skills": list_skills()}


@router.post("/campaigns/skills")
def post_campaign_skill(body: SkillSaveBody) -> dict[str, Any]:
    """Сохранить отправленное сообщение в навык генерации."""
    try:
        from plugins.moysklad.outreach_skills import list_skills, save_skill

        item = save_skill(text=body.text, notes=body.notes)
        return {"ok": True, "skill": item, "total": len(list_skills())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad POST /campaigns/skills failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/campaigns/skills/{skill_id}")
def delete_campaign_skill(skill_id: str) -> dict[str, Any]:
    from plugins.moysklad.outreach_skills import delete_skill

    if not delete_skill(skill_id):
        raise HTTPException(status_code=404, detail="skill not found")
    return {"ok": True, "deleted": skill_id}


@router.post("/campaigns/generate")
def post_campaign_generate(body: OutreachGenerateBody) -> dict[str, Any]:
    """AI (or heuristic) outreach text + facts panel for one client.

    Uses the same durable catalog cache as /clients (marketplace/direct).
    Serves Redis/file draft cache unless ``refresh_ai`` forces a new LLM pass.
    """
    try:
        client_id = (body.client_id or "").strip()
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        row = _row_or_contact(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        force = bool(body.refresh_ai)
        if not force:
            # Fingerprint against live catalog facts so stale DeepSeek
            # history_profile from an old draft is never served.
            live_fp = client_ai_facts_fingerprint(build_client_detail(row))
            cached = get_outreach_draft(
                client_id, body.channel, facts_fingerprint=live_fp
            )
            msg = str((cached or {}).get("message") or "").strip()
            if cached and msg:
                return _attach_cache_meta(
                    {
                        "ok": True,
                        "message": msg,
                        "grounding_notes": cached.get("grounding_notes") or "",
                        "source": cached.get("source") or "redis-cache",
                        "from_cache": True,
                        "cached": True,
                        "facts": cached.get("facts")
                        if isinstance(cached.get("facts"), dict)
                        else {},
                        "sanity": cached.get("sanity")
                        if isinstance(cached.get("sanity"), dict)
                        else None,
                        "client_id": client_id,
                        "client_name": cached.get("client_name") or "",
                        "seller_name": seller_name,
                        "seller_facts": seller_facts,
                        "channel": body.channel,
                        "cache_backend": outreach_cache_backend_name(),
                    },
                    meta,
                )
        outreach = build_outreach_for_row(
            row,
            channel=body.channel,
            refresh_ai=force,
            seller_name=seller_name,
            seller_facts=seller_facts,
            use_draft_cache=True,
            force_refresh=force,
        )
        return _attach_cache_meta(
            {
                "ok": True,
                **outreach,
                "cache_backend": outreach_cache_backend_name(),
            },
            meta,
        )
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/generate failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/generate/stream")
def post_campaign_generate_stream(body: OutreachGenerateBody) -> Any:
    """NDJSON stream: status → delta* → (replace?) → done.

    First status event is emitted before catalog I/O so the UI is not stuck
    on a silent wait. Tokens stream as plain text (or ``message`` JSON field).
    Cached drafts short-circuit when ``refresh_ai`` is false.
    """
    client_id = (body.client_id or "").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")
    seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
    if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
        save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
    force = bool(body.refresh_ai)

    def _events() -> Iterator[dict[str, Any]]:
        if not force:
            cached = get_outreach_draft(client_id, body.channel)
            msg = str((cached or {}).get("message") or "").strip()
            if cached and msg:
                yield {"type": "status", "text": "Из кэша Redis/файл…"}
                yield {
                    "type": "done",
                    "ok": True,
                    "message": msg,
                    "grounding_notes": cached.get("grounding_notes") or "",
                    "source": cached.get("source") or "redis-cache",
                    "from_cache": True,
                    "cached": True,
                    "cache_backend": outreach_cache_backend_name(),
                    "facts": cached.get("facts")
                    if isinstance(cached.get("facts"), dict)
                    else {},
                    "sanity": cached.get("sanity")
                    if isinstance(cached.get("sanity"), dict)
                    else None,
                    "client_id": client_id,
                    "client_name": cached.get("client_name") or "",
                    "channel": body.channel,
                }
                return

        yield {"type": "status", "text": "Генерируем креативный текст…"}
        try:
            catalog, _meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = _row_or_contact(catalog, client_id)
            if row is None:
                yield {"type": "error", "error": "client not found in catalog"}
                return
            for ev in iter_generate_outreach_for_row_events(
                row,
                channel=body.channel,
                refresh_ai=force,
                seller_name=seller_name,
                seller_facts=seller_facts,
            ):
                if ev.get("type") == "status":
                    continue  # already emitted above
                if ev.get("type") == "done":
                    ev = _persist_outreach_draft_from_done(
                        ev,
                        client_id=client_id,
                        channel=body.channel,
                        status="AI сгенерировал креативный текст — можно править вручную.",
                    )
                yield ev
        except MoySkladError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception("moysklad /campaigns/generate/stream failed mid-stream")
            yield {"type": "error", "error": str(exc)}

    return _ndjson_response(_events())

@router.post("/campaigns/rewrite")
def post_campaign_rewrite(body: OutreachRewriteBody) -> dict[str, Any]:
    """Rewrite current draft to be more sales-oriented and human."""
    try:
        draft = (body.message or "").strip()
        if not draft:
            raise HTTPException(status_code=400, detail="message required")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        detail: dict[str, Any] | None = None
        client_id = (body.client_id or "").strip()
        meta = None
        if client_id:
            catalog, meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = find_row_in_catalog(catalog, client_id)
            if row is not None:
                detail = build_client_detail(row)
        result = rewrite_outreach_message(
            draft,
            channel=body.channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
            detail=detail,
        )
        if meta is not None:
            return _attach_cache_meta({"ok": True, **result}, meta)
        return {"ok": True, **result}
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/rewrite failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/rewrite/stream")
def post_campaign_rewrite_stream(body: OutreachRewriteBody) -> Any:
    """NDJSON stream for rewrite (same event shape as generate/stream)."""
    draft = (body.message or "").strip()
    if not draft:
        raise HTTPException(status_code=400, detail="message required")
    seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
    if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
        save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
    client_id = (body.client_id or "").strip()

    def _events() -> Iterator[dict[str, Any]]:
        yield {"type": "status", "text": "Переписываем продающе…"}
        try:
            detail: dict[str, Any] | None = None
            if client_id:
                catalog, _meta = _get_catalog(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                    force=False,
                )
                row = find_row_in_catalog(catalog, client_id)
                if row is not None:
                    detail = build_client_detail(row)
            for ev in iter_rewrite_outreach_events(
                draft,
                channel=body.channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
                detail=detail,
            ):
                if ev.get("type") == "status":
                    continue
                if ev.get("type") == "done":
                    ev = _persist_outreach_draft_from_done(
                        ev,
                        client_id=client_id,
                        channel=body.channel,
                        status="Текст обновлён: продающе и по-человечески.",
                    )
                yield ev
        except MoySkladError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception("moysklad /campaigns/rewrite/stream failed mid-stream")
            yield {"type": "error", "error": str(exc)}

    return _ndjson_response(_events())


@router.post("/campaigns/suggest-bouquet")
def post_campaign_suggest_bouquet(body: OutreachGenerateBody) -> dict[str, Any]:
    """Suggest a concrete bouquet from the client's order history."""
    try:
        client_id = (body.client_id or "").strip()
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id required")
        catalog, meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        row = _row_or_contact(catalog, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="client not found in catalog")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        detail = build_client_detail(row)
        result = suggest_historical_bouquet_message(
            detail,
            channel=body.channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        result["client_id"] = (detail.get("client") or {}).get("id")
        result["client_name"] = (detail.get("client") or {}).get("name")
        return _attach_cache_meta({"ok": True, **result}, meta)
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/suggest-bouquet failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/suggest-bouquet/stream")
def post_campaign_suggest_bouquet_stream(body: OutreachGenerateBody) -> Any:
    """NDJSON stream for historical bouquet suggestion."""
    client_id = (body.client_id or "").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")
    seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
    if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
        save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)

    def _events() -> Iterator[dict[str, Any]]:
        yield {"type": "status", "text": "Подбираем букет из истории…"}
        try:
            catalog, _meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = _row_or_contact(catalog, client_id)
            if row is None:
                yield {"type": "error", "error": "client not found in catalog"}
                return
            detail = build_client_detail(row)
            client_id_out = (detail.get("client") or {}).get("id")
            client_name_out = (detail.get("client") or {}).get("name")
            for ev in iter_suggest_bouquet_events(
                detail,
                channel=body.channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
            ):
                if ev.get("type") == "status":
                    continue
                if ev.get("type") == "done":
                    ev = {
                        **ev,
                        "client_id": client_id_out,
                        "client_name": client_name_out,
                    }
                    ev = _persist_outreach_draft_from_done(
                        ev,
                        client_id=str(client_id_out or client_id),
                        channel=body.channel,
                        status="Предложен конкретный букет из истории заказов.",
                    )
                yield ev
        except MoySkladError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception("moysklad /campaigns/suggest-bouquet/stream mid-stream")
            yield {"type": "error", "error": str(exc)}

    return _ndjson_response(_events())


@router.post("/campaigns/paraphrase")
def post_campaign_paraphrase(body: OutreachRewriteBody) -> dict[str, Any]:
    """Full paraphrase — must differ from generate and sales rewrite."""
    try:
        draft = (body.message or "").strip()
        if not draft:
            raise HTTPException(status_code=400, detail="message required")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
        detail: dict[str, Any] | None = None
        client_id = (body.client_id or "").strip()
        meta = None
        if client_id:
            catalog, meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = find_row_in_catalog(catalog, client_id)
            if row is not None:
                detail = build_client_detail(row)
        result = paraphrase_outreach_message(
            draft,
            channel=body.channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
            detail=detail,
        )
        if meta is not None:
            return _attach_cache_meta({"ok": True, **result}, meta)
        return {"ok": True, **result}
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/paraphrase failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/paraphrase/stream")
def post_campaign_paraphrase_stream(body: OutreachRewriteBody) -> Any:
    """NDJSON stream for full paraphrase."""
    draft = (body.message or "").strip()
    if not draft:
        raise HTTPException(status_code=400, detail="message required")
    seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
    if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
        save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)
    client_id = (body.client_id or "").strip()

    def _events() -> Iterator[dict[str, Any]]:
        yield {"type": "status", "text": "Делаем полную парафразу…"}
        try:
            detail: dict[str, Any] | None = None
            if client_id:
                catalog, _meta = _get_catalog(
                    max_orders=body.max_orders,
                    max_counterparties=body.max_counterparties,
                    include_archived=body.include_archived,
                    force=False,
                )
                row = find_row_in_catalog(catalog, client_id)
                if row is not None:
                    detail = build_client_detail(row)
            for ev in iter_paraphrase_outreach_events(
                draft,
                channel=body.channel,
                seller_name=seller_name,
                seller_facts=seller_facts,
                detail=detail,
            ):
                if ev.get("type") == "status":
                    continue
                if ev.get("type") == "done":
                    ev = _persist_outreach_draft_from_done(
                        ev,
                        client_id=client_id,
                        channel=body.channel,
                        status="Полная парафраза: формулировки сменены, факты те же.",
                    )
                yield ev
        except MoySkladError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            log.exception("moysklad /campaigns/paraphrase/stream mid-stream")
            yield {"type": "error", "error": str(exc)}

    return _ndjson_response(_events())


@router.post("/campaigns/personalize/stream")
def post_campaign_personalize_stream(body: OutreachPersonalizeBody) -> Any:
    """Batch NDJSON: batch_start → client_done* → batch_done (parallel LLM)."""
    try:
        limit = max(1, min(int(body.limit or 20), 200))
        workers = max(1, min(int(body.max_workers or 6), 8))
        catalog, _meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            force=False,
        )
        page = clients_page(
            _client(),
            sales_filter=body.sales_filter or "all",
            group=body.group or "",
            q=body.q or "",
            channel_kind=body.channel_kind or "",
            require_phone=bool(body.require_phone),
            require_telegram=bool(body.require_telegram),
            vip_only=bool(body.vip_only),
            birthday_soon=bool(body.birthday_soon),
            group_source=getattr(body, 'group_source', 'any') or 'any',
            days_before_event=int(getattr(body, 'days_before_event', 0) or 0),
            event_date_from=str(getattr(body, 'event_date_from', '') or ''),
            event_date_to=str(getattr(body, 'event_date_to', '') or ''),
            limit=limit,
            offset=0,
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
            catalog=catalog,
        )
        rows = list(page.get("clients") or [])[:limit]
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
            save_seller_settings(seller_name=seller_name, seller_facts=seller_facts)

        def _events() -> Iterator[dict[str, Any]]:
            try:
                yield from iter_personalize_batch_events(
                    rows,
                    channel=body.channel,
                    seller_name=seller_name,
                    seller_facts=seller_facts,
                    max_workers=workers,
                )
            except Exception as exc:  # pragma: no cover
                log.exception("moysklad /campaigns/personalize/stream failed")
                yield {"type": "error", "error": str(exc)}

        return _ndjson_response(_events())
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/personalize/stream setup failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns/sanity")
def post_campaign_sanity(body: OutreachSanityBody) -> dict[str, Any]:
    """Second-pass meaning check: debt/risk must not produce flower upsell."""
    try:
        draft = (body.message or "").strip()
        if not draft:
            raise HTTPException(status_code=400, detail="message required")
        seller_name, seller_facts = _resolve_seller(body.seller_name, body.seller_facts)
        detail: dict[str, Any] | None = None
        client_id = (body.client_id or "").strip()
        meta = None
        if client_id:
            catalog, meta = _get_catalog(
                max_orders=body.max_orders,
                max_counterparties=body.max_counterparties,
                include_archived=body.include_archived,
                force=False,
            )
            row = find_row_in_catalog(catalog, client_id)
            if row is not None:
                detail = build_client_detail(row)
        sanity = sanity_check_outreach_message(
            draft,
            detail,
            channel=body.channel,
            seller_name=seller_name,
            seller_facts=seller_facts,
        )
        message = draft
        if body.apply_revision and not sanity.get("ok") and sanity.get("revised_text"):
            message = str(sanity["revised_text"])
            sanity = {**sanity, "auto_revised": True}
        payload = {
            "ok": True,
            "message": message,
            "sanity": sanity,
            "facts": facts_panel(detail) if detail else {},
            "seller_name": seller_name,
            "seller_facts": seller_facts,
            "channel": (body.channel or "telegram").strip().lower(),
        }
        if meta is not None:
            return _attach_cache_meta(payload, meta)
        return payload
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns/sanity failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/campaigns")
def post_campaign(body: CampaignCreateBody) -> dict[str, Any]:
    """Create a draft. Audience filters share the Clients catalog cache.

    Pass ``client_id`` for a personalized 1:1 draft (facts + optional AI text).
    ``mode=auto`` without offer runs outreach generation when client_id is set.
    """
    try:
        catalog, _meta = _get_catalog(
            max_orders=body.max_orders,
            max_counterparties=body.max_counterparties,
            include_archived=body.include_archived,
        )
        client_id = (body.client_id or "").strip()
        facts: dict[str, Any] = {}
        recommendation = ""
        grounding_notes = ""
        ai_source = ""
        client_name = ""
        offer = body.offer
        preview: list[dict[str, Any]] = []
        audience_count = 0
        sales_filter = body.sales_filter or "all"

        if client_id:
            row = _row_or_contact(catalog, client_id)
            if row is None:
                raise HTTPException(
                    status_code=404, detail="client not found in catalog"
                )
            detail = build_client_detail(row)
            client = detail.get("client") or {}
            client_name = str(client.get("name") or "")
            sales_filter = str(client.get("sales_type") or sales_filter)
            # Normalize UI filter ids when sales_type is a display label.
            st = sales_filter.lower().replace("ё", "е")
            if "маркет" in st:
                sales_filter = "marketplace"
            elif "прям" in st:
                sales_filter = "direct"
            facts = facts_panel(detail)
            recommendation = str((detail.get("ai") or {}).get("recommendation") or "")
            seller_name, seller_facts = _resolve_seller(
                body.seller_name, body.seller_facts
            )
            if (body.seller_name or "").strip() or (body.seller_facts or "").strip():
                save_seller_settings(
                    seller_name=seller_name, seller_facts=seller_facts
                )
            want_ai = body.mode == "auto" or body.generate_ai or not (offer or "").strip()
            if want_ai and not (offer or "").strip():
                outreach = generate_outreach_message(
                    detail,
                    channel=body.channel,
                    refresh_ai=True,
                    seller_name=seller_name,
                    seller_facts=seller_facts,
                )
                offer = outreach.get("message") or ""
                facts = outreach.get("facts") or facts
                grounding_notes = str(outreach.get("grounding_notes") or "")
                ai_source = str(outreach.get("source") or "")
                recommendation = str(
                    (outreach.get("ai") or detail.get("ai") or {}).get(
                        "recommendation"
                    )
                    or recommendation
                )
            preview = [
                {
                    "id": client.get("id"),
                    "name": client_name,
                    "phone": client.get("phone"),
                    "email": client.get("email"),
                    "sales_type": client.get("sales_type"),
                }
            ]
            audience_count = 1
            title = body.title
            if not (title or "").strip() or title == "Рассылка":
                title = f"Черновик · {client_name or client_id}"
        else:
            page = clients_page(
                _client(),
                sales_filter=body.sales_filter,
                group=body.group,
                q=body.q,
                channel_kind=body.channel_kind,
                require_phone=body.require_phone,
                require_telegram=body.require_telegram,
                vip_only=body.vip_only,
                birthday_soon=body.birthday_soon,
                group_source=body.group_source or "any",
                days_before_event=int(body.days_before_event or 0),
                event_date_from=body.event_date_from or "",
                event_date_to=body.event_date_to or "",
                last_contact_from=body.last_contact_from or "",
                last_contact_to=body.last_contact_to or "",
                limit=20 if body.include_preview else 1,
                offset=0,
                catalog=catalog,
            )
            sales_filter = page.get("sales_filter") or body.sales_filter
            audience_count = int(page.get("matched_total") or 0)
            if body.include_preview:
                for row in page.get("clients") or []:
                    preview.append(
                        {
                            "id": row.get("id"),
                            "name": row.get("name"),
                            "phone": row.get("phone"),
                            "email": row.get("email"),
                            "sales_type": row.get("sales_type"),
                        }
                    )
            if body.mode == "auto" and not (offer or "").strip():
                # Mass filter auto: shared template for the group (not per-client).
                seller_name, _seller_facts = _resolve_seller(
                    body.seller_name, body.seller_facts
                )
                intro = (
                    f"Это {seller_name}"
                    if seller_name
                    else "Пишем из цветочного магазина"
                )
                tag_bit = (
                    f' по тегу «{body.group}»'
                    if (body.group or "").strip()
                    else ""
                )
                occasion = " к предстоящему поводу" if body.birthday_soon else ""
                offer = (
                    f"Здравствуйте! {intro}{tag_bit}{occasion}. "
                    "Если удобно подобрать букет — напишите, подберём спокойно. "
                    f"(Общий текст для аудитории {audience_count} чел.; "
                    "персонализация по клиентам — отдельным шагом.)"
                )
                ai_source = "filter_group_stub"
            title = body.title
            if not (title or "").strip() or title in ("Рассылка", "Рассылка по фильтрам"):
                bits = [sales_filter]
                if body.channel_kind:
                    bits.append(body.channel_kind)
                if body.group:
                    bits.append(body.group)
                if body.birthday_soon:
                    bits.append("др/событие")
                if body.vip_only:
                    bits.append("VIP")
                title = "Массовая · " + " · ".join(bits)

        audience_filters = {
            "sales_filter": sales_filter,
            "group": body.group or "",
            "q": body.q or "",
            "channel_kind": body.channel_kind or "",
            "require_phone": bool(body.require_phone),
            "require_telegram": bool(body.require_telegram),
            "vip_only": bool(body.vip_only),
            "birthday_soon": bool(body.birthday_soon),
            "group_source": body.group_source or "any",
            "days_before_event": int(body.days_before_event or 0),
            "event_date_from": body.event_date_from or "",
            "event_date_to": body.event_date_to or "",
            "personalize": bool(body.personalize),
        }

        item = create_draft(
            title=title,
            channel=body.channel,
            mode=body.mode,
            offer=offer,
            sales_filter=sales_filter,
            group=body.group,
            q=body.q,
            audience_count=audience_count,
            audience_preview=preview,
            audience_filters=audience_filters,
            client_id=client_id,
            client_name=client_name,
            facts=facts,
            recommendation=recommendation,
            grounding_notes=grounding_notes,
            ai_source=ai_source,
            personalize_pending=bool(body.personalize) and not client_id,
        )
        return {"ok": True, "campaign": item}
    except HTTPException:
        raise
    except MoySkladError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /campaigns create failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/campaigns/{campaign_id}")
def remove_campaign(campaign_id: str) -> dict[str, Any]:
    ok = delete_campaign(campaign_id)
    if not ok:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True, "deleted": campaign_id}


@router.get("/cards/marketplaces")
def get_marketplace_cards(
    limit: int = Query(default=100, ge=1, le=1000),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Карточки товаров с маркетплейсов (Flowwow live, Яндекс — по токену).

    Cached 5 min; ``force=true`` refetches. Feeds the «Карточки» tab.
    """
    try:
        from plugins.moysklad.marketplace_cards import marketplace_cards_payload

        return {"ok": True, **marketplace_cards_payload(limit=limit, force=force)}
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/marketplaces failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class CardsChatBody(BaseModel):
    """One chat turn of the «Карточки» report analyst."""

    messages: list[dict[str, str]] = Field(default_factory=list)
    provider: str = ""
    model: str = ""


@router.post("/cards/chat")
def post_cards_chat(body: CardsChatBody) -> dict[str, Any]:
    """Консультант по карточкам: размещение, продвижение, исправление,
    добавление. Context = combined card list of both marketplaces."""
    try:
        from datetime import date as _date

        from plugins.moysklad.cards_chat import cards_advisor_reply
        from plugins.moysklad.marketplace_cards import marketplace_cards_payload

        # Full catalog (not the 100-card page) — the advisor must see everything.
        cards = marketplace_cards_payload(limit=1000)
        combined = list((cards or {}).get("combined") or [])

        channel_dynamics: dict[str, Any] = {}
        try:
            from plugins.moysklad.dashboard_analytics import build_analytics
            from plugins.moysklad.report_backtest import extract_month_report

            catalog, _meta = _get_catalog(force=False, blocking=True)
            analytics = build_analytics(
                list((catalog or {}).get("rows") or []), today=_date.today()
            )
            month_report = extract_month_report(analytics)
            for month_id in sorted(month_report)[-3:]:
                channel_dynamics[month_id] = {
                    ch: [cell.get("turnover"), cell.get("orders")]
                    for ch, cell in sorted(month_report[month_id].items())
                    if cell.get("orders")
                }
        except Exception:
            log.warning("cards chat: channel dynamics unavailable", exc_info=True)

        yandex_stats = None
        try:
            from plugins.moysklad.yandex_stats import yandex_monthly_stats_cached

            stats = yandex_monthly_stats_cached(months=3)
            if stats:
                yandex_stats = stats.get("months")
        except Exception:
            log.warning("cards chat: yandex stats unavailable", exc_info=True)

        out = cards_advisor_reply(
            list(body.messages or []),
            combined=combined,
            channel_dynamics=channel_dynamics or None,
            yandex_stats=yandex_stats,
            provider=body.provider,
            model=body.model,
        )
        if not out.get("ok"):
            raise HTTPException(
                status_code=502, detail=str(out.get("error") or "chat failed")
            )
        return {"ok": True, "reply": out["reply"], "followups": out.get("followups") or []}
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/chat failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/dashboard/chat")
def post_dashboard_chat(body: CardsChatBody) -> dict[str, Any]:
    """Чат-аналитик отчёта (методика созвона 22.08) — живёт в Дашборде.

    Context = report computed from the cached MoySklad catalog + last
    marketplace cards summary. The assistant must name missing figures
    instead of inventing them.
    """
    try:
        from datetime import date as _date

        from plugins.moysklad.cards_chat import cards_chat_reply
        from plugins.moysklad.dashboard_analytics import build_analytics
        from plugins.moysklad.marketplace_cards import cached_payload
        from plugins.moysklad.report_backtest import extract_month_report

        catalog, meta = _get_catalog(force=False, blocking=True)
        rows = list((catalog or {}).get("rows") or [])
        analytics = build_analytics(rows, today=_date.today())
        month_report = extract_month_report(analytics)

        cards_summary: dict[str, Any] | None = None
        cards = cached_payload()
        if cards:
            fw = cards.get("flowwow") or {}
            ya = cards.get("yandex") or {}
            cards_summary = {
                "flowwow": {
                    "shop": fw.get("shop"),
                    "cards_total": fw.get("total"),
                    "configured": fw.get("configured"),
                },
                "yandex_market": {
                    "business": ya.get("business"),
                    "cards_total": ya.get("total"),
                    "configured": ya.get("configured"),
                },
            }

        out = cards_chat_reply(
            list(body.messages or []),
            month_report=month_report,
            cards_summary=cards_summary,
            provider=body.provider,
            model=body.model,
        )
        if not out.get("ok"):
            raise HTTPException(
                status_code=502, detail=str(out.get("error") or "chat failed")
            )
        return _attach_cache_meta(
            {"ok": True, "reply": out["reply"], "followups": out.get("followups") or []},
            meta,
        )
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /dashboard/chat failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/cards/recommendations")
def get_cards_recommendations(
    force: bool = Query(default=False),
    rating_threshold: int = Query(default=85, ge=1, le=100),
    min_photos: int = Query(default=3, ge=1, le=20),
    price_gap_min: float = Query(default=0.10, ge=0.0, le=1.0),
    cap: int = Query(default=25, ge=1, le=200),
    name: str | None = Query(
        default=None,
        description="If set, return concrete recommendations for this card only",
    ),
) -> dict[str, Any]:
    """Рекомендации по карточкам, посчитанные из данных (без LLM, мгновенно).

    Параметры модели настраиваются с фронта (порог рейтинга, минимум фото,
    порог разницы цен, размер блока); ``meta`` объясняет правило и источник
    данных каждого блока.

    With ``name``, returns ``recommendations`` for that one card (API facts +
    marketplace KB docs) instead of the aggregate blocks.
    """
    try:
        from plugins.moysklad.cards_recommendations import (
            block_meta,
            build_recommendations,
            recommendations_for_card,
        )
        from plugins.moysklad.marketplace_cards import marketplace_cards_payload
        from plugins.moysklad.marketplace_kb import knowledge_payload

        cards = marketplace_cards_payload(limit=1000, force=force)
        combined = list((cards or {}).get("combined") or [])
        meta = block_meta(
            rating_threshold=rating_threshold,
            min_photos=min_photos,
            price_gap_min=price_gap_min,
        )
        knowledge = knowledge_payload()
        params = {
            "rating_threshold": rating_threshold,
            "min_photos": min_photos,
            "price_gap_min": price_gap_min,
            "cap": cap,
        }
        card_name = str(name or "").strip()
        if card_name:
            row = next(
                (
                    r
                    for r in combined
                    if str((r or {}).get("name") or "").strip() == card_name
                ),
                None,
            )
            return {
                "ok": True,
                "name": card_name,
                "found": row is not None,
                "cards_total": len(combined),
                "generated_at": cards.get("generated_at"),
                "params": params,
                "meta": meta,
                "knowledge": knowledge,
                "recommendations": recommendations_for_card(row or {"name": card_name}),
            }
        return {
            "ok": True,
            "cards_total": len(combined),
            "generated_at": cards.get("generated_at"),
            "params": params,
            "meta": meta,
            # Seller-docs / KB grounding (placement + promotion), not LLM fluff.
            "knowledge": knowledge,
            **build_recommendations(
                combined,
                rating_threshold=rating_threshold,
                min_photos=min_photos,
                price_gap_min=price_gap_min,
                cap=cap,
            ),
        }
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/recommendations failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class CardDraftBody(BaseModel):
    """Generate per-marketplace descriptions for one MS bouquet."""

    name: str = ""
    price: float | None = None
    composition: str = ""
    provider: str = ""
    model: str = ""


class CardImproveBody(BaseModel):
    """Concrete SEO fix: description draft + photo shots (optional image gen)."""

    name: str = ""
    price: float | None = None
    composition: str = ""
    images_count: int | None = None
    content_rating: int | None = None
    marketplace: str = ""
    image_url: str = ""
    generate_images: bool = True
    max_images: int = 2
    provider: str = ""
    model: str = ""


@router.get("/cards/ms-search")
def get_cards_ms_search(query: str = Query(default=""), limit: int = Query(default=20, ge=1, le=50)) -> dict[str, Any]:
    """Поиск букета в каталоге МоегоСклада (шаг 1 создания карточки)."""
    try:
        from plugins.moysklad.cards_studio import search_ms_assortment

        return {"ok": True, "rows": search_ms_assortment(query, limit=limit)}
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/ms-search failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/cards/draft")
def post_cards_draft(body: CardDraftBody) -> dict[str, Any]:
    """Описания под каждую площадку для выбранного букета (шаг 3).

    Публикация на площадки (products/create / offer-mappings) — следующий
    этап; сейчас возвращаются готовые тексты для ручной публикации.
    """
    try:
        from plugins.moysklad.cards_studio import generate_card_draft

        out = generate_card_draft(
            name=body.name,
            price=body.price,
            composition=body.composition,
            provider=body.provider,
            model=body.model,
        )
        if not out.get("ok"):
            raise HTTPException(status_code=502, detail=str(out.get("error") or "draft failed"))
        return out
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/draft failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/cards/improve")
def post_cards_improve(body: CardImproveBody) -> dict[str, Any]:
    """Конкретные варианты для слабой карточки: описание + план/генерация фото.

    On-demand (не для всех 500 сразу): LLM-черновики описаний + shot-list
    под гайд площадки; при настроенном ``image_gen`` — до ``max_images`` картинок.
    """
    try:
        from plugins.moysklad.cards_studio import improve_card_content

        out = improve_card_content(
            name=body.name,
            price=body.price,
            composition=body.composition,
            images_count=body.images_count,
            content_rating=body.content_rating,
            marketplace=body.marketplace,
            image_url=body.image_url,
            generate_images=body.generate_images,
            max_images=max(0, min(4, int(body.max_images or 0))),
            provider=body.provider,
            model=body.model,
        )
        if not out.get("ok"):
            raise HTTPException(
                status_code=400, detail=str(out.get("error") or "improve failed")
            )
        return out
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/improve failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.get("/cards/orders")
def get_cards_orders(
    limit: int = Query(default=25, ge=1, le=100),
    status: str = Query(default=""),
) -> dict[str, Any]:
    """Живые заказы и статусы из кабинета Яндекс Маркета (новые сверху).

    Заказы уже попадают в МойСклад родной интеграцией МС↔Яндекс; Flowwow
    заказов в открытом API не отдаёт.
    """
    try:
        from plugins.moysklad.cards_studio import recent_yandex_orders

        out = recent_yandex_orders(limit=limit if not status else 100)
        rows = out.get("orders") or []
        if status.strip():
            wanted = status.strip().upper()
            rows = [r for r in rows if str(r.get("status") or "").upper() == wanted][:limit]
        return {
            "ok": True,
            "configured": out.get("configured"),
            "source": "Яндекс Маркет API /campaigns/{id}/orders — живой кабинет, без кэша",
            "orders": rows,
        }
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/orders failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/cards/analytics")
def get_cards_analytics(
    months: int = Query(default=4, ge=2, le=14),
) -> dict[str, Any]:
    """Аналитика для вкладки: динамика каналов из МС + сверка с Яндексом."""
    try:
        from datetime import date as _date

        from plugins.moysklad.dashboard_analytics import build_analytics
        from plugins.moysklad.report_backtest import extract_month_report
        from plugins.moysklad.yandex_stats import (
            build_reconciliation,
            yandex_monthly_stats_cached,
        )

        catalog, _meta = _get_catalog(force=False, blocking=True)
        analytics = build_analytics(
            list((catalog or {}).get("rows") or []), today=_date.today()
        )
        month_report = extract_month_report(analytics)
        dynamics: dict[str, Any] = {}
        for month_id in sorted(month_report)[-max(2, months):]:
            dynamics[month_id] = {
                ch: {"turnover": cell.get("turnover"), "orders": cell.get("orders")}
                for ch, cell in sorted(month_report[month_id].items())
                if cell.get("orders")
            }
        stats = yandex_monthly_stats_cached(months=max(2, months) - 1)
        return {
            "ok": True,
            "months": months,
            "sources": {
                "channel_dynamics": (
                    "МойСклад: оплаченные и неоплаченные заказы без отменённых, "
                    "канал из поля «Канал продаж», формулы листов Excel"
                ),
                "yandex_reconciliation": (
                    "Яндекс Маркет API stats/orders: фактические продажи "
                    "(покупательская цена и выплата продавцу) по всем кампаниям"
                ),
            },
            "channel_dynamics": dynamics,
            "yandex_reconciliation": build_reconciliation(month_report, stats),
        }
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/analytics failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/cards/seo-audit")
def get_cards_seo_audit(
    cap: int = Query(default=40, ge=1, le=200),
    min_photos_fw: int = Query(default=4, ge=1, le=20),
    min_photos_ym: int = Query(default=5, ge=1, le=20),
    quality_threshold: int = Query(default=80, ge=1, le=100),
    min_title_len: int = Query(default=25, ge=5, le=200),
    min_desc_len: int = Query(default=300, ge=50, le=4000),
    price_gap_min: float = Query(default=0.10, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """СЕО: правила ранжирования площадок + разбор каждой карточки по ним.

    Каждая находка — «данные карточки → правило площадки → проблема →
    действие → ожидаемый эффект → приоритет», со ссылкой на официальный
    источник (Flowwow blog / Яндекс справка «Качество карточки»).
    """
    try:
        from plugins.moysklad.marketplace_cards import marketplace_cards_payload
        from plugins.moysklad.ranking_rules import seo_audit

        cards = marketplace_cards_payload(limit=1000)
        combined = list((cards or {}).get("combined") or [])
        return {
            "ok": True,
            "generated_at": (cards or {}).get("generated_at"),
            **seo_audit(
                combined,
                cap=cap,
                min_photos_fw=min_photos_fw,
                min_photos_ym=min_photos_ym,
                quality_threshold=quality_threshold,
                min_title_len=min_title_len,
                min_desc_len=min_desc_len,
                price_gap_min=price_gap_min,
            ),
        }
    except Exception as exc:  # pragma: no cover
        log.exception("moysklad /cards/seo-audit failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
