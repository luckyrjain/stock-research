import os
import unittest
from unittest.mock import MagicMock, patch

import verdict_history


class _FakeConn:
    """Fake SQLAlchemy connection: returns queued results in call order and
    records every execute() call for assertions."""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls: list = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class SaveSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        verdict_history._ENGINE = None

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        verdict_history._ENGINE = None

    def test_noop_without_database_url(self) -> None:
        with patch("verdict_history._get_engine") as get_engine:
            verdict_history.save_snapshot("TCS", {"recommendation": "BUY"}, {"final_score": 5.0}, {"current_price": 100.0})
        get_engine.assert_not_called()

    def test_noop_without_recommendation(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("verdict_history._get_engine") as get_engine:
            verdict_history.save_snapshot("TCS", {}, None, {})
        get_engine.assert_not_called()

    def test_upserts_when_recommendation_present(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        conn = _FakeConn([MagicMock()])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("verdict_history._get_engine", return_value=fake_engine):
            verdict_history.save_snapshot(
                "tcs",
                {"recommendation": "BUY", "confidence": "HIGH"},
                {"final_score": 7.5},
                {"current_price": 3500.25},
            )

        self.assertEqual(len(conn.calls), 1)
        args, _kwargs = conn.calls[0]
        _stmt, params = args
        verdict_date = params.pop("verdict_date", None)
        self.assertRegex(verdict_date or "", r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(params, {
            "symbol": "TCS",
            "recommendation": "BUY",
            "confidence": "HIGH",
            "current_price": 3500.25,
            "signal_score": 7.5,
        })

    def test_signal_score_upsert_preserves_existing_value_on_a_null_write(self) -> None:
        # Regression test for an adversarial-review finding, confirmed against
        # a real Postgres instance: main.py's cache-hit early return and
        # pipelines/watchlist_alerts.py's "nothing to re-analyze today" branch both
        # intentionally call save_snapshot with signal_context=None (no
        # signal engine run for a cache hit). The upsert previously
        # unconditionally overwrote signal_score with EXCLUDED.signal_score,
        # so a same-day no-op re-save silently NULLed out a real
        # signal_score a genuine earlier run that same day had written.
        # A mocked connection can't exercise real ON CONFLICT semantics, so
        # this locks in the SQL-level fix (COALESCE against the existing
        # stored value) directly.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        conn = _FakeConn([MagicMock()])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("verdict_history._get_engine", return_value=fake_engine):
            verdict_history.save_snapshot("TCS", {"recommendation": "BUY"}, None, {"current_price": 100.0})

        args, _kwargs = conn.calls[0]
        stmt, params = args
        self.assertIn("COALESCE(EXCLUDED.signal_score, verdict_history.signal_score)", str(stmt))
        self.assertIsNone(params["signal_score"])

    def test_swallows_db_errors(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("verdict_history._get_engine", side_effect=RuntimeError("connection refused: password exposed")):
            # Must not raise.
            verdict_history.save_snapshot("TCS", {"recommendation": "BUY"}, None, {})


class LoadHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        verdict_history._ENGINE = None

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        verdict_history._ENGINE = None

    def test_returns_empty_without_database_url(self) -> None:
        self.assertEqual(verdict_history.load_history("TCS"), [])

    def test_returns_mapped_rows(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows = [
            {"date": "2026-07-01", "recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 100.0, "signal_score": 2.0},
            {"date": "2026-07-24", "recommendation": "BUY", "confidence": "HIGH", "current_price": 110.5, "signal_score": 6.0},
        ]
        result_mock = MagicMock()
        result_mock.mappings.return_value.fetchall.return_value = rows
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("verdict_history._get_engine", return_value=fake_engine):
            history = verdict_history.load_history("tcs", limit=10)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[1]["recommendation"], "BUY")
        self.assertEqual(history[1]["current_price"], 110.5)

    def test_swallows_db_errors_and_returns_empty(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("verdict_history._get_engine", side_effect=RuntimeError("connection refused: password exposed")):
            self.assertEqual(verdict_history.load_history("TCS"), [])


class DetectRecentChangesTest(unittest.TestCase):
    """Shared by pipelines/watchlist_alerts.py's daily digest and
    GET /api/watchlist/calendar's same-day in-app surfacing — one place
    deciding what counts as a notable change."""

    def test_both_none_with_fewer_than_two_snapshots(self) -> None:
        with patch("verdict_history.load_history", return_value=[{"recommendation": "BUY"}]):
            result = verdict_history.detect_recent_changes("TCS")
        self.assertEqual(result, {"recommendation_change": None, "price_move": None})

    def test_recommendation_change_detected(self) -> None:
        history = [
            {"recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 100.0},
            {"recommendation": "BUY", "confidence": "HIGH", "current_price": 101.0},
        ]
        with patch("verdict_history.load_history", return_value=history):
            result = verdict_history.detect_recent_changes("TCS")
        self.assertEqual(result["recommendation_change"], {
            "old_recommendation": "HOLD", "new_recommendation": "BUY", "confidence": "HIGH",
        })
        self.assertIsNone(result["price_move"])

    def test_price_move_detected_independent_of_recommendation(self) -> None:
        # Recommendation stays HOLD, but the price still moved double digits —
        # the recommendation-change check alone would never catch this.
        history = [
            {"recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 100.0},
            {"recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 115.0},
        ]
        with patch("verdict_history.load_history", return_value=history):
            result = verdict_history.detect_recent_changes("TCS")
        self.assertIsNone(result["recommendation_change"])
        self.assertEqual(result["price_move"], {"old_price": 100.0, "new_price": 115.0, "change_pct": 15.0})

    def test_price_move_respects_custom_threshold(self) -> None:
        history = [
            {"recommendation": "HOLD", "current_price": 100.0},
            {"recommendation": "HOLD", "current_price": 105.0},
        ]
        with patch("verdict_history.load_history", return_value=history):
            under_default = verdict_history.detect_recent_changes("TCS")
            over_custom = verdict_history.detect_recent_changes("TCS", price_move_threshold_pct=3.0)
        self.assertIsNone(under_default["price_move"])
        self.assertIsNotNone(over_custom["price_move"])

    def test_both_can_fire_together(self) -> None:
        history = [
            {"recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 100.0},
            {"recommendation": "SELL", "confidence": "HIGH", "current_price": 80.0},
        ]
        with patch("verdict_history.load_history", return_value=history):
            result = verdict_history.detect_recent_changes("TCS")
        self.assertIsNotNone(result["recommendation_change"])
        self.assertIsNotNone(result["price_move"])

    def test_price_move_none_when_prior_price_missing_or_zero(self) -> None:
        for prior_price in (None, 0.0):
            history = [{"recommendation": "HOLD", "current_price": prior_price}, {"recommendation": "HOLD", "current_price": 200.0}]
            with patch("verdict_history.load_history", return_value=history):
                result = verdict_history.detect_recent_changes("TCS")
            self.assertIsNone(result["price_move"])


if __name__ == "__main__":
    unittest.main()
