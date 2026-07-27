import os
import unittest
from unittest.mock import MagicMock, patch

import watchlist_alerts


class _FakeConn:
    def __init__(self, result) -> None:
        self._result = result
        self.calls: list = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class GetWatchedSymbolsTest(unittest.TestCase):
    def test_groups_rows_by_symbol(self) -> None:
        rows = [
            {"symbol": "TCS", "user_id": 1, "email": "a@example.com"},
            {"symbol": "TCS", "user_id": 2, "email": "b@example.com"},
            {"symbol": "INFY", "user_id": 1, "email": "a@example.com"},
        ]
        result_mock = MagicMock()
        result_mock.mappings.return_value.fetchall.return_value = rows
        conn = _FakeConn(result_mock)
        engine = MagicMock()
        engine.connect.return_value = conn

        by_symbol = watchlist_alerts._get_watched_symbols(engine)

        self.assertEqual(set(by_symbol), {"TCS", "INFY"})
        self.assertEqual(len(by_symbol["TCS"]), 2)
        self.assertEqual(by_symbol["INFY"], [{"user_id": 1, "email": "a@example.com"}])

    def test_empty_rows_returns_empty_dict(self) -> None:
        result_mock = MagicMock()
        result_mock.mappings.return_value.fetchall.return_value = []
        conn = _FakeConn(result_mock)
        engine = MagicMock()
        engine.connect.return_value = conn

        self.assertEqual(watchlist_alerts._get_watched_symbols(engine), {})


class DetectChangeTest(unittest.TestCase):
    def test_none_with_fewer_than_two_entries(self) -> None:
        with patch("watchlist_alerts.verdict_history.load_history", return_value=[{"recommendation": "BUY"}]):
            self.assertIsNone(watchlist_alerts._detect_change("TCS"))

    def test_none_when_recommendation_unchanged(self) -> None:
        history = [{"recommendation": "BUY", "confidence": "HIGH"}, {"recommendation": "BUY", "confidence": "HIGH"}]
        with patch("watchlist_alerts.verdict_history.load_history", return_value=history):
            self.assertIsNone(watchlist_alerts._detect_change("TCS"))

    def test_returns_change_dict_when_recommendation_differs(self) -> None:
        history = [{"recommendation": "HOLD", "confidence": "MEDIUM"}, {"recommendation": "BUY", "confidence": "HIGH"}]
        with patch("watchlist_alerts.verdict_history.load_history", return_value=history):
            change = watchlist_alerts._detect_change("TCS")
        self.assertEqual(change, {
            "kind": "recommendation_change",
            "symbol": "TCS", "old_recommendation": "HOLD",
            "new_recommendation": "BUY", "confidence": "HIGH",
        })

    def test_none_when_latest_recommendation_missing(self) -> None:
        history = [{"recommendation": "HOLD"}, {"recommendation": None}]
        with patch("watchlist_alerts.verdict_history.load_history", return_value=history):
            self.assertIsNone(watchlist_alerts._detect_change("TCS"))


class DetectPriceMoveTest(unittest.TestCase):
    def test_none_with_fewer_than_two_entries(self) -> None:
        with patch("watchlist_alerts.verdict_history.load_history", return_value=[{"current_price": 100.0}]):
            self.assertIsNone(watchlist_alerts._detect_price_move("TCS"))

    def test_none_when_move_under_threshold(self) -> None:
        history = [{"current_price": 100.0}, {"current_price": 105.0}]  # +5%, under the 10% threshold
        with patch("watchlist_alerts.verdict_history.load_history", return_value=history):
            self.assertIsNone(watchlist_alerts._detect_price_move("TCS"))

    def test_returns_move_dict_when_move_at_or_above_threshold(self) -> None:
        history = [{"current_price": 100.0}, {"current_price": 112.0}]  # +12%
        with patch("watchlist_alerts.verdict_history.load_history", return_value=history):
            move = watchlist_alerts._detect_price_move("TCS")
        self.assertEqual(move, {
            "kind": "price_move", "symbol": "TCS",
            "old_price": 100.0, "new_price": 112.0, "change_pct": 12.0,
        })

    def test_detects_downward_move_too(self) -> None:
        history = [{"current_price": 100.0}, {"current_price": 85.0}]  # -15%
        with patch("watchlist_alerts.verdict_history.load_history", return_value=history):
            move = watchlist_alerts._detect_price_move("TCS")
        self.assertEqual(move["change_pct"], -15.0)

    def test_none_when_either_price_missing(self) -> None:
        history = [{"current_price": None}, {"current_price": 112.0}]
        with patch("watchlist_alerts.verdict_history.load_history", return_value=history):
            self.assertIsNone(watchlist_alerts._detect_price_move("TCS"))

    def test_none_when_prior_price_is_zero(self) -> None:
        history = [{"current_price": 0.0}, {"current_price": 112.0}]
        with patch("watchlist_alerts.verdict_history.load_history", return_value=history):
            self.assertIsNone(watchlist_alerts._detect_price_move("TCS"))


class AnalyzeSymbolTest(unittest.TestCase):
    def _fake_signal_result(self):
        sig = MagicMock()
        sig.final_score = 5.0
        sig.verdict = "BUY"
        sig.signals = {}
        return sig

    def test_cache_hit_skips_llm_and_still_saves_snapshot(self) -> None:
        with patch("watchlist_alerts.cache.is_fresh", return_value=True), \
             patch("watchlist_alerts.cache.load", return_value={"recommendation": "HOLD"}), \
             patch("watchlist_alerts.run_analysis_with_fallback") as run_analysis, \
             patch("watchlist_alerts.verdict_history.save_snapshot") as save_snapshot:
            result = watchlist_alerts._analyze_symbol("TCS", "run1")

        run_analysis.assert_not_called()
        save_snapshot.assert_called_once()
        self.assertEqual(result, {"recommendation": "HOLD"})

    def test_stale_tasks_fetched_and_llm_run(self) -> None:
        def fake_fetch_task(name, symbol, run_id):
            return {"raw": name}

        with patch("watchlist_alerts.cache.is_fresh", return_value=False), \
             patch("watchlist_alerts.cache.load", return_value=None), \
             patch("watchlist_alerts.cache.save") as cache_save, \
             patch("watchlist_alerts._fetch_task", side_effect=fake_fetch_task), \
             patch("watchlist_alerts.schema_normalize", side_effect=lambda name, data: {**data, "symbol": "TCS"} if name != "stock_info" else {"current_price": 100.0}), \
             patch("watchlist_alerts.run_signal_engine", return_value=self._fake_signal_result()), \
             patch("watchlist_alerts.run_analysis_with_fallback", return_value={"recommendation": "BUY"}) as run_analysis, \
             patch("watchlist_alerts.verdict_history.save_snapshot") as save_snapshot:
            result = watchlist_alerts._analyze_symbol("TCS", "run1")

        run_analysis.assert_called_once()
        self.assertTrue(cache_save.called)
        save_snapshot.assert_called_once()
        self.assertEqual(result, {"recommendation": "BUY"})

    def test_missing_stock_info_returns_none(self) -> None:
        with patch("watchlist_alerts.cache.is_fresh", return_value=False), \
             patch("watchlist_alerts.cache.load", return_value=None), \
             patch("watchlist_alerts._fetch_task", return_value={"error": "boom"}), \
             patch("watchlist_alerts.schema_normalize", return_value={"error": "boom"}), \
             patch("watchlist_alerts.run_analysis_with_fallback") as run_analysis:
            result = watchlist_alerts._analyze_symbol("TCS", "run1")

        self.assertIsNone(result)
        run_analysis.assert_not_called()

    def test_exception_is_isolated_and_returns_none(self) -> None:
        with patch("watchlist_alerts.cache.is_fresh", side_effect=RuntimeError("boom")):
            result = watchlist_alerts._analyze_symbol("TCS", "run1")
        self.assertIsNone(result)

    def test_force_bypasses_cache_entirely(self) -> None:
        with patch("watchlist_alerts.cache.is_fresh") as is_fresh, \
             patch("watchlist_alerts.cache.load"), \
             patch("watchlist_alerts.cache.save"), \
             patch("watchlist_alerts._fetch_task", return_value={"current_price": 100.0}), \
             patch("watchlist_alerts.schema_normalize", side_effect=lambda name, data: data), \
             patch("watchlist_alerts.run_signal_engine", return_value=self._fake_signal_result()), \
             patch("watchlist_alerts.run_analysis_with_fallback", return_value={"recommendation": "BUY"}), \
             patch("watchlist_alerts.verdict_history.save_snapshot"):
            watchlist_alerts._analyze_symbol("TCS", "run1", force=True)

        is_fresh.assert_not_called()


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {k: os.environ.pop(k, None) for k in ("DATABASE_URL", "ANTHROPIC_API_KEY", "LLM_PROVIDER")}

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_false_without_database_url(self) -> None:
        self.assertFalse(watchlist_alerts.run())

    def test_false_without_llm_key(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        self.assertFalse(watchlist_alerts.run())

    def test_true_with_no_watched_symbols(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        with patch("watchlist_alerts.get_engine", return_value=MagicMock()), \
             patch("watchlist_alerts._get_watched_symbols", return_value={}):
            self.assertTrue(watchlist_alerts.run())

    def test_sends_alert_email_only_for_changed_symbols(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        by_symbol = {
            "TCS": [{"user_id": 1, "email": "a@example.com"}],
            "INFY": [{"user_id": 1, "email": "a@example.com"}, {"user_id": 2, "email": "b@example.com"}],
        }
        change = {"kind": "recommendation_change", "symbol": "TCS", "old_recommendation": "HOLD", "new_recommendation": "BUY", "confidence": "HIGH"}

        def fake_detect(symbol, changes=None):
            return change if symbol == "TCS" else None

        with patch("watchlist_alerts.get_engine", return_value=MagicMock()), \
             patch("watchlist_alerts._get_watched_symbols", return_value=by_symbol), \
             patch("watchlist_alerts._analyze_symbol", return_value={"recommendation": "BUY"}), \
             patch("watchlist_alerts._detect_change", side_effect=fake_detect), \
             patch("watchlist_alerts._detect_price_move", return_value=None), \
             patch("watchlist_alerts.send_watchlist_alert_email", return_value=True) as send_email:
            result = watchlist_alerts.run()

        self.assertTrue(result)
        send_email.assert_called_once_with("a@example.com", [change])

    def test_sends_alert_for_price_move_even_without_recommendation_change(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        by_symbol = {"TCS": [{"user_id": 1, "email": "a@example.com"}]}
        move = {"kind": "price_move", "symbol": "TCS", "old_price": 100.0, "new_price": 115.0, "change_pct": 15.0}

        with patch("watchlist_alerts.get_engine", return_value=MagicMock()), \
             patch("watchlist_alerts._get_watched_symbols", return_value=by_symbol), \
             patch("watchlist_alerts._analyze_symbol", return_value={"recommendation": "HOLD"}), \
             patch("watchlist_alerts._detect_change", return_value=None), \
             patch("watchlist_alerts._detect_price_move", return_value=move), \
             patch("watchlist_alerts.send_watchlist_alert_email", return_value=True) as send_email:
            result = watchlist_alerts.run()

        self.assertTrue(result)
        send_email.assert_called_once_with("a@example.com", [move])

    def test_both_alert_kinds_included_for_the_same_symbol(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        by_symbol = {"TCS": [{"user_id": 1, "email": "a@example.com"}]}
        change = {"kind": "recommendation_change", "symbol": "TCS", "old_recommendation": "HOLD", "new_recommendation": "SELL", "confidence": "HIGH"}
        move = {"kind": "price_move", "symbol": "TCS", "old_price": 100.0, "new_price": 80.0, "change_pct": -20.0}

        with patch("watchlist_alerts.get_engine", return_value=MagicMock()), \
             patch("watchlist_alerts._get_watched_symbols", return_value=by_symbol), \
             patch("watchlist_alerts._analyze_symbol", return_value={"recommendation": "SELL"}), \
             patch("watchlist_alerts._detect_change", return_value=change), \
             patch("watchlist_alerts._detect_price_move", return_value=move), \
             patch("watchlist_alerts.send_watchlist_alert_email", return_value=True) as send_email:
            result = watchlist_alerts.run()

        self.assertTrue(result)
        send_email.assert_called_once_with("a@example.com", [change, move])

    def test_caps_symbol_count(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        by_symbol = {f"SYM{i}": [{"user_id": 1, "email": "a@example.com"}] for i in range(watchlist_alerts._MAX_ALERT_SYMBOLS + 5)}

        with patch("watchlist_alerts.get_engine", return_value=MagicMock()), \
             patch("watchlist_alerts._get_watched_symbols", return_value=by_symbol), \
             patch("watchlist_alerts._analyze_symbol", return_value={"recommendation": "HOLD"}) as analyze, \
             patch("watchlist_alerts._detect_change", return_value=None), \
             patch("watchlist_alerts._detect_price_move", return_value=None), \
             patch("watchlist_alerts.send_watchlist_alert_email"):
            watchlist_alerts.run()

        self.assertEqual(analyze.call_count, watchlist_alerts._MAX_ALERT_SYMBOLS)

    def test_returns_false_when_error_rate_exceeds_threshold(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        by_symbol = {"A": [{"user_id": 1, "email": "a@example.com"}], "B": [{"user_id": 1, "email": "a@example.com"}]}

        with patch("watchlist_alerts.get_engine", return_value=MagicMock()), \
             patch("watchlist_alerts._get_watched_symbols", return_value=by_symbol), \
             patch("watchlist_alerts._analyze_symbol", return_value=None), \
             patch("watchlist_alerts.send_watchlist_alert_email"):
            self.assertFalse(watchlist_alerts.run())

    def test_ollama_provider_does_not_require_api_key(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        os.environ["LLM_PROVIDER"] = "ollama"
        with patch("watchlist_alerts.get_engine", return_value=MagicMock()), \
             patch("watchlist_alerts._get_watched_symbols", return_value={}):
            self.assertTrue(watchlist_alerts.run())

    def test_detect_recent_changes_is_called_once_per_symbol_not_twice(self) -> None:
        # Regression test for an adversarial-review finding: run() used to
        # call watchlist_alerts._detect_change(symbol) and
        # _detect_price_move(symbol) with no shared state between them, and
        # each independently called verdict_history.detect_recent_changes()
        # -- which itself does one full DB read of the last two stored
        # snapshots to compute BOTH the recommendation-change and
        # price-move verdicts. That doubled the DB round-trips per watched
        # symbol for no benefit, since one shared call already has
        # everything both checks need. run() must now fetch it once per
        # symbol and pass the result down to both checks.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        by_symbol = {
            "TCS": [{"user_id": 1, "email": "a@example.com"}],
            "INFY": [{"user_id": 1, "email": "a@example.com"}],
        }

        with patch("watchlist_alerts.get_engine", return_value=MagicMock()), \
             patch("watchlist_alerts._get_watched_symbols", return_value=by_symbol), \
             patch("watchlist_alerts._analyze_symbol", return_value={"recommendation": "HOLD"}), \
             patch(
                 "watchlist_alerts.verdict_history.detect_recent_changes",
                 return_value={"recommendation_change": None, "price_move": None},
             ) as mock_detect, \
             patch("watchlist_alerts.send_watchlist_alert_email"):
            self.assertTrue(watchlist_alerts.run())

        # Exactly one call per watched symbol (2 symbols -> 2 calls), not
        # one per symbol per alert-kind (which would be 4).
        self.assertEqual(mock_detect.call_count, 2)
        called_symbols = {call.args[0] for call in mock_detect.call_args_list}
        self.assertEqual(called_symbols, {"TCS", "INFY"})


if __name__ == "__main__":
    unittest.main()
