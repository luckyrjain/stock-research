import unittest
from datetime import date, timedelta

import pandas as pd

from sme_ema_pipeline import _compute_ema_signals, _STORE_DAYS


def _make_result(closes: list[float]) -> dict:
    start = date(2026, 1, 1)
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    df = pd.DataFrame({"Close": closes}, index=idx)
    return {"symbol": "TESTSME", "exchange": "NSE", "df": df}


class ComputeEmaSignalsTest(unittest.TestCase):
    def test_golden_cross_detected_once_on_v_shaped_recovery(self) -> None:
        # 60 days falling, then 60 days rising strongly: EMA20 crosses above EMA50 once.
        closes = [200.0 - i for i in range(60)] + [140.0 + 3.0 * i for i in range(60)]
        rows = _compute_ema_signals(_make_result(closes))

        golden = [r for r in rows if r["cross"] == "golden"]
        death = [r for r in rows if r["cross"] == "death"]
        self.assertEqual(len(golden), 1)
        self.assertEqual(len(death), 0)
        # On the cross day EMA20 is above EMA50, and it is the first stored day above.
        self.assertGreater(golden[0]["ema20"], golden[0]["ema50"])
        first_above = next(r for r in rows if r["ema20"] > r["ema50"])
        self.assertEqual(first_above["trade_date"], golden[0]["trade_date"])

    def test_death_cross_detected_once_on_peak_and_decline(self) -> None:
        closes = [100.0 + i for i in range(60)] + [160.0 - 2.0 * i for i in range(60)]
        rows = _compute_ema_signals(_make_result(closes))

        golden = [r for r in rows if r["cross"] == "golden"]
        death = [r for r in rows if r["cross"] == "death"]
        self.assertEqual(len(death), 1)
        self.assertEqual(len(golden), 0)
        self.assertLess(death[0]["ema20"], death[0]["ema50"])

    def test_only_last_store_days_rows_are_returned(self) -> None:
        closes = [100.0 + (i % 7) for i in range(250)]
        rows = _compute_ema_signals(_make_result(closes))
        self.assertEqual(len(rows), _STORE_DAYS)

    def test_short_series_returns_all_rows(self) -> None:
        closes = [100.0 + i for i in range(40)]
        rows = _compute_ema_signals(_make_result(closes))
        self.assertEqual(len(rows), 40)

    def test_error_result_returns_empty_list(self) -> None:
        self.assertEqual(_compute_ema_signals({"error": "no data", "symbol": "X"}), [])


if __name__ == "__main__":
    unittest.main()
