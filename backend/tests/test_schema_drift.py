import logging
import unittest
from unittest.mock import MagicMock, patch

from core import schema_drift
from main import _fetch_task


class CheckDriftTest(unittest.TestCase):
    def test_no_drift_when_types_match(self) -> None:
        problems = schema_drift.check_drift("research", {
            "symbol": "TCS", "ratios": {"P/E": "28"}, "quarterly_trend": {"quarters": []},
        })
        self.assertEqual(problems, [])

    def test_flags_dict_field_that_became_a_list(self) -> None:
        problems = schema_drift.check_drift("research", {
            "symbol": "TCS", "ratios": [{"P/E": "28"}],
        })
        self.assertEqual(len(problems), 1)
        self.assertIn("'ratios'", problems[0])
        self.assertIn("expected dict", problems[0])
        self.assertIn("got list", problems[0])

    def test_flags_list_field_that_became_a_dict(self) -> None:
        problems = schema_drift.check_drift("news", {"articles": {"0": "not a list"}})
        self.assertEqual(len(problems), 1)
        self.assertIn("'articles'", problems[0])

    def test_missing_optional_field_is_not_drift(self) -> None:
        # quarterly_trend is legitimately absent for a recent IPO with too
        # little Screener history — must never be flagged as drift.
        problems = schema_drift.check_drift("research", {"symbol": "TCS", "ratios": {}})
        self.assertEqual(problems, [])

    def test_none_value_is_not_drift(self) -> None:
        # A *typed* field explicitly present-but-None (this codebase's
        # "never invent" convention for a genuinely unknown value) isn't a
        # type mismatch either — quarterly_trend is in research's "types"
        # map, unlike pledge_pct (untyped, scalar-only), so this actually
        # exercises the `value is not None` guard in check_drift().
        problems = schema_drift.check_drift("research", {
            "symbol": "TCS", "ratios": {}, "quarterly_trend": None,
        })
        self.assertEqual(problems, [])

    def test_error_payload_is_never_checked(self) -> None:
        problems = schema_drift.check_drift("research", {"error": "boom", "ratios": "not-a-dict"})
        self.assertEqual(problems, [])

    def test_non_dict_payload_returns_no_problems(self) -> None:
        self.assertEqual(schema_drift.check_drift("research", "not a dict"), [])
        self.assertEqual(schema_drift.check_drift("research", None), [])

    def test_unknown_task_returns_no_problems(self) -> None:
        problems = schema_drift.check_drift("price_history", {"anything": "goes"})
        self.assertEqual(problems, [])

    def test_multiple_fields_can_each_be_flagged(self) -> None:
        problems = schema_drift.check_drift("research", {
            "symbol": "TCS", "ratios": "oops", "quarterly_trend": "also oops",
        })
        self.assertEqual(len(problems), 2)


class LogDriftIfAnyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("stock_research.test_schema_drift")
        self.logger.addHandler(logging.NullHandler())

    def test_logs_warning_when_drift_found(self) -> None:
        with patch("core.schema_drift.log_event") as mock_log:
            schema_drift.log_drift_if_any("research", {"symbol": "TCS", "ratios": []}, run_id="r1", symbol="TCS")

        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(args[1], "schema_drift_detected")
        self.assertEqual(kwargs["level"], "warning")
        self.assertEqual(kwargs["task"], "research")
        self.assertEqual(kwargs["run_id"], "r1")
        self.assertEqual(kwargs["symbol"], "TCS")
        self.assertEqual(len(kwargs["problems"]), 1)

    def test_does_not_log_when_no_drift(self) -> None:
        with patch("core.schema_drift.log_event") as mock_log:
            schema_drift.log_drift_if_any("research", {"symbol": "TCS", "ratios": {}})

        mock_log.assert_not_called()

    def test_never_raises_even_if_logging_itself_fails(self) -> None:
        with patch("core.schema_drift.log_event", side_effect=RuntimeError("boom")):
            schema_drift.log_drift_if_any("research", {"symbol": "TCS", "ratios": []})  # must not raise

    def test_never_raises_on_malformed_input(self) -> None:
        schema_drift.log_drift_if_any("research", object())  # must not raise

    def test_check_drift_bug_is_traced_not_silently_swallowed(self) -> None:
        # A bug in check_drift() itself (e.g. a future CONTRACTS["types"]
        # typo) must leave some trace rather than silently disabling the
        # whole feature — same instinct as core/error_tracking.py's own
        # capture_error() logging once before it degrades.
        with patch("core.schema_drift.check_drift", side_effect=RuntimeError("boom")), \
             patch("core.schema_drift.log_event") as mock_log:
            schema_drift.log_drift_if_any("research", {"symbol": "TCS"})  # must not raise

        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(args[1], "schema_drift_check_failed")
        self.assertEqual(kwargs["level"], "warning")
        self.assertEqual(kwargs["task"], "research")
        self.assertIn("boom", kwargs["error"])


class FetchTaskIntegrationTest(unittest.TestCase):
    """Confirms main._fetch_task — the single choke point all six data-slice
    fetches go through for both the CLI and api.py's SSE endpoint — actually
    invokes schema_drift on every successful attempt."""

    def test_dict_payload_triggers_drift_check(self) -> None:
        fake_tool = MagicMock()
        fake_tool.run.return_value = {"symbol": "TCS", "ratios": "not-a-dict"}

        with patch("main.get_fundamentals", fake_tool), \
             patch("main.log_drift_if_any") as mock_drift:
            result = _fetch_task("research", "TCS", "run-1")

        self.assertEqual(result, {"symbol": "TCS", "ratios": "not-a-dict"})
        mock_drift.assert_called_once_with("research", result, run_id="run-1", symbol="TCS")

    def test_drift_check_runs_even_when_no_drift_present(self) -> None:
        # log_drift_if_any is always invoked on success; whether it actually
        # logs anything is check_drift's decision, tested separately above.
        fake_tool = MagicMock()
        fake_tool.run.return_value = {"symbol": "TCS", "ratios": {}}

        with patch("main.get_fundamentals", fake_tool), \
             patch("main.log_drift_if_any") as mock_drift:
            _fetch_task("research", "TCS", "run-1")

        mock_drift.assert_called_once()

    def test_json_text_payload_triggers_drift_check(self) -> None:
        # In real production every tool except filings returns a
        # json.dumps(...) string via crewai's .run() convention, so
        # _fetch_task takes the parse_json_object branch, not the plain-dict
        # branch above — that's a second, separate call site for
        # log_drift_if_any (main.py's JSON-text success path) that must be
        # covered independently, or a regression there would go undetected.
        fake_tool = MagicMock()
        fake_tool.run.return_value = '{"symbol": "TCS", "ratios": "not-a-dict"}'

        with patch("main.get_fundamentals", fake_tool), \
             patch("main.log_drift_if_any") as mock_drift:
            result = _fetch_task("research", "TCS", "run-1")

        self.assertEqual(result, {"symbol": "TCS", "ratios": "not-a-dict"})
        mock_drift.assert_called_once_with("research", result, run_id="run-1", symbol="TCS")

    def test_error_payload_still_reaches_drift_check_as_a_noop(self) -> None:
        # check_drift() itself is what skips error payloads (see
        # CheckDriftTest.test_error_payload_is_never_checked) — _fetch_task
        # doesn't special-case this, it's the same success path either way
        # since dispatch[task_name]() returned a dict without raising.
        fake_tool = MagicMock()
        fake_tool.run.return_value = {"error": "Screener.in unreachable", "symbol": "TCS"}

        with patch("main.get_fundamentals", fake_tool):
            result = _fetch_task("research", "TCS", "run-1")

        self.assertEqual(result["error"], "Screener.in unreachable")


if __name__ == "__main__":
    unittest.main()
