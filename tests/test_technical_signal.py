import unittest
from unittest.mock import patch

from signals.technical import technical_signal


def _flat(n: int, price: float = 100.0) -> list[float]:
    return [price] * n


def _uptrend(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + i * step for i in range(n)]


def _downtrend(n: int, start: float = 200.0, step: float = 1.0) -> list[float]:
    return [start - i * step for i in range(n)]


def _zigzag(n: int, start: float = 100.0, up: float = 3.0, down: float = 2.0) -> list[float]:
    """Alternating up/down deltas with a net drift (up - down per 2-day
    cycle) — unlike a strictly monotonic series, this keeps RSI in a
    moderate band (mixed gains AND losses feed the average) while still
    giving EMA20/EMA50 a clear directional posture. A purely monotonic
    series drives RSI to ~0/100 for any step size, which is only useful for
    the overbought/oversold test cases below, not a "plain" trend."""
    closes = [start]
    for i in range(1, n):
        closes.append(closes[-1] + (up if i % 2 == 1 else -down))
    return closes


class TechnicalSignalTest(unittest.TestCase):
    def _run(self, closes: list[float]):
        with patch("signals.technical.get_price_series", return_value={"symbol": "TCS", "closes": closes}):
            return technical_signal("TCS")

    def test_insufficient_history_returns_unknown(self) -> None:
        sig = self._run(_flat(54))
        self.assertEqual(sig.value, "UNKNOWN")
        self.assertEqual(sig.score, 0)
        self.assertEqual(sig.meta["closes_available"], 54)

    def test_empty_series_returns_unknown_not_crash(self) -> None:
        sig = self._run([])
        self.assertEqual(sig.value, "UNKNOWN")

    def test_moderate_uptrend_is_bullish_trend(self) -> None:
        sig = self._run(_zigzag(80, start=100, up=3.0, down=2.0))
        self.assertEqual(sig.value, "BULLISH_TREND")
        self.assertEqual(sig.score, 0.5)
        self.assertTrue(sig.meta["ema20_above_ema50"])
        self.assertTrue(30 < sig.meta["rsi14"] < 70)

    def test_moderate_downtrend_is_bearish_trend(self) -> None:
        sig = self._run(_zigzag(80, start=300, up=2.0, down=3.0))
        self.assertEqual(sig.value, "BEARISH_TREND")
        self.assertEqual(sig.score, -0.4)
        self.assertFalse(sig.meta["ema20_above_ema50"])
        self.assertTrue(30 < sig.meta["rsi14"] < 70)

    def test_uptrend_with_overbought_rsi_is_dampened(self) -> None:
        # A steep, uninterrupted uptrend pushes RSI toward 100 (overbought).
        closes = _uptrend(80, start=100, step=3.0)
        sig = self._run(closes)
        self.assertEqual(sig.value, "OVERBOUGHT_UPTREND")
        self.assertEqual(sig.score, 0.3)
        self.assertGreaterEqual(sig.meta["rsi14"], 70)

    def test_downtrend_with_oversold_rsi_is_dampened(self) -> None:
        closes = _downtrend(80, start=300, step=3.0)
        sig = self._run(closes)
        self.assertEqual(sig.value, "OVERSOLD_DOWNTREND")
        self.assertEqual(sig.score, -0.2)
        self.assertLessEqual(sig.meta["rsi14"], 30)

    def test_dip_within_uptrend_is_oversold_in_uptrend(self) -> None:
        # A long gentle uptrend (keeps EMA20 > EMA50) followed by a sharp
        # recent selloff (drags RSI down) without enough days to flip the
        # EMA posture yet.
        closes = _uptrend(70, start=100, step=1.0) + _downtrend(10, start=169, step=5.0)
        sig = self._run(closes)
        self.assertEqual(sig.value, "OVERSOLD_IN_UPTREND")
        self.assertEqual(sig.score, 0.6)
        self.assertTrue(sig.meta["ema20_above_ema50"])
        self.assertLessEqual(sig.meta["rsi14"], 30)

    def test_meta_always_carries_rsi_and_ema_posture(self) -> None:
        sig = self._run(_flat(80))
        self.assertIn("rsi14", sig.meta)
        self.assertIn("ema20_above_ema50", sig.meta)

    def test_flat_series_does_not_crash_on_zero_avg_loss_and_gain(self) -> None:
        sig = self._run(_flat(80))
        self.assertEqual(sig.meta["rsi14"], 50.0)


if __name__ == "__main__":
    unittest.main()
