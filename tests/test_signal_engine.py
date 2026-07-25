import unittest
from unittest.mock import patch

from signals.features import extract_features
from signals.engine import run_signal_engine
from signals.models import Signal


def _sig(score: float) -> Signal:
    return Signal(name="x", value="x", score=score, meta={})


class ExtractFeaturesTest(unittest.TestCase):
    def test_pulls_expected_fields_from_stock_info_and_research(self) -> None:
        all_data = {
            "stock_info": {
                "current_price": 100.0, "volume": 5000, "avg_volume_10d": 4000,
                "pe_ratio": 20.5, "market_cap_cr": 1200.0, "sector": "IT",
            },
            "research": {"ratios": {"ROE": "18%"}},
            "filings": {"filings": [{"title": "Board meeting"}]},
        }
        features = extract_features(all_data)
        self.assertEqual(features["price"], 100.0)
        self.assertEqual(features["volume"], 5000)
        self.assertEqual(features["avg_volume"], 4000)
        self.assertEqual(features["pe"], 20.5)
        self.assertEqual(features["market_cap"], 1200.0)
        self.assertEqual(features["sector"], "IT")
        self.assertEqual(features["ratios"], {"ROE": "18%"})
        self.assertEqual(features["filings"], [{"title": "Board meeting"}])

    def test_missing_sections_default_to_empty_without_crashing(self) -> None:
        features = extract_features({})
        self.assertIsNone(features["price"])
        self.assertEqual(features["ratios"], {})
        self.assertEqual(features["filings"], [])


class RunSignalEngineTest(unittest.TestCase):
    def _run(self, volume, valuation, growth, filings, technical=None, macro=None, symbol="TCS"):
        # technical/macro default to a neutral (score 0) signal — they're the
        # only signals that do their own I/O (see signals/technical.py,
        # signals/macro.py), so they're always patched here rather than
        # letting run_signal_engine call the real network-fetching
        # implementation in a unit test.
        with patch("signals.engine.volume_signal", return_value=volume), \
             patch("signals.engine.valuation_signal", return_value=valuation), \
             patch("signals.engine.growth_signal", return_value=growth), \
             patch("signals.engine.filings_signal", return_value=filings), \
             patch("signals.engine.technical_signal", return_value=technical or _sig(0.0)), \
             patch("signals.engine.macro_signal", return_value=macro or _sig(0.0)), \
             patch("signals.engine.extract_features", return_value={}):
            return run_signal_engine(symbol, {})

    def test_weighted_score_is_computed_correctly(self) -> None:
        # weights: valuation 0.4, volume 0.2, growth 0.4, filings 0.2,
        # technical 0.2, macro 0.15 — note these sum to 1.55, not 1.0, so
        # final_score is not bounded to [-1, 1] even though each individual
        # signal score is.
        result = self._run(
            volume=_sig(1.0), valuation=_sig(1.0), growth=_sig(1.0), filings=_sig(1.0),
            technical=_sig(1.0), macro=_sig(1.0),
        )
        self.assertAlmostEqual(result.final_score, 1.55, places=2)

        result2 = self._run(volume=_sig(0.0), valuation=_sig(1.0), growth=_sig(0.0), filings=_sig(0.0))
        self.assertAlmostEqual(result2.final_score, 0.4, places=2)

    def test_final_score_is_rounded_to_two_decimals(self) -> None:
        result = self._run(volume=_sig(0.333), valuation=_sig(0.111), growth=_sig(0.777), filings=_sig(0.222))
        self.assertEqual(result.final_score, round(result.final_score, 2))

    def test_verdict_thresholds(self) -> None:
        cases = [
            (1.0, "BUY"),        # > 0.5
            (0.51, "BUY"),
            (0.5, "WATCHLIST"),  # boundary: not > 0.5
            (0.3, "WATCHLIST"),  # > 0.1
            (0.0, "HOLD"),       # > -0.3
            (-0.29, "HOLD"),
            (-0.3, "AVOID"),     # boundary: not > -0.3
            (-0.5, "AVOID"),     # > -0.6
            (-0.6, "SELL"),      # boundary: not > -0.6
            (-1.0, "SELL"),
        ]
        for target_score, expected_verdict in cases:
            # All weight is on valuation (0.4) and growth (0.4) — pick a score s
            # such that 0.8*s == target_score, giving volume/filings score 0.
            s = target_score / 0.8
            with self.subTest(target_score=target_score):
                result = self._run(
                    volume=_sig(0.0), valuation=_sig(s), growth=_sig(s), filings=_sig(0.0),
                )
                self.assertAlmostEqual(result.final_score, target_score, places=2)
                self.assertEqual(result.verdict, expected_verdict)

    def test_missing_signal_is_skipped_not_fatal(self) -> None:
        result = self._run(volume=None, valuation=_sig(1.0), growth=_sig(1.0), filings=None, technical=None)
        # only valuation (0.4) + growth (0.4) contribute
        self.assertAlmostEqual(result.final_score, 0.8, places=2)

    def test_result_carries_symbol_and_all_signals(self) -> None:
        v, val, g, f, t, m = _sig(0.1), _sig(0.2), _sig(0.3), _sig(0.4), _sig(0.5), _sig(0.6)
        result = self._run(volume=v, valuation=val, growth=g, filings=f, technical=t, macro=m, symbol="INFY")
        self.assertEqual(result.symbol, "INFY")
        self.assertEqual(
            result.signals,
            {"volume": v, "valuation": val, "growth": g, "filings": f, "technical": t, "macro": m},
        )


if __name__ == "__main__":
    unittest.main()
