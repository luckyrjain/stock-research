import os
import unittest
from unittest.mock import MagicMock, patch

import mf_holdings_history


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
        mf_holdings_history._ENGINE = None

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        mf_holdings_history._ENGINE = None

    def test_noop_without_database_url(self) -> None:
        with patch("mf_holdings_history._get_engine") as get_engine:
            mf_holdings_history.save_snapshot("TCS", {
                "as_of_date": "2026-03-31",
                "mutual_funds": [{"fund": "HDFC MF", "holding_pct": 3.5}],
            })
        get_engine.assert_not_called()

    def test_noop_on_error_payload(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("mf_holdings_history._get_engine") as get_engine:
            mf_holdings_history.save_snapshot("TCS", {"error": "boom", "symbol": "TCS"})
        get_engine.assert_not_called()

    def test_noop_without_as_of_date(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("mf_holdings_history._get_engine") as get_engine:
            mf_holdings_history.save_snapshot("TCS", {"mutual_funds": [{"fund": "HDFC MF", "holding_pct": 3.5}]})
        get_engine.assert_not_called()

    def test_noop_without_any_funds(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("mf_holdings_history._get_engine") as get_engine:
            mf_holdings_history.save_snapshot("TCS", {"as_of_date": "2026-03-31", "mutual_funds": []})
        get_engine.assert_not_called()

    def test_upserts_one_row_per_fund(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        conn = _FakeConn([MagicMock(), MagicMock()])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("mf_holdings_history._get_engine", return_value=fake_engine):
            mf_holdings_history.save_snapshot("tcs", {
                "as_of_date": "2026-03-31",
                "mutual_funds": [
                    {"fund": "HDFC MF", "holding_pct": 3.5},
                    {"fund": "ICICI Pru MF", "holding_pct": 2.1},
                ],
            })

        self.assertEqual(len(conn.calls), 2)
        _stmt, params = conn.calls[0][0]
        self.assertEqual(params, {
            "symbol": "TCS", "as_of_date": "2026-03-31", "fund": "HDFC MF", "holding_pct": 3.5,
        })

    def test_fund_missing_holding_pct_is_skipped_not_fatal(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        conn = _FakeConn([MagicMock()])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("mf_holdings_history._get_engine", return_value=fake_engine):
            mf_holdings_history.save_snapshot("TCS", {
                "as_of_date": "2026-03-31",
                "mutual_funds": [
                    {"fund": "HDFC MF", "holding_pct": None},
                    {"fund": "ICICI Pru MF", "holding_pct": 2.1},
                ],
            })
        self.assertEqual(len(conn.calls), 1)

    def test_swallows_db_errors(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("mf_holdings_history._get_engine", side_effect=RuntimeError("connection refused: password exposed")):
            mf_holdings_history.save_snapshot("TCS", {
                "as_of_date": "2026-03-31",
                "mutual_funds": [{"fund": "HDFC MF", "holding_pct": 3.5}],
            })


class ComputeStakeDeltasTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        mf_holdings_history._ENGINE = None

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        mf_holdings_history._ENGINE = None

    def test_returns_empty_without_database_url(self) -> None:
        self.assertEqual(mf_holdings_history.compute_stake_deltas("TCS"), [])

    def test_computes_delta_against_prior_snapshot(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows = [
            {
                "fund": "HDFC MF", "holding_pct": 4.0, "prior_holding_pct": 3.5,
                "as_of_date": "2026-06-30", "prior_as_of_date": "2026-03-31",
            },
            {
                "fund": "ICICI Pru MF", "holding_pct": 1.8, "prior_holding_pct": None,
                "as_of_date": "2026-06-30", "prior_as_of_date": "2026-03-31",
            },
        ]
        result_mock = MagicMock()
        result_mock.mappings.return_value.fetchall.return_value = rows
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("mf_holdings_history._get_engine", return_value=fake_engine):
            deltas = mf_holdings_history.compute_stake_deltas("tcs")

        self.assertEqual(len(deltas), 2)
        self.assertEqual(deltas[0]["fund"], "HDFC MF")
        self.assertAlmostEqual(deltas[0]["delta_pct"], 0.5)
        self.assertIsNone(deltas[1]["delta_pct"])  # new entrant, no prior snapshot to compare

    def test_swallows_db_errors_and_returns_empty(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("mf_holdings_history._get_engine", side_effect=RuntimeError("connection refused: password exposed")):
            self.assertEqual(mf_holdings_history.compute_stake_deltas("TCS"), [])

    def test_no_stored_snapshot_returns_empty(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        result_mock = MagicMock()
        result_mock.mappings.return_value.fetchall.return_value = []
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("mf_holdings_history._get_engine", return_value=fake_engine):
            self.assertEqual(mf_holdings_history.compute_stake_deltas("TCS"), [])


if __name__ == "__main__":
    unittest.main()
