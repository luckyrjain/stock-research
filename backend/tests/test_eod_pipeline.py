import unittest
import warnings
from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, insert, select, text

from db.models import (
    metadata, mf_nav_daily, prices_daily, securities, securities_bse, securities_sme,
)
from pipelines.eod_prices_pipeline import (
    _missing_dates, _upsert_navs, _upsert_prices, _upsert_seen, ingest_day,
    refresh_bse_securities_master, refresh_sme_securities_master,
)


def _silence_sqlite_date_adapter_warning() -> None:
    # Python 3.13 deprecates sqlite3's default date adapter; SQLAlchemy's SQLite
    # dialect still uses it. Test-only noise — production runs PostgreSQL.
    # Called from setUp so it applies inside pytest's per-test warning context.
    warnings.filterwarnings(
        "ignore",
        message="The default date adapter is deprecated",
        category=DeprecationWarning,
    )


def _price_row(symbol: str, d: date, close: float) -> dict:
    return {
        "symbol": symbol, "series": "EQ", "trade_date": d,
        "open": close, "high": close, "low": close, "close": close,
        "prev_close": close, "avg_price": close, "volume": 100,
        "turnover_lacs": 1.0, "trades": 10, "delivery_qty": 50,
        "delivery_pct": 50.0,
    }


class MissingDatesTest(unittest.TestCase):
    def test_weekdays_only_and_existing_excluded(self) -> None:
        # Fri 2026-07-03 .. Thu 2026-07-09; today = Thu 2026-07-09.
        today = date(2026, 7, 9)
        existing = {date(2026, 7, 7), date(2026, 7, 8)}
        missing = _missing_dates(existing, today, window=5)
        # Window of 5 weekdays ending today: Jul 3 (Fri), 6, 7, 8, 9. Sat/Sun skipped.
        self.assertEqual(missing, [date(2026, 7, 3), date(2026, 7, 6), date(2026, 7, 9)])

    def test_all_present_returns_empty(self) -> None:
        today = date(2026, 7, 9)
        existing = {date(2026, 7, 3), date(2026, 7, 6), date(2026, 7, 7),
                    date(2026, 7, 8), date(2026, 7, 9)}
        self.assertEqual(_missing_dates(existing, today, window=5), [])


class UpsertTest(unittest.TestCase):
    def setUp(self) -> None:
        _silence_sqlite_date_adapter_warning()
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine, tables=[securities, prices_daily, mf_nav_daily])

    def _count(self, table: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()

    def test_price_upsert_idempotent(self) -> None:
        rows = [_price_row("TCS", date(2026, 7, 3), 3945.5),
                _price_row("SBIN", date(2026, 7, 3), 816.25)]
        _upsert_prices(self.engine, rows)
        _upsert_prices(self.engine, rows)
        self.assertEqual(self._count("prices_daily"), 2)

    def test_price_upsert_updates_values(self) -> None:
        _upsert_prices(self.engine, [_price_row("TCS", date(2026, 7, 3), 100.0)])
        _upsert_prices(self.engine, [_price_row("TCS", date(2026, 7, 3), 200.0)])
        with self.engine.connect() as conn:
            close = conn.execute(text("SELECT close FROM prices_daily")).scalar_one()
        self.assertEqual(float(close), 200.0)

    def test_seen_upsert_idempotent_and_updates_last_seen(self) -> None:
        _upsert_seen(self.engine, [{"symbol": "TCS", "series": "EQ", "last_seen": date(2026, 7, 2)}])
        _upsert_seen(self.engine, [{"symbol": "TCS", "series": "EQ", "last_seen": date(2026, 7, 3)}])
        self.assertEqual(self._count("securities"), 1)
        with self.engine.connect() as conn:
            seen = conn.execute(text("SELECT last_seen FROM securities")).scalar_one()
        self.assertEqual(str(seen), "2026-07-03")

    def test_nav_upsert_idempotent(self) -> None:
        rows = [{"scheme_code": "120465", "nav_date": date(2026, 7, 3),
                 "nav": 58.1234, "scheme_name": "Axis Bluechip Fund - Growth"}]
        _upsert_navs(self.engine, rows)
        _upsert_navs(self.engine, rows)
        self.assertEqual(self._count("mf_nav_daily"), 1)

    def test_seen_upsert_last_seen_is_monotonic(self) -> None:
        # Re-ingesting an older historical day (e.g. via --date) must not
        # regress last_seen, even though series still gets updated.
        _upsert_seen(self.engine, [{"symbol": "TCS", "series": "EQ", "last_seen": date(2026, 7, 3)}])
        _upsert_seen(self.engine, [{"symbol": "TCS", "series": "BE", "last_seen": date(2026, 7, 2)}])
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT series, last_seen FROM securities")).fetchone()
        self.assertEqual(str(row[1]), "2026-07-03")
        self.assertEqual(row[0], "BE")


class IngestDayTest(unittest.TestCase):
    @patch("pipelines.eod_prices_pipeline.parse_bhavcopy")
    @patch("pipelines.eod_prices_pipeline.download_bhavcopy")
    def test_empty_bhavcopy_is_error(self, mock_download: MagicMock, mock_parse: MagicMock) -> None:
        mock_download.return_value = {"status": "ok", "csv": "SYMBOL, SERIES, ...\n"}
        mock_parse.return_value = {"rows": [], "skipped_series": 0, "malformed": 0}
        out = ingest_day(MagicMock(), date(2026, 7, 3), MagicMock())
        self.assertEqual(out["status"], "error")
        self.assertIn("empty bhavcopy", out["error"])


class HeldSchemeCodesTest(unittest.TestCase):
    def test_returns_empty_set_when_assets_table_missing(self) -> None:
        # This codebase has no portfolio `assets` table yet — NAV ingestion
        # must degrade gracefully (log + empty set), never raise.
        from pipelines.eod_prices_pipeline import _held_scheme_codes
        engine = create_engine("sqlite://")
        self.assertEqual(_held_scheme_codes(engine), set())


class RefreshBseSecuritiesMasterTest(unittest.TestCase):
    def setUp(self) -> None:
        _silence_sqlite_date_adapter_warning()
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine, tables=[securities_bse])

    @patch("pipelines.eod_prices_pipeline.fetch_bse_main_board")
    def test_upserts_rows_with_last_seen(self, mock_fetch) -> None:
        mock_fetch.return_value = [
            {"symbol": "ABC", "name": "ABC Ltd", "isin": "INE000A01011",
             "exchange": "BSE", "code": "500001", "series": "A"},
        ]
        refresh_bse_securities_master(self.engine)
        with self.engine.connect() as conn:
            row = conn.execute(select(securities_bse)).mappings().one()
        self.assertEqual(row["code"], "500001")
        self.assertEqual(row["symbol"], "ABC")
        self.assertEqual(row["last_seen"], date.today())

    @patch("pipelines.eod_prices_pipeline.fetch_bse_main_board")
    def test_last_seen_never_regresses(self, mock_fetch) -> None:
        mock_fetch.return_value = [
            {"symbol": "ABC", "name": "ABC Ltd", "isin": "INE000A01011",
             "exchange": "BSE", "code": "500001", "series": "A"},
        ]
        with self.engine.begin() as conn:
            conn.execute(insert(securities_bse).values(
                code="500001", symbol="ABC", name="ABC Ltd",
                isin="INE000A01011", series="A", last_seen=date(2099, 1, 1),
            ))
        refresh_bse_securities_master(self.engine)
        with self.engine.connect() as conn:
            row = conn.execute(select(securities_bse)).mappings().one()
        self.assertEqual(row["last_seen"], date(2099, 1, 1))

    @patch("pipelines.eod_prices_pipeline.fetch_bse_main_board")
    def test_fetch_failure_never_raises_and_keeps_existing_rows(self, mock_fetch) -> None:
        mock_fetch.return_value = []  # fetch_bse_main_board itself never raises; empty is its failure signal
        with self.engine.begin() as conn:
            conn.execute(insert(securities_bse).values(
                code="500001", symbol="ABC", name="ABC Ltd",
                isin="INE000A01011", series="A", last_seen=date(2020, 1, 1),
            ))
        refresh_bse_securities_master(self.engine)  # must not raise
        with self.engine.connect() as conn:
            row = conn.execute(select(securities_bse)).mappings().one()
        self.assertEqual(row["last_seen"], date(2020, 1, 1))  # untouched, not deleted


class RefreshSmeSecuritiesMasterTest(unittest.TestCase):
    def setUp(self) -> None:
        _silence_sqlite_date_adapter_warning()
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine, tables=[securities_sme])

    @patch("pipelines.eod_prices_pipeline.get_all_sme_stocks")
    def test_upserts_rows_keyed_by_exchange_and_symbol(self, mock_fetch) -> None:
        mock_fetch.return_value = [
            {"symbol": "EMERGE1", "name": "Emerge Stock 1", "isin": "INE999Z01001",
             "series": "SM", "exchange": "NSE"},
        ]
        refresh_sme_securities_master(self.engine)
        with self.engine.connect() as conn:
            row = conn.execute(select(securities_sme)).mappings().one()
        self.assertEqual((row["exchange"], row["symbol"]), ("NSE", "EMERGE1"))
        self.assertEqual(row["last_seen"], date.today())


if __name__ == "__main__":
    unittest.main()
