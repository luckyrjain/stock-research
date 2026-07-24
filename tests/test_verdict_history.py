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


if __name__ == "__main__":
    unittest.main()
