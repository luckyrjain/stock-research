import json
import unittest
from unittest.mock import MagicMock, patch

from pipelines import screener_pipeline
from signals.models import Signal


def _stock(symbol: str, industry: str = "Information Technology") -> dict:
    return {"symbol": symbol, "company_name": f"{symbol} Ltd", "industry": industry, "isin": None}


def _fake_quote_tool(payload: dict) -> MagicMock:
    tool = MagicMock()
    tool.run.return_value = json.dumps(payload)
    return tool


class FetchOneTest(unittest.TestCase):
    def test_combines_quote_and_technical_metrics(self) -> None:
        quote = {
            "company_name": "TCS Ltd", "primary_exchange": "NSE", "sector": "Technology",
            "current_price": 3500.0, "pe_ratio": 28.5, "market_cap_cr": 1250000.0,
            "avg_volume_10d": 2500000.0,
        }
        tech = Signal("technical", "BULLISH_TREND", 0.5, {"rsi14": 62.3, "ema20_above_ema50": True})
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool(quote)), \
             patch("signals.technical.technical_signal", return_value=tech):
            result = screener_pipeline._fetch_one(_stock("TCS"))

        self.assertNotIn("error", result)
        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["company_name"], "TCS Ltd")
        self.assertEqual(result["exchange"], "NSE")
        self.assertEqual(result["nse_industry"], "Information Technology")
        self.assertEqual(result["sector"], "Technology")
        self.assertEqual(result["pe_ratio"], 28.5)
        self.assertEqual(result["rsi14"], 62.3)
        self.assertEqual(result["ema_trend"], "bullish")

    def test_bearish_trend_from_ema_posture(self) -> None:
        quote = {"current_price": 100.0}
        tech = Signal("technical", "BEARISH_TREND", -0.4, {"rsi14": 25.0, "ema20_above_ema50": False})
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool(quote)), \
             patch("signals.technical.technical_signal", return_value=tech):
            result = screener_pipeline._fetch_one(_stock("XYZ"))
        self.assertEqual(result["ema_trend"], "bearish")

    def test_unknown_technical_signal_leaves_trend_and_rsi_null(self) -> None:
        # technical_signal returns UNKNOWN (no rsi14/ema20_above_ema50 in meta)
        # when there isn't enough price history — never guessed.
        quote = {"current_price": 100.0}
        tech = Signal("technical", "UNKNOWN", 0, {"closes_available": 10})
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool(quote)), \
             patch("signals.technical.technical_signal", return_value=tech):
            result = screener_pipeline._fetch_one(_stock("NEWIPO"))
        self.assertIsNone(result["rsi14"])
        self.assertIsNone(result["ema_trend"])

    def test_quote_error_returns_error_result(self) -> None:
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool({"error": "No data found for XYZ"})):
            result = screener_pipeline._fetch_one(_stock("XYZ"))
        self.assertIn("error", result)
        self.assertEqual(result["symbol"], "XYZ")

    def test_exception_returns_error_result_not_raise(self) -> None:
        with patch("tools.nse_tools.get_stock_quote", side_effect=RuntimeError("boom")):
            result = screener_pipeline._fetch_one(_stock("XYZ"))
        self.assertIn("error", result)
        self.assertEqual(result["symbol"], "XYZ")

    def test_technical_signal_failure_does_not_discard_a_good_quote(self) -> None:
        # A transient pandas/price-history hiccup inside technical_signal must
        # not throw away an otherwise-good quote (price/P/E/market cap) — the
        # two fetches are isolated in their own try/except specifically so
        # this degrades to null rsi14/ema_trend, not a dropped row.
        quote = {"current_price": 100.0, "pe_ratio": 15.0, "company_name": "XYZ Ltd"}
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool(quote)), \
             patch("signals.technical.technical_signal", side_effect=RuntimeError("boom")):
            result = screener_pipeline._fetch_one(_stock("XYZ"))
        self.assertNotIn("error", result)
        self.assertEqual(result["pe_ratio"], 15.0)
        self.assertIsNone(result["rsi14"])
        self.assertIsNone(result["ema_trend"])


class SetupDbStampsAlembicHeadTest(unittest.TestCase):
    """--setup-db bypasses Alembic entirely (metadata.create_all directly) —
    without stamping head afterward, a fresh database ends up with every
    table but no alembic_version row, so a subsequent `alembic upgrade
    head` fails. See db.models.stamp_alembic_head's own docstring. Unlike
    --reset-db (ResetDbScopeTest below, scoped to just screener_stocks),
    --setup-db calls metadata.create_all() for the full schema, so it's the
    one that needs the stamp."""

    def test_setup_db_calls_create_all_then_stamps_head(self) -> None:
        engine = MagicMock()
        with patch.object(screener_pipeline, "metadata") as mock_metadata, \
             patch("db.models.stamp_alembic_head") as mock_stamp:
            screener_pipeline.setup_db(engine)
        mock_metadata.create_all.assert_called_once_with(engine)
        mock_stamp.assert_called_once_with()


class ResetDbScopeTest(unittest.TestCase):
    """--reset-db must only touch this pipeline's own table — db/models.py's
    MetaData() is shared across every table in the app (users, sessions,
    watchlist_items, ...), so metadata.drop_all() here would take down
    unrelated, NOT-regenerable data. See pipelines/sme_ema_pipeline.py's own
    --reset-db, which predates this fix and still has that broader-than-
    intended blast radius (documented as a disclosed limitation in
    CLAUDE.md)."""

    def test_reset_db_drops_and_creates_only_screener_stocks(self) -> None:
        fake_engine = MagicMock()
        with patch("sys.argv", ["pipelines/screener_pipeline.py", "--reset-db"]), \
             patch.object(screener_pipeline, "get_engine", return_value=fake_engine), \
             patch.object(screener_pipeline, "screener_stocks") as fake_table, \
             patch.object(screener_pipeline, "metadata") as fake_metadata:
            screener_pipeline.main()

        fake_table.drop.assert_called_once_with(fake_engine, checkfirst=True)
        fake_table.create.assert_called_once_with(fake_engine, checkfirst=True)
        fake_metadata.drop_all.assert_not_called()


class RunHealthSignalTest(unittest.TestCase):
    """run()'s return value drives whether the scheduled CI workflow (and the
    API's refresh endpoint logging) treats a run as healthy — see
    _MAX_ACCEPTABLE_ERROR_RATE. Mocks every I/O boundary (DB, constituent-list
    fetch, per-stock fetch) to isolate just that decision logic, same
    approach as tests/test_sme_ema_pipeline.py's RunHealthSignalTest."""

    def setUp(self) -> None:
        patches = [
            patch.object(screener_pipeline, "get_engine", return_value=MagicMock()),
            patch.object(screener_pipeline, "_upsert_stocks"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_empty_constituent_list_returns_false(self) -> None:
        with patch.object(screener_pipeline, "get_nifty500_constituents", return_value=[]):
            self.assertFalse(screener_pipeline.run())

    def test_high_error_rate_returns_false(self) -> None:
        stocks = [_stock(f"SYM{i}") for i in range(10)]

        def _fetch(stock):
            idx = int(stock["symbol"].removeprefix("SYM"))
            if idx < 6:  # 60% error rate, above the 50% threshold
                return {"error": "no data", "symbol": stock["symbol"]}
            return {"symbol": stock["symbol"]}

        with patch.object(screener_pipeline, "get_nifty500_constituents", return_value=stocks), \
             patch.object(screener_pipeline, "_fetch_one", side_effect=_fetch):
            self.assertFalse(screener_pipeline.run())

    def test_low_error_rate_returns_true(self) -> None:
        stocks = [_stock(f"SYM{i}") for i in range(10)]

        def _fetch(stock):
            if stock["symbol"] == "SYM0":  # 10% error rate, well under the threshold
                return {"error": "no data", "symbol": stock["symbol"]}
            return {"symbol": stock["symbol"]}

        with patch.object(screener_pipeline, "get_nifty500_constituents", return_value=stocks), \
             patch.object(screener_pipeline, "_fetch_one", side_effect=_fetch):
            self.assertTrue(screener_pipeline.run())


if __name__ == "__main__":
    unittest.main()
