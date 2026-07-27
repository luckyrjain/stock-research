import threading
import time
import unittest
from unittest.mock import patch

import signals.engine as engine
from signals.features import extract_features
from signals.engine import _DEFAULT_WEIGHTS, _log_unmatched_sector_once, _weights_for_sector, run_signal_engine
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

    def test_wrong_type_ratios_degrades_to_empty_dict_not_a_crash(self) -> None:
        # Regression test for an adversarial-review finding: schemas.py's
        # own contract check only validates FIELD PRESENCE, not type — a
        # scraped/cached "ratios" value present but of the wrong type (a
        # list instead of a dict, e.g. from schema drift or a malformed
        # cache entry) used to pass through normalize() unchanged and reach
        # every signal module's own ratios.get(...) call, which raised
        # AttributeError ('list' object has no attribute 'get') and crashed
        # the entire signal engine — and, since api.py's call site has no
        # local try/except around run_signal_engine(), the whole
        # /api/analyse/{symbol} request, not just this one degraded signal.
        all_data = {"research": {"ratios": ["not", "a", "dict"]}}
        features = extract_features(all_data)
        self.assertEqual(features["ratios"], {})

    def test_wrong_type_filings_list_degrades_to_empty_list_not_a_crash(self) -> None:
        # Same failure mode as the ratios case above, for the filings list.
        all_data = {"filings": {"filings": {"not": "a list"}}}
        features = extract_features(all_data)
        self.assertEqual(features["filings"], [])

    def test_wrong_type_research_container_degrades_to_empty_dict_not_a_crash(self) -> None:
        # Regression test for a gap in the two tests above, caught by an
        # independent adversarial review: the isinstance guards only covered
        # the *nested* ratios/filings fields, not the outer task containers
        # (all_data["research"]/["filings"]/["stock_info"]) those .get()
        # calls chain through. A malformed cache entry storing the whole
        # "research" task as a list, not a dict, previously raised
        # AttributeError on research.get("ratios", {}) before the nested
        # isinstance check ever ran.
        all_data = {"research": ["not", "a", "dict"]}
        features = extract_features(all_data)
        self.assertEqual(features["ratios"], {})

    def test_wrong_type_filings_container_degrades_to_empty_list_not_a_crash(self) -> None:
        all_data = {"filings": ["not", "a", "dict"]}
        features = extract_features(all_data)
        self.assertEqual(features["filings"], [])

    def test_wrong_type_stock_info_container_degrades_gracefully_not_a_crash(self) -> None:
        all_data = {"stock_info": ["not", "a", "dict"]}
        features = extract_features(all_data)
        self.assertIsNone(features["price"])
        self.assertIsNone(features["volume"])


class WeightsForSectorTest(unittest.TestCase):
    def test_unknown_or_missing_sector_uses_default_weights(self) -> None:
        self.assertEqual(_weights_for_sector(None), _DEFAULT_WEIGHTS)
        self.assertEqual(_weights_for_sector("Some Sector Nobody Heard Of"), _DEFAULT_WEIGHTS)

    def test_rate_sensitive_sector_tilts_toward_valuation_and_macro(self) -> None:
        for sector in ("Financial Services", "Real Estate", "Utilities"):
            with self.subTest(sector=sector):
                weights = _weights_for_sector(sector)
                self.assertEqual(weights["valuation"], 0.45)
                self.assertEqual(weights["growth"], 0.3)
                self.assertEqual(weights["macro"], 0.20)
                # Untouched weights carry over from the default unchanged.
                self.assertEqual(weights["volume"], _DEFAULT_WEIGHTS["volume"])
                self.assertEqual(weights["filings"], _DEFAULT_WEIGHTS["filings"])
                self.assertEqual(weights["technical"], _DEFAULT_WEIGHTS["technical"])

    def test_growth_sector_tilts_toward_growth_and_away_from_macro(self) -> None:
        for sector in ("Technology", "Communication Services", "Healthcare"):
            with self.subTest(sector=sector):
                weights = _weights_for_sector(sector)
                self.assertEqual(weights["growth"], 0.45)
                self.assertEqual(weights["macro"], 0.1)
                # valuation is deliberately left untouched for this group —
                # only growth/macro move, so their +0.05/-0.05 deltas net to
                # zero without needing a third lever.
                self.assertEqual(weights["valuation"], _DEFAULT_WEIGHTS["valuation"])

    def test_cyclical_sector_tilts_toward_technical_and_volume(self) -> None:
        for sector in ("Basic Materials", "Energy", "Industrials", "Consumer Cyclical"):
            with self.subTest(sector=sector):
                weights = _weights_for_sector(sector)
                self.assertEqual(weights["technical"], 0.3)
                self.assertEqual(weights["volume"], 0.3)
                # valuation/growth are weighted down to offset technical/volume
                # being weighted up, so the group's weight sum stays at the
                # 1.55 baseline rather than inflating.
                self.assertEqual(weights["valuation"], 0.3)
                self.assertEqual(weights["growth"], 0.3)
                # Untouched weights carry over from the default unchanged.
                self.assertEqual(weights["filings"], _DEFAULT_WEIGHTS["filings"])
                self.assertEqual(weights["macro"], _DEFAULT_WEIGHTS["macro"])

    def test_every_sector_group_weight_sum_matches_default_baseline(self) -> None:
        # Regression test: rate_sensitive and growth previously summed to
        # 1.60 and 1.50 respectively instead of the shared 1.55 baseline —
        # only cyclical (which happened to be correct) had this invariant
        # tested, so the other two shipped silently miscalibrated. Every
        # override group must be covered here, not just one representative.
        baseline = sum(_DEFAULT_WEIGHTS.values())
        for sector in ("Financial Services", "Technology", "Energy"):
            with self.subTest(sector=sector):
                weights = _weights_for_sector(sector)
                self.assertAlmostEqual(sum(weights.values()), baseline, places=6)

    def test_never_mutates_the_shared_default_dict(self) -> None:
        _weights_for_sector("Financial Services")
        self.assertEqual(_DEFAULT_WEIGHTS["valuation"], 0.4)


class RunSignalEngineTest(unittest.TestCase):
    def _run(self, volume, valuation, growth, filings, technical=None, macro=None, symbol="TCS", sector=None):
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
             patch("signals.engine.extract_features", return_value={"sector": sector}):
            return run_signal_engine(symbol, {})

    def test_weighted_score_is_computed_correctly(self) -> None:
        # weights: valuation 0.4, volume 0.2, growth 0.4, filings 0.2,
        # technical 0.2, macro 0.15 — these sum to 1.55, not 1.0, so the raw
        # weighted sum before clamping is not bounded to [-1, 1] even
        # though each individual signal score is. The engine clamps the
        # final result to the documented -1..1 contract (see the comment
        # in signals/engine.py), so a weighted sum of 1.55 here reads back
        # as exactly 1.0.
        result = self._run(
            volume=_sig(1.0), valuation=_sig(1.0), growth=_sig(1.0), filings=_sig(1.0),
            technical=_sig(1.0), macro=_sig(1.0),
        )
        self.assertAlmostEqual(result.final_score, 1.0, places=2)

        result2 = self._run(volume=_sig(0.0), valuation=_sig(1.0), growth=_sig(0.0), filings=_sig(0.0))
        self.assertAlmostEqual(result2.final_score, 0.4, places=2)

    def test_final_score_is_clamped_to_documented_range(self) -> None:
        # Regression test for an adversarial-review finding: with real
        # per-signal score ceilings/floors (not the +/-1.0 extremes the
        # test above uses to prove the raw sum can exceed 1.55), the
        # engine's own achievable max/min is asymmetric (~1.18 / ~-0.89 in
        # the default weight configuration) — final_score must never
        # silently exceed the -1..1 contract every consumer (frontend,
        # GET /api/v1, docs/output-schema.md) treats as a hard bound.
        result = self._run(
            volume=_sig(1.0), valuation=_sig(0.6), growth=_sig(0.9), filings=_sig(0.95),
            technical=_sig(0.6), macro=_sig(0.44),
        )
        self.assertLessEqual(result.final_score, 1.0)

        result2 = self._run(
            volume=_sig(-0.5), valuation=_sig(-1.0), growth=_sig(-0.5), filings=_sig(-0.15),
            technical=_sig(-0.4), macro=_sig(-0.52),
        )
        self.assertGreaterEqual(result2.final_score, -1.0)

    def test_negative_clamp_actually_engages(self) -> None:
        # test_final_score_is_clamped_to_documented_range's negative case
        # above uses each signal's own real achievable floor, which sums to
        # only ~-0.888 with the default weights (the engine's own asymmetry,
        # documented in signals/engine.py) -- that assertion holds whether
        # or not the clamp exists, so it can't actually prove the clamp
        # engages on the negative side. This uses synthetic -1.0 scores for
        # every signal (mirroring test_weighted_score_is_computed_correctly's
        # +1.0 case above, which DOES exceed the -1..1 range pre-clamp) to
        # force a raw weighted sum of -1.55, and asserts it reads back as
        # exactly -1.0 -- if the clamp were removed or its bound flipped,
        # this fails.
        result = self._run(
            volume=_sig(-1.0), valuation=_sig(-1.0), growth=_sig(-1.0), filings=_sig(-1.0),
            technical=_sig(-1.0), macro=_sig(-1.0),
        )
        self.assertAlmostEqual(result.final_score, -1.0, places=2)

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

    def test_sector_tilt_changes_final_score(self) -> None:
        # Same underlying signal scores, different sector -> different
        # weights -> different final_score. valuation=1.0, growth=0.0: a
        # rate-sensitive sector weights valuation higher (0.45 vs 0.4), so
        # its final_score should be strictly higher than the default.
        common = dict(volume=_sig(0.0), valuation=_sig(1.0), growth=_sig(0.0), filings=_sig(0.0))
        default_result = self._run(**common, sector=None)
        rate_sensitive_result = self._run(**common, sector="Financial Services")
        self.assertGreater(rate_sensitive_result.final_score, default_result.final_score)

    def test_unrecognized_sector_falls_back_to_default_weighted_score(self) -> None:
        common = dict(volume=_sig(0.0), valuation=_sig(1.0), growth=_sig(0.0), filings=_sig(0.0))
        default_result = self._run(**common, sector=None)
        unknown_result = self._run(**common, sector="Made Up Sector")
        self.assertAlmostEqual(unknown_result.final_score, default_result.final_score, places=6)

    def test_result_carries_symbol_and_all_signals(self) -> None:
        v, val, g, f, t, m = _sig(0.1), _sig(0.2), _sig(0.3), _sig(0.4), _sig(0.5), _sig(0.6)
        result = self._run(volume=v, valuation=val, growth=g, filings=f, technical=t, macro=m, symbol="INFY")
        self.assertEqual(result.symbol, "INFY")
        self.assertEqual(
            result.signals,
            {"volume": v, "valuation": val, "growth": g, "filings": f, "technical": t, "macro": m},
        )


class _PausingSet(set):
    """A set whose .add() pauses (via an Event handshake) after being called
    but before the mutation actually lands — used to force a deterministic
    interleaving of two threads racing on _log_unmatched_sector_once()'s
    check-then-act sequence. A plain threading.Barrier-plus-sleep approach
    doesn't reliably reproduce this race: a membership check plus a single
    set.add() is pure Python with no I/O, so CPython's GIL essentially never
    yields mid-sequence in practice, and a statistical race test can pass
    against genuinely unsynchronized code run after run (confirmed directly
    against this codebase's own reverted, pre-fix version: 15/15 runs of a
    50-thread barrier-based version of this test passed). This class inserts
    an explicit synchronization point exactly between "checked" and
    "mutated" so a second thread's own check can be deterministically landed
    inside that window instead of relying on scheduler luck."""

    def __init__(self):
        super().__init__()
        self.reached_add = threading.Event()
        self.release_add = threading.Event()

    def add(self, item):
        self.reached_add.set()
        self.release_add.wait(timeout=2)
        super().add(item)


class LogUnmatchedSectorOnceConcurrencyTest(unittest.TestCase):
    """Regression test for an adversarial-review finding: _log_unmatched_
    sector_once()'s check-then-act sequence (membership check, then .add(),
    then log_event()) had no lock, while run_signal_engine() runs inside
    ThreadPoolExecutor workers from several concurrent call sites in this
    codebase. Two threads first encountering the same never-before-seen
    sector at the same moment could both pass the membership check before
    either added it, producing a duplicate warning log instead of the
    documented one-time-per-process behavior."""

    def setUp(self) -> None:
        engine._unmatched_sectors_logged.clear()
        self.addCleanup(engine._unmatched_sectors_logged.clear)

    def test_two_concurrent_callers_for_the_same_new_sector_log_exactly_once(self) -> None:
        pausing_set = _PausingSet()

        def _call():
            _log_unmatched_sector_once("Some Never-Before-Seen Sector")

        with patch("signals.engine._unmatched_sectors_logged", pausing_set), \
                patch("signals.engine.log_event") as mock_log:
            t1 = threading.Thread(target=_call)
            t1.start()
            # Wait until thread 1 has passed its own membership check and
            # called add() — add() itself is paused before the mutation
            # lands, so the item isn't actually in the set yet. Starting
            # thread 2's own check now lands it exactly in the pre-add
            # window this race depends on (in the unfixed version — with
            # the fix's lock in place, thread 2 instead blocks acquiring
            # the lock and never reaches its own check at this point).
            self.assertTrue(pausing_set.reached_add.wait(timeout=2))

            t2 = threading.Thread(target=_call)
            t2.start()
            time.sleep(0.1)  # let thread 2 reach its own check/add if unlocked
            pausing_set.release_add.set()

            t1.join(timeout=2)
            t2.join(timeout=2)

        self.assertEqual(mock_log.call_count, 1)


if __name__ == "__main__":
    unittest.main()
