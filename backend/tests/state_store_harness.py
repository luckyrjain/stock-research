"""Shared SQLite backing for tests of the modules that persist through
core/state_store.py (llm_cost, source_health, scraper_error_counters,
source_quality, market_picks_pipeline's history, main's CLI report,
cas_import's archive).

Replaces the `patch.object(mod, "_SOME_DIR", tmpdir)` setUp those tests used
when each of those modules owned its own directory of JSON files. House rule
is no live DB in tests, so these run against SQLite — which is enough, since
every one of those modules now talks to Postgres only through state_store's
four functions.
"""
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.models import metadata

# Generous: several tests deliberately pile concurrent writers onto one row,
# and SQLite blocks the losers for the duration of the winner's transaction.
_BUSY_TIMEOUT_SECONDS = 30


def _patched(engine, url: str) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch.dict(os.environ, {"DATABASE_URL": url}))
    stack.enter_context(patch("core.state_store._get_engine", return_value=engine))
    stack.callback(engine.dispose)
    return stack


def isolated_state_store() -> ExitStack:
    """Started ExitStack pointing state_store at a fresh in-memory SQLite.
    Call `.close()` in tearDown (or hand it to `addCleanup`).

    StaticPool hands every thread the same DBAPI connection — correct for the
    ordinary single-writer test, wrong for anything contending on one record.
    Use `shared_state_store()` for those.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return _patched(engine, "sqlite://")


def shared_state_store(directory, create: bool = True) -> ExitStack:
    """Started ExitStack pointing state_store at a file-backed SQLite under
    `directory` — one connection per thread, and real write locking, so
    concurrent writers genuinely contend the way they would on Postgres.

    `create=False` is for use inside a forked child process: the parent has
    already created the tables, and a SQLAlchemy engine is not fork-safe (its
    pooled connections are shared file descriptors), so a child must build its
    own engine over the same file rather than inherit the parent's.
    """
    url = f"sqlite:///{Path(directory) / 'state.db'}"
    engine = create_engine(url, connect_args={"timeout": _BUSY_TIMEOUT_SECONDS})
    if create:
        metadata.create_all(engine)
    return _patched(engine, url)
