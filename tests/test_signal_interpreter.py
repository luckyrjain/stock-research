import unittest

from signals.interpreter import interpret
from signals.models import Signal, SignalResult


def _sig(name: str, value: str, score: float = 0.0) -> Signal:
    return Signal(name, value, score, {})


class InterpretTest(unittest.TestCase):
    def test_all_unknown_signals_reports_insufficient_data(self) -> None:
        # Regression test for an adversarial-review finding: the
        # "insufficient signal data" guard was written as `not g or not v`,
        # which is unreachable dead code -- run_signal_engine() always
        # populates every signals dict key with a real Signal instance
        # (never None), so growth/valuation data that's genuinely missing
        # shows up as Signal(value="UNKNOWN", ...), not a falsy object. A
        # stock with completely missing growth/valuation data used to read
        # the confident-sounding "Neutral setup" instead of any indication
        # the underlying data was missing.
        sr = SignalResult(
            symbol="NEWCO",
            signals={
                "growth": _sig("growth", "UNKNOWN"),
                "valuation": _sig("valuation", "UNKNOWN"),
                "volume": _sig("volume", "UNKNOWN"),
                "filings": _sig("filings", "NONE"),
                "technical": _sig("technical", "UNKNOWN"),
                "macro": _sig("macro", "NEUTRAL"),
            },
            final_score=0.0,
            verdict="HOLD",
        )
        self.assertEqual(interpret(sr), "Insufficient signal data")

    def test_unknown_growth_alone_reports_insufficient_data(self) -> None:
        sr = SignalResult(
            symbol="NEWCO",
            signals={
                "growth": _sig("growth", "UNKNOWN"),
                "valuation": _sig("valuation", "FAIR"),
                "volume": _sig("volume", "NORMAL"),
            },
            final_score=0.0,
            verdict="HOLD",
        )
        self.assertEqual(interpret(sr), "Insufficient signal data")

    def test_unknown_valuation_alone_reports_insufficient_data(self) -> None:
        sr = SignalResult(
            symbol="NEWCO",
            signals={
                "growth": _sig("growth", "MODERATE_GROWTH"),
                "valuation": _sig("valuation", "UNKNOWN"),
                "volume": _sig("volume", "NORMAL"),
            },
            final_score=0.0,
            verdict="HOLD",
        )
        self.assertEqual(interpret(sr), "Insufficient signal data")

    def test_high_growth_overvalued_combination(self) -> None:
        sr = SignalResult(
            symbol="TCS",
            signals={
                "growth": _sig("growth", "HIGH_GROWTH"),
                "valuation": _sig("valuation", "OVERVALUED"),
                "volume": _sig("volume", "NORMAL"),
            },
            final_score=0.2,
            verdict="HOLD",
        )
        self.assertEqual(interpret(sr), "High-quality company trading at premium. Wait for better entry.")

    def test_extreme_overvalued_with_normal_volume(self) -> None:
        sr = SignalResult(
            symbol="TCS",
            signals={
                "growth": _sig("growth", "MODERATE_GROWTH"),
                "valuation": _sig("valuation", "EXTREME_OVERVALUED"),
                "volume": _sig("volume", "NORMAL"),
            },
            final_score=-0.2,
            verdict="HOLD",
        )
        self.assertEqual(interpret(sr), "No accumulation. Risk of sideways or correction.")

    def test_low_growth(self) -> None:
        sr = SignalResult(
            symbol="TCS",
            signals={
                "growth": _sig("growth", "LOW_GROWTH"),
                "valuation": _sig("valuation", "FAIR"),
                "volume": _sig("volume", "NORMAL"),
            },
            final_score=-0.1,
            verdict="HOLD",
        )
        self.assertEqual(interpret(sr), "Weak fundamentals. Avoid unless turnaround visible.")

    def test_neutral_fallback(self) -> None:
        sr = SignalResult(
            symbol="TCS",
            signals={
                "growth": _sig("growth", "MODERATE_GROWTH"),
                "valuation": _sig("valuation", "FAIR"),
                "volume": _sig("volume", "NORMAL"),
            },
            final_score=0.1,
            verdict="HOLD",
        )
        self.assertEqual(interpret(sr), "Neutral setup")


if __name__ == "__main__":
    unittest.main()
