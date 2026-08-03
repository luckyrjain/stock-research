import multiprocessing
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from telemetry import source_health
from core import state_store
from state_store_harness import isolated_state_store, shared_state_store


def _stored(source: str) -> dict:
    return state_store.load(source_health._NAMESPACE, source_health._safe_name(source)) or {}


def _mp_record_and_check(state_dir: str, source_name: str, date: str, ok: bool) -> None:
    """Top-level (picklable) worker for MultiProcessConcurrencySafetyTest —
    see llm_cost's own equivalent worker for why a real OS process, not a
    thread, is what actually exercises the cross-process guarantee
    state_store.mutate()'s row lock provides. Builds its own engine over the
    parent's SQLite file, since a SQLAlchemy engine is not fork-safe."""
    from telemetry import source_health as _source_health

    with shared_state_store(state_dir, create=False):
        _source_health.record_and_check(source_name, ok, date=date)


class RecordAndCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(isolated_state_store().close)

    def _history(self, source: str) -> list:
        return _stored(source).get("days", [])

    def _oks(self, source: str) -> list:
        return [d["ok"] for d in self._history(source)]

    def _record_on_days(self, source: str, dates_and_oks: list) -> None:
        """Simulate `len(dates_and_oks)` distinct-day calls without sleeping
        in real time, via record_and_check's explicit `date` override."""
        for d, ok in dates_and_oks:
            source_health.record_and_check(source, ok, date=d)

    def test_first_run_is_recorded_with_no_alert(self) -> None:
        with patch("telemetry.source_health.log_event") as mock_log:
            source_health.record_and_check("Test Source", True)
        self.assertEqual(self._oks("Test Source"), [True])
        mock_log.assert_not_called()

    def test_new_source_failing_from_the_start_does_not_alert(self) -> None:
        # No established healthy baseline yet — a source that's simply
        # always been empty (e.g. genuinely no coverage) shouldn't page
        # anyone.
        with patch("telemetry.source_health.log_event") as mock_log:
            self._record_on_days(
                "New Source",
                [(f"2026-01-0{i}", False) for i in range(1, 6)],
            )
        mock_log.assert_not_called()

    def test_healthy_source_failing_three_days_in_a_row_alerts(self) -> None:
        with patch("telemetry.source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)]
                + [(f"2026-01-0{i}", False) for i in range(6, 9)],
            )
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(args[1], "source_health_anomaly")
        self.assertEqual(kwargs["level"], "warning")

    def test_healthy_source_failing_only_two_days_does_not_alert_yet(self) -> None:
        with patch("telemetry.source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)]
                + [(f"2026-01-0{i}", False) for i in range(6, 8)],
            )
        mock_log.assert_not_called()

    def test_alert_fires_only_once_not_every_subsequent_failing_day(self) -> None:
        with patch("telemetry.source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)]
                + [(f"2026-01-0{i}", False) for i in range(6, 9)],
            )
            self.assertEqual(mock_log.call_count, 1)
            self._record_on_days("Reliable Source", [("2026-01-09", False)])
        self.assertEqual(mock_log.call_count, 2)  # still 3 consecutive bad days each time — expected re-alert

    def test_recovery_resets_the_consecutive_failure_streak(self) -> None:
        with patch("telemetry.source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)]
                + [("2026-01-06", False)]
                + [("2026-01-07", False)]
                + [("2026-01-08", True)]  # recovers
                + [("2026-01-09", False)]
                + [("2026-01-10", False)],
            )
        mock_log.assert_not_called()

    def test_multiple_calls_on_the_same_day_collapse_to_one_entry(self) -> None:
        # A burst of same-day force-refresh retries must not each count as
        # their own data point — otherwise 3 quick retries within an hour
        # would trip the "3 consecutive bad days" threshold, which is
        # meant to mean 3 distinct calendar days, not 3 raw calls.
        source_health.record_and_check("Bursty Source", True, date="2026-01-01")
        source_health.record_and_check("Bursty Source", False, date="2026-01-01")
        source_health.record_and_check("Bursty Source", False, date="2026-01-01")
        self.assertEqual(self._history("Bursty Source"), [{"date": "2026-01-01", "ok": False}])

    def test_same_day_collapse_does_not_falsely_alert(self) -> None:
        with patch("telemetry.source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)],
            )
            for _ in range(5):  # a burst of failures, but all on ONE day
                source_health.record_and_check("Reliable Source", False, date="2026-01-06")
        mock_log.assert_not_called()  # only 1 distinct bad day so far, not 3

    def test_history_is_capped_to_max_history_days(self) -> None:
        self._record_on_days(
            "Long Running Source",
            [(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", True) for i in range(source_health._MAX_HISTORY + 10)],
        )
        self.assertEqual(len(self._history("Long Running Source")), source_health._MAX_HISTORY)

    def test_outage_longer_than_max_history_keeps_alerting(self) -> None:
        # Regression test for an adversarial-review finding: was_healthy_baseline
        # used to be derived from `any(d.get("ok") for d in prior_days)` --
        # the rolling window, capped at _MAX_HISTORY entries. A source
        # failing continuously for longer than that eventually ages its own
        # last healthy day out of the window, was_healthy_baseline flips
        # False, and the module silently stops alerting about an outage
        # it's still observing -- precisely when the outage is most severe.
        healthy_days = source_health._MIN_HISTORY_FOR_BASELINE
        total_days = source_health._MAX_HISTORY + 20
        dates = [f"day{i:03d}" for i in range(total_days)]

        fired_on = []
        for i, d in enumerate(dates):
            ok = i < healthy_days
            with patch("telemetry.source_health.log_event") as mock_log:
                source_health.record_and_check("Outage Source", ok, date=d)
            if mock_log.called:
                fired_on.append(i)

        # An alert must still fire for a failing day well past the point
        # where the rolling window has aged every healthy day out of it
        # (index >= healthy_days + _MAX_HISTORY) -- the exact case the old
        # code went silent on.
        past_window_alerts = [i for i in fired_on if i >= healthy_days + source_health._MAX_HISTORY]
        self.assertTrue(past_window_alerts, f"no alert fired past the rolling window; fired_on={fired_on}")

    def test_ever_healthy_flag_persists_across_reads_of_an_old_format_record(self) -> None:
        # A record written before the `ever_healthy` field existed must still
        # correctly derive it (from whatever days are currently stored) on
        # the first read after this fix ships, rather than treating a
        # genuinely-healthy-before source as if it never had a baseline.
        state_store.save(
            source_health._NAMESPACE, source_health._safe_name("Legacy Source"),
            {"days": [{"date": f"2026-01-0{i}", "ok": True} for i in range(1, 6)]},  # no "ever_healthy" key
        )
        with patch("telemetry.source_health.log_event") as mock_log:
            self._record_on_days("Legacy Source", [(f"2026-01-0{i}", False) for i in range(6, 9)])
        mock_log.assert_called_once()  # still correctly alerts on 3 consecutive failures

    def test_different_sources_do_not_share_history(self) -> None:
        source_health.record_and_check("Source A", True)
        source_health.record_and_check("Source B", False)
        self.assertEqual(self._oks("Source A"), [True])
        self.assertEqual(self._oks("Source B"), [False])

    def test_special_characters_in_source_name_do_not_raise(self) -> None:
        with patch("telemetry.source_health.log_event"):
            source_health.record_and_check("Motilal Oswal / ICICI Direct / Axis Securities", True)
        # No exception is the assertion here.

    def test_record_missing_the_days_key_is_treated_as_empty_not_fatal(self) -> None:
        # The stored-JSON equivalent of the old "corrupt file" case: a
        # payload that isn't the shape this module expects must degrade to
        # "no history yet", not raise.
        state_store.save(source_health._NAMESPACE, source_health._safe_name("Bad Record Source"), {})
        with patch("telemetry.source_health.log_event") as mock_log:
            source_health.record_and_check("Bad Record Source", True)
        self.assertEqual(len(self._oks("Bad Record Source")), 1)
        mock_log.assert_not_called()

    def test_never_raises_even_if_the_state_store_is_broken(self) -> None:
        with patch("core.state_store._get_engine", side_effect=RuntimeError("db down")):
            try:
                source_health.record_and_check("Any Source", True)
            except Exception as exc:  # pragma: no cover - the assertion IS that this doesn't happen
                self.fail(f"record_and_check raised: {exc}")


class ConcurrencySafetyTest(unittest.TestCase):
    """Regression coverage for the reproduced concurrent-writer race: two
    callers racing to update the same source must not corrupt the stored
    record, and must not silently lose one caller's update.

    File-backed, not in-memory: StaticPool would hand every thread the same
    DBAPI connection, which can't hold concurrent transactions at all."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-source-health-lock-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.addCleanup(shared_state_store(self._tmpdir).close)

    def test_concurrent_record_and_check_never_corrupts_the_record(self) -> None:
        errors = []

        def worker(i: int) -> None:
            try:
                source_health.record_and_check("Hammered Source", i % 2 == 0)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertIn("days", _stored("Hammered Source"))

    def test_concurrent_calls_on_distinct_days_lose_no_updates(self) -> None:
        """Simulates several worker threads each recording a DIFFERENT
        day's result for the same source at once (e.g. a backfill or
        several pipeline runs racing) — every distinct day's entry must
        survive, none silently dropped by a lost-update race."""
        dates = [f"2026-02-{i:02d}" for i in range(1, 11)]

        def worker(date: str) -> None:
            source_health.record_and_check("Backfilled Source", True, date=date)

        threads = [threading.Thread(target=worker, args=(d,)) for d in dates]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        recorded_dates = {d["date"] for d in _stored("Backfilled Source")["days"]}
        self.assertEqual(recorded_dates, set(dates))


class MultiProcessConcurrencySafetyTest(unittest.TestCase):
    """The gap ConcurrencySafetyTest's own tests don't cover: they only spawn
    threads within one process. The race this module's locking actually
    targets is two separate *processes* (several pipelines/market_picks_pipeline.py
    workers, or several backend API workers) contending for the same source's
    record, which a plain threading.Lock cannot prevent at all. This spawns
    real OS processes."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-source-health-mp-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.addCleanup(shared_state_store(self._tmpdir).close)

    def test_concurrent_processes_on_distinct_days_lose_no_updates(self) -> None:
        ctx = multiprocessing.get_context("fork")
        dates = [f"2026-05-{i:02d}" for i in range(1, 9)]
        procs = [
            ctx.Process(target=_mp_record_and_check, args=(self._tmpdir, "MP Source", d, True))
            for d in dates
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
            self.assertEqual(p.exitcode, 0)

        recorded_dates = {d["date"] for d in _stored("MP Source")["days"]}
        self.assertEqual(recorded_dates, set(dates))


if __name__ == "__main__":
    unittest.main()
