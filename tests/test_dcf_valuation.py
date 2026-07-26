import unittest

from dcf_valuation import compute_dcf_estimate


def _cash_flow(values: list[float | None], label: str = "Cash from Operating Activity") -> dict:
    return {
        "years": [str(2019 + i) for i in range(len(values))],
        "rows": [{"label": label, "values": values}],
    }


class ComputeDcfEstimateTest(unittest.TestCase):
    def test_growing_cash_flows_return_a_full_estimate(self) -> None:
        cash_flow = _cash_flow([100.0, 140.0, 190.0, 240.0, 300.0])
        result = compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0)
        self.assertIsNotNone(result)
        self.assertIn(result["verdict"], {"Undervalued", "Overvalued", "Fair"})
        self.assertEqual(result["current_price"], 500.0)
        self.assertEqual(result["discount_rate"], 12.0)
        self.assertEqual(result["terminal_growth"], 5.0)
        self.assertEqual(result["latest_ocf_cr"], 300.0)
        self.assertGreater(result["fair_value_per_share"], 0)

    def test_growth_rate_is_clamped(self) -> None:
        # 10x over 4 years is a far higher CAGR than the +25% clamp allows.
        cash_flow = _cash_flow([10.0, 20.0, 50.0, 80.0, 100.0])
        result = compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0)
        self.assertIsNotNone(result)
        self.assertLessEqual(result["growth_rate_used"], 25.0)

    def test_upside_verdict_thresholds(self) -> None:
        # Small market cap relative to a large, growing OCF pushes fair value
        # well above current price -> Undervalued.
        cash_flow = _cash_flow([100.0, 140.0, 190.0, 240.0, 300.0])
        result = compute_dcf_estimate(cash_flow, current_price=10.0, market_cap_cr=100.0)
        self.assertEqual(result["verdict"], "Undervalued")
        self.assertGreaterEqual(result["upside_pct"], 20.0)

    def test_no_cash_flow_returns_none(self) -> None:
        self.assertIsNone(compute_dcf_estimate(None, current_price=500.0, market_cap_cr=50000.0))

    def test_no_operating_activity_row_returns_none(self) -> None:
        cash_flow = {"years": ["2023"], "rows": [{"label": "Cash from Investing Activity", "values": [-50.0]}]}
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0))

    def test_negative_latest_ocf_returns_none(self) -> None:
        cash_flow = _cash_flow([50.0, 20.0, -10.0])
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0))

    def test_zero_latest_ocf_returns_none(self) -> None:
        cash_flow = _cash_flow([50.0, 20.0, 0.0])
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0))

    def test_too_few_history_points_returns_none(self) -> None:
        cash_flow = _cash_flow([50.0, 60.0])
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0))

    def test_all_null_history_returns_none(self) -> None:
        cash_flow = _cash_flow([None, None, None])
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0))

    def test_missing_current_price_returns_none(self) -> None:
        cash_flow = _cash_flow([100.0, 140.0, 190.0])
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=None, market_cap_cr=50000.0))

    def test_zero_current_price_returns_none(self) -> None:
        cash_flow = _cash_flow([100.0, 140.0, 190.0])
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=0.0, market_cap_cr=50000.0))

    def test_missing_market_cap_returns_none(self) -> None:
        cash_flow = _cash_flow([100.0, 140.0, 190.0])
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=None))

    def test_non_positive_market_cap_returns_none(self) -> None:
        cash_flow = _cash_flow([100.0, 140.0, 190.0])
        self.assertIsNone(compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=0.0))

    def test_flat_ocf_uses_zero_growth(self) -> None:
        cash_flow = _cash_flow([100.0, 100.0, 100.0, 100.0])
        result = compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0)
        self.assertIsNotNone(result)
        self.assertEqual(result["growth_rate_used"], 0.0)

    def test_negative_earliest_value_makes_cagr_unusable(self) -> None:
        # A CAGR needs a positive base year to be meaningful; here the
        # earliest OCF value is negative, so _historical_cagr bails out and
        # the whole estimate comes back None rather than a nonsensical rate.
        cash_flow = _cash_flow([-10.0, 50.0, 80.0, 120.0])
        result = compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0)
        self.assertIsNone(result)

    def test_row_matching_is_case_insensitive_substring(self) -> None:
        cash_flow = _cash_flow([100.0, 140.0, 190.0], label="Net Cash from Operating Activities")
        result = compute_dcf_estimate(cash_flow, current_price=500.0, market_cap_cr=50000.0)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
