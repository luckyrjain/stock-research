import unittest

from peer_analytics import build_peer_result, compute_peer_percentiles, compute_valuation_anchor


class ComputePeerPercentilesTest(unittest.TestCase):
    def test_tie_is_split_evenly(self) -> None:
        self_row = {"values": {"P/E": "25"}}
        peers = [{"values": {"P/E": "25"}}, {"values": {"P/E": "20"}}]
        result = compute_peer_percentiles(self_row, peers)
        self.assertEqual(result["P/E"], 66.7)

    def test_no_self_row_returns_empty(self) -> None:
        self.assertEqual(compute_peer_percentiles(None, [{"values": {"P/E": "20"}}]), {})

    def test_no_peers_returns_empty(self) -> None:
        self.assertEqual(compute_peer_percentiles({"values": {"P/E": "20"}}, []), {})


class ComputeValuationAnchorTest(unittest.TestCase):
    def test_computes_band_and_percentile(self) -> None:
        self_row = {"values": {"P/E": "24"}}
        band = {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 22.0, 26.0]}
        result = compute_valuation_anchor(self_row, band)
        self.assertEqual(result["percentile"], 66.7)
        self.assertEqual(result["low"], 20.0)
        self.assertEqual(result["high"], 26.0)

    def test_fewer_than_three_years_returns_none(self) -> None:
        self_row = {"values": {"P/E": "24"}}
        band = {"years": ["Mar 2023", "Mar 2024"], "pe": [22.0, 26.0]}
        self.assertIsNone(compute_valuation_anchor(self_row, band))

    def test_no_self_row_returns_none(self) -> None:
        band = {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 22.0, 26.0]}
        self.assertIsNone(compute_valuation_anchor(None, band))

    def test_negative_current_pe_returns_none_not_a_fabricated_cheap_percentile(self) -> None:
        # Regression test for an adversarial-review finding: a negative P/E
        # (a currently loss-making company) used to rank as arithmetically
        # "cheapest" against a positive historical P/E band -- percentile
        # near 0, rendered as bullish "cheap vs. its own history" and
        # feeding a confidence-score nudge in market_picks_pipeline.py.
        # Same anti-pattern signals/valuation.py::valuation_signal() already
        # guards against for the identical reason: a negative P/E says
        # nothing about over/undervaluation.
        self_row = {"values": {"P/E": "-42.3"}}
        band = {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [18.0, 22.0, 26.0]}
        self.assertIsNone(compute_valuation_anchor(self_row, band))

    def test_zero_current_pe_returns_none(self) -> None:
        self_row = {"values": {"P/E": "0"}}
        band = {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [18.0, 22.0, 26.0]}
        self.assertIsNone(compute_valuation_anchor(self_row, band))


class BuildPeerResultTest(unittest.TestCase):
    """build_peer_result() is the single source of truth for the shape
    cached under cache key "peers" — both api.py's GET /api/peers/{symbol}
    and market_picks_pipeline.py's valuation-percentile fetch read/write
    this exact shape so they can transparently share one cache entry."""

    def test_shapes_a_raw_scrape_into_the_cached_response(self) -> None:
        raw = {
            "symbol": "TCS",
            "self": {"values": {"P/E": "22"}},
            "peers": [{"values": {"P/E": "20"}}],
            "sector_median": {"values": {"P/E": "21"}},
            "valuation_band": {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 25.0, 30.0]},
        }
        result = build_peer_result("TCS", raw)
        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["self"], raw["self"])
        self.assertEqual(result["peers"], raw["peers"])
        self.assertEqual(result["sector_median"], raw["sector_median"])
        self.assertIn("P/E", result["percentiles"])
        self.assertEqual(result["absolute_anchor"]["percentile"], 33.3)

    def test_missing_valuation_band_yields_null_anchor(self) -> None:
        raw = {"symbol": "TCS", "self": {"values": {"P/E": "22"}}, "peers": []}
        result = build_peer_result("TCS", raw)
        self.assertIsNone(result["absolute_anchor"])

    def test_falls_back_to_the_passed_symbol_when_raw_lacks_one(self) -> None:
        result = build_peer_result("TCS", {})
        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["peers"], [])
        self.assertIsNone(result["self"])


if __name__ == "__main__":
    unittest.main()
