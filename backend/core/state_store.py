"""Durable JSON state, keyed by (namespace, key), in Postgres.

Replaces the per-family JSON files this codebase used to scatter under
`output/` — `_history/`, `_source_health/`, `_source_quality/`,
`_scraper_error_counters/`, `_llm_cost/`, `_cas/`, and the CLI's own saved
report. Every one of those was literally "a JSON blob at a path", so they
share one table (`db.models.app_state`) under distinct namespaces rather than
six near-identical tables. `output/` is now strictly a regenerable TTL cache.

Two things this buys beyond "nothing durable is written to the folder":

- **The `fcntl.flock` locks are gone.** `llm_cost.py`, `telemetry/source_health.py` and
  `telemetry/scraper_error_counters.py` each carried their own advisory-lock helper to
  make a read-modify-write cycle safe across worker *processes*. `mutate()`
  below does the same job with a row lock, which — unlike `flock` — also holds
  across separate *hosts*, the multi-replica topology `docs/deployment.md`
  already documents as supported.
- **No POSIX dependency.** Those locks were POSIX-only by their own admission.

Best-effort throughout, the same convention as `verdict_history.py`: an unset
`DATABASE_URL` or a DB hiccup is logged and swallowed, never raised. Callers
are telemetry/audit paths that must not be able to break the request or
pipeline run they are observing. The consequence is disclosed rather than
hidden: a deployment with no `DATABASE_URL` keeps none of this state at all,
where it previously kept it on local disk.
"""
import os
import threading
from datetime import datetime, timezone

from core.observability import get_logger, log_event

LOGGER = get_logger("state_store")

_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def _get_engine():
    """Lazily-built, process-wide engine — same double-checked pattern as
    `verdict_history.py::_get_engine()`, and the same patch target tests use
    (`patch("core.state_store._get_engine", ...)`)."""
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:  # re-check: another thread may have won the race
                from db.models import get_engine
                _ENGINE = get_engine()
    return _ENGINE


def _enabled() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _insert(engine):
    """Dialect-specific INSERT, for `on_conflict_do_update`. Postgres and
    SQLite expose the identical API for it; SQLite is what this codebase's
    tests run these tables against (house rule: no live DB in tests)."""
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def load(namespace: str, key: str) -> dict | None:
    """One record, or None if it was never written (or DB is unavailable).
    None is always "nothing stored", never a guessed empty payload."""
    if not _enabled():
        return None
    try:
        from db.models import app_state
        from sqlalchemy import select

        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                select(app_state.c.payload).where(
                    app_state.c.namespace == namespace, app_state.c.key == key
                )
            ).first()
        return row[0] if row else None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "state_load_failed", level="warning",
                  namespace=namespace, key=key, error=str(exc))
        return None


def items(namespace: str, limit: int | None = None, newest_first: bool = False) -> list[tuple[str, dict]]:
    """Every `(key, payload)` in `namespace`, key-ascending by default.

    Callers key these namespaces by date string (`_history`) or run id, so
    lexical key order is also chronological order — `newest_first=True` plus
    `limit` is the "last N days" read, replacing a `sorted(dir.glob("*.json"))`
    slice. Returns [] rather than raising on any failure."""
    if not _enabled():
        return []
    try:
        from db.models import app_state
        from sqlalchemy import select

        stmt = select(app_state.c.key, app_state.c.payload).where(
            app_state.c.namespace == namespace
        ).order_by(app_state.c.key.desc() if newest_first else app_state.c.key.asc())
        if limit is not None:
            stmt = stmt.limit(limit)

        engine = _get_engine()
        with engine.connect() as conn:
            return [(r[0], r[1]) for r in conn.execute(stmt).fetchall()]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "state_items_failed", level="warning", namespace=namespace, error=str(exc))
        return []


def delete_older_than(namespace: str, days: int) -> int:
    """Delete every record in `namespace` whose `updated_at` is older than
    `days` ago. Returns the number of rows deleted (0 on any failure, never
    raises) — the one disk-directory convenience `output/_*` gave operators
    (`rm output/_source_quality/*.json`) that a shared Postgres table doesn't,
    for namespaces with per-run/unbounded growth (e.g. `source_quality`)."""
    if not _enabled():
        return 0
    try:
        from datetime import timedelta

        from db.models import app_state

        cutoff = _now() - timedelta(days=days)
        engine = _get_engine()
        with engine.begin() as conn:
            result = conn.execute(
                app_state.delete().where(
                    app_state.c.namespace == namespace, app_state.c.updated_at < cutoff
                )
            )
            return result.rowcount or 0
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "state_delete_failed", level="warning", namespace=namespace, error=str(exc))
        return 0


def save(namespace: str, key: str, payload: dict) -> bool:
    """Upsert one record. Last write wins — used only where a single writer
    owns the key (one snapshot per date, one telemetry file per run id, one
    report per symbol-and-date). Anything with concurrent writers must use
    `mutate()` instead, which serializes them.

    Returns whether the write actually happened — best-effort callers that
    report success to a user (e.g. a CLI's "Report saved") must check this
    rather than assuming a call that never raises also always persisted."""
    if not _enabled():
        return False
    try:
        from db.models import app_state

        engine = _get_engine()
        stmt = _insert(engine)(app_state).values(
            namespace=namespace, key=key, payload=payload, updated_at=_now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[app_state.c.namespace, app_state.c.key],
            set_={"payload": stmt.excluded.payload, "updated_at": stmt.excluded.updated_at},
        )
        with engine.begin() as conn:
            conn.execute(stmt)
        return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "state_save_failed", level="warning",
                  namespace=namespace, key=key, error=str(exc))
        return False


def mutate(namespace: str, key: str, fn, default: dict) -> dict | None:
    """Read-modify-write one record under a row lock, returning the stored
    result (or None if nothing was written).

    `fn(current) -> new_payload` runs *inside* the transaction holding the
    lock, so two workers incrementing the same counter can't both read the
    same prior value and have the second silently clobber the first — the
    exact lost-update race the three `fcntl.flock` helpers this replaces were
    each written to prevent. The row is created from `default` if absent.

    The `INSERT ... ON CONFLICT DO UPDATE` is what takes the lock: on the
    conflict path it performs a real (no-op) UPDATE of the existing row, which
    locks it for the rest of the transaction, and `RETURNING` hands back the
    payload as it was *before* `fn` sees it. A plain `SELECT ... FOR UPDATE`
    would not work here — it locks nothing when the row doesn't exist yet, so
    two workers racing on a brand-new key would both fall through to an
    INSERT.
    """
    if not _enabled():
        return None
    try:
        from db.models import app_state

        engine = _get_engine()
        ins = _insert(engine)(app_state).values(
            namespace=namespace, key=key, payload=default, updated_at=_now(),
        )
        lock_and_read = ins.on_conflict_do_update(
            index_elements=[app_state.c.namespace, app_state.c.key],
            set_={"namespace": namespace},   # no-op assignment; taken purely for the lock
        ).returning(app_state.c.payload)

        with engine.begin() as conn:
            current = conn.execute(lock_and_read).scalar()
            updated = fn(current if isinstance(current, dict) else dict(default))
            conn.execute(
                app_state.update()
                .where(app_state.c.namespace == namespace, app_state.c.key == key)
                .values(payload=updated, updated_at=_now())
            )
        return updated
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "state_mutate_failed", level="warning",
                  namespace=namespace, key=key, error=str(exc))
        return None
