import unittest

from peer_analytics import compute_peer_percentiles, compute_valuation_anchor


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


if __name__ == "__main__":
    unittest.main()
