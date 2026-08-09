"""Tests for portfolio/broker_sync_common.py's call_with_backoff() — the
shared retry helper every broker sync module wraps its raw network calls
with (kite.holdings/trades, HDFC/Paytm's requests-based fetches). Real
sleeps are avoided via a tiny base_delay_seconds, not by mocking time.sleep,
so these also prove the actual sleep-then-retry control flow runs, not just
that time.sleep was called."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import requests
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.pool import StaticPool

from db.models import accounts, assets, holdings, metadata, positions, profiles, users
from portfolio.broker_sync_common import call_with_backoff, sync_holdings, upsert_position_from_holding


class _HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"http {status_code}")
        self.response = MagicMock(status_code=status_code)


class _KiteException(Exception):
    """Mirrors kiteconnect's own KiteException shape: a `.code` attribute
    directly on the exception, no `.response` at all — unlike `requests`'
    HTTPError, which carries the status on `.response.status_code`."""
    def __init__(self, code):
        super().__init__(f"kite error {code}")
        self.code = code


class CallWithBackoffTest(unittest.TestCase):
    def test_succeeds_on_first_attempt_no_retry(self):
        fn = MagicMock(return_value="ok")
        result = call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 1)

    def test_retries_transient_failure_then_succeeds(self):
        fn = MagicMock(side_effect=[requests.exceptions.ConnectionError("blip"), "ok"])
        result = call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 2)

    def test_exhausts_all_attempts_then_raises_last_exception(self):
        fn = MagicMock(side_effect=requests.exceptions.Timeout("still down"))
        with self.assertRaises(requests.exceptions.Timeout):
            call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(fn.call_count, 3)

    def test_5xx_http_error_is_retried(self):
        fn = MagicMock(side_effect=[_HttpError(503), "ok"])
        result = call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 2)

    def test_4xx_http_error_is_not_retried(self):
        """An expired token or bad request needs a reconnect, not three
        retries of the exact same failure — this is what keeps a doomed
        sync from wasting ~(1+2)s of backoff sleep before the 422/404 the
        caller needs anyway to prompt a reconnect."""
        fn = MagicMock(side_effect=_HttpError(401))
        with self.assertRaises(_HttpError):
            call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(fn.call_count, 1)

    def test_kiteconnect_style_exception_code_attribute_is_honored(self):
        """kiteconnect's own KiteException carries `.code` directly (no
        `.response`) — a real gap this test guards against: without
        checking `.code` too, TokenException(code=403) would fall through
        to "no status attached" and get retried as if it were transient,
        wasting ~3s of backoff sleep on an auth failure that a retry can
        never fix."""
        fn = MagicMock(side_effect=_KiteException(403))
        with self.assertRaises(_KiteException):
            call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(fn.call_count, 1)

    def test_kiteconnect_style_5xx_code_is_retried(self):
        fn = MagicMock(side_effect=[_KiteException(503), "ok"])
        result = call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 2)


_TABLES = [profiles, accounts, assets, holdings, positions, users]


def _sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    # positions.bought_at and users.created_at both default to Postgres'
    # NOW() (server_default=text("NOW()")) — SQLite has no such function.
    # Registered once per new DBAPI connection rather than passed explicitly
    # in every insert() below, since upsert_position_from_holding's own SQL
    # relies on the server_default firing exactly like it would in Postgres.
    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, _record):
        dbapi_conn.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat())

    metadata.create_all(engine, tables=_TABLES)
    return engine


def _mk_account(engine) -> int:
    with engine.begin() as conn:
        pid = conn.execute(insert(profiles).values(name="p")).inserted_primary_key[0]
        return conn.execute(insert(accounts).values(profile_id=pid, name="a", type="broker")).inserted_primary_key[0]


class UpsertPositionFromHoldingTest(unittest.TestCase):
    """The `owner` tuple (see routes/watchlist.py::WatchlistOwner) decides
    which of positions.client_id/user_id gets written — a plain client_id
    string would always land in the wrong column for a signed-in caller,
    since GET /api/positions reads by user_id once a valid session is
    present (see routes/watchlist.py::resolve_owner)."""

    def test_client_owner_writes_client_id_column(self):
        engine = _sqlite_engine()
        with engine.begin() as conn:
            upsert_position_from_holding(conn, ("client", "abc-123"), "TCS", "NSE", Decimal("10"), Decimal("100"))
        with engine.connect() as conn:
            row = conn.execute(select(positions.c.client_id, positions.c.user_id, positions.c.shares)).first()
        self.assertEqual(row.client_id, "abc-123")
        self.assertIsNone(row.user_id)
        self.assertEqual(row.shares, 10)

    def test_user_owner_writes_user_id_column(self):
        engine = _sqlite_engine()
        with engine.begin() as conn:
            uid = conn.execute(insert(users).values(email="a@example.com")).inserted_primary_key[0]
            upsert_position_from_holding(conn, ("user", uid), "TCS", "NSE", Decimal("10"), Decimal("100"))
        with engine.connect() as conn:
            row = conn.execute(select(positions.c.client_id, positions.c.user_id)).first()
        self.assertEqual(row.user_id, uid)
        self.assertIsNone(row.client_id)

    def test_resync_updates_shares_and_entry_price_in_place(self):
        engine = _sqlite_engine()
        with engine.begin() as conn:
            upsert_position_from_holding(conn, ("client", "abc-123"), "TCS", "NSE", Decimal("10"), Decimal("100"))
        with engine.begin() as conn:
            upsert_position_from_holding(conn, ("client", "abc-123"), "TCS", "NSE", Decimal("15"), Decimal("110"))
        with engine.connect() as conn:
            rows = conn.execute(select(positions.c.shares, positions.c.entry_price)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].shares, 15)
        self.assertEqual(rows[0].entry_price, 110)


class SyncHoldingsPositionMirrorIsolationTest(unittest.TestCase):
    """Regression coverage for the bug an independent review caught: a
    plain try/except around the position-mirroring insert is not enough,
    because a failed statement aborts the whole Postgres/SQLAlchemy
    transaction — every later statement on that same connection (the rest
    of this loop, reconcile_stale_holdings, the trades sync that follows in
    the caller's own `with engine.begin()` block) would fail too unless the
    mirroring call runs inside its own SAVEPOINT (conn.begin_nested())."""

    def test_broken_owner_does_not_abort_the_rest_of_the_holdings_sync(self):
        engine = _sqlite_engine()
        account_id = _mk_account(engine)
        # ("client", None) inserts client_id=NULL with user_id also NULL
        # (never set) — violates positions.ck_positions_exactly_one_owner,
        # a real DB-level failure, not a mocked one.
        broken_owner = ("client", None)
        normalized_holdings = [
            {"symbol": "GOOD1", "exchange": "NSE", "quantity": Decimal("10"), "avg_price": Decimal("100")},
            {"symbol": "GOOD2", "exchange": "NSE", "quantity": Decimal("5"), "avg_price": Decimal("50")},
        ]
        with engine.begin() as conn:
            result = sync_holdings(conn, account_id, normalized_holdings, "test_source", "2026-01-01", owner=broken_owner)

        self.assertEqual(result["holdings_synced"], 2)
        with engine.connect() as conn:
            synced_symbols = {r[0] for r in conn.execute(select(assets.c.symbol))}
        self.assertEqual(synced_symbols, {"GOOD1", "GOOD2"})
        with engine.connect() as conn:
            self.assertEqual(conn.execute(select(positions.c.id)).fetchall(), [])


if __name__ == "__main__":
    unittest.main()
