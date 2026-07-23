"""Direct unit tests for market_picks_pipeline.py's pure scoring/dedup helpers.

These previously had zero test coverage — tests/test_market_picks_sources.py
only covers the source registry and insider-trade article formatting, not the
money-adjacent consolidation/scoring logic that decides what actually ranks.
"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import market_picks_pipeline
from market_picks_pipeline import (
    MarketPicksPipeline,
    _build_ranking_reasons,
    _compute_confidence,
    _effective_signal,
    _parse_targets_from_sources,
    _prune_extract_cache,
    _reason_strength,
    _title_words,
    _trade_levels,
)


def _source(**overrides) -> dict:
    base = {
        "name": "Test Brokerage", "source_type": "brokerage", "direction": "BUY",
        "syndicated": False, "credibility": 0.9, "article_age_days": 0, "reason": "",
        "story_cluster": "s0",
    }
    base.update(overrides)
    return base


class TradeLevelsTest(unittest.TestCase):
    def test_missing_price_returns_all_none(self) -> None:
        self.assertEqual(_trade_levels({}, 0.5), (None, None, None))

    def test_bullish_signal_gives_higher_upside_target(self) -> None:
        entry_lo, target_lo, _ = _trade_levels({"current_price": 100}, -1.0)
        entry_hi, target_hi, _ = _trade_levels({"current_price": 100}, 1.0)
        self.assertGreater(target_hi, target_lo)

    def test_strong_buy_signal_discounts_entry(self) -> None:
        entry, _, _ = _trade_levels({"current_price": 100}, 0.5)
        self.assertEqual(entry, 99)  # 1% discount

    def test_weak_signal_entry_at_market(self) -> None:
        entry, _, _ = _trade_levels({"current_price": 100}, 0.0)
        self.assertEqual(entry, 100)

    def test_stop_loss_clamped_between_7_and_15_pct(self) -> None:
        # Very wide 52w range should still clamp the stop-loss discount to 15%.
        _, _, stop = _trade_levels({"current_price": 100, "52w_high": 500, "52w_low": 10}, 0.0)
        self.assertEqual(stop, 85)

    def test_missing_52w_range_uses_default_10_pct_stop(self) -> None:
        _, _, stop = _trade_levels({"current_price": 100}, 0.0)
        self.assertEqual(stop, 90)


class TitleWordsTest(unittest.TestCase):
    def test_strips_stopwords_and_short_tokens(self) -> None:
        words = _title_words("TCS is the top pick for Q4 2026 results")
        self.assertNotIn("is", words)
        self.assertNotIn("the", words)
        self.assertNotIn("for", words)
        self.assertIn("results", words)

    def test_case_and_punctuation_insensitive(self) -> None:
        a = _title_words("Reliance Industries: Strong Buy!")
        b = _title_words("reliance industries strong buy")
        self.assertEqual(a, b)


class ReasonStrengthTest(unittest.TestCase):
    def test_bare_mention_is_zero(self) -> None:
        self.assertEqual(_reason_strength("Also discussed TCS today"), 0.0)

    def test_target_price_upgrade_and_buy_maxes_out(self) -> None:
        strength = _reason_strength("Upgraded to Buy, target price raised to 3200")
        self.assertEqual(strength, 1.0)

    def test_only_buy_keyword_gives_partial_strength(self) -> None:
        strength = _reason_strength("Analysts recommend accumulate")
        self.assertAlmostEqual(strength, 1 / 5)


class ParseTargetsFromSourcesTest(unittest.TestCase):
    def test_no_target_returns_none(self) -> None:
        self.assertIsNone(_parse_targets_from_sources([_source(reason="Strong buy call")]))

    def test_extracts_credibility_weighted_average(self) -> None:
        sources = [
            _source(reason="target 1000", credibility=1.0),
            _source(reason="target 2000", credibility=1.0),
        ]
        self.assertEqual(_parse_targets_from_sources(sources), 1500)

    def test_higher_credibility_source_dominates_average(self) -> None:
        sources = [
            _source(reason="target 1000", credibility=0.9),
            _source(reason="target 2000", credibility=0.1),
        ]
        result = _parse_targets_from_sources(sources)
        self.assertLess(result, 1500)

    def test_handles_rupee_symbol_and_commas(self) -> None:
        sources = [_source(reason="TP ₹3,250", credibility=1.0)]
        self.assertEqual(_parse_targets_from_sources(sources), 3250)


class EffectiveSignalTest(unittest.TestCase):
    def test_no_sources_is_zero(self) -> None:
        self.assertEqual(_effective_signal([]), 0.0)

    def test_brokerage_outweighs_news(self) -> None:
        brokerage = _effective_signal([_source(source_type="brokerage", credibility=1.0)])
        news = _effective_signal([_source(source_type="news", credibility=1.0)])
        self.assertGreater(brokerage, news)

    def test_syndicated_source_down_weighted(self) -> None:
        original = _effective_signal([_source(syndicated=False, credibility=1.0)])
        synd = _effective_signal([_source(syndicated=True, credibility=1.0)])
        self.assertGreater(original, synd)

    def test_sell_direction_flips_sign(self) -> None:
        self.assertLess(_effective_signal([_source(direction="SELL", credibility=1.0)]), 0)

    def test_neutral_direction_contributes_nothing(self) -> None:
        self.assertEqual(_effective_signal([_source(direction="NEUTRAL", credibility=1.0)]), 0.0)

    def test_story_cluster_keeps_only_dominant_member(self) -> None:
        # Two sources reporting the exact same wire story shouldn't double-count.
        clustered = _effective_signal([
            _source(story_cluster="s0", credibility=1.0),
            _source(story_cluster="s0", credibility=1.0),
        ])
        single = _effective_signal([_source(story_cluster="s0", credibility=1.0)])
        self.assertEqual(clustered, single)


class ComputeConfidenceTest(unittest.TestCase):
    def test_bullish_signal_and_strong_consensus_scores_high(self) -> None:
        sources = [_source(credibility=1.0, article_age_days=0) for _ in range(3)]
        score = _compute_confidence(1.0, sources, max_effective_signal=10.0, stock_info={})
        self.assertGreater(score, 70)

    def test_no_sources_still_returns_a_bounded_score(self) -> None:
        score = _compute_confidence(0.0, [], max_effective_signal=1.0, stock_info={})
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)

    def test_older_articles_score_lower_than_fresh_ones(self) -> None:
        fresh = _compute_confidence(0.0, [_source(article_age_days=0, credibility=1.0)], 1.0, {})
        old = _compute_confidence(0.0, [_source(article_age_days=10, credibility=1.0)], 1.0, {})
        self.assertGreater(fresh, old)

    def test_score_is_always_bounded_0_to_100(self) -> None:
        sources = [_source(credibility=1.0, article_age_days=0) for _ in range(20)]
        score = _compute_confidence(1.0, sources, max_effective_signal=1.0, stock_info={})
        self.assertLessEqual(score, 100.0)


class BuildRankingReasonsTest(unittest.TestCase):
    def test_three_or_more_brokerage_buys_gives_count_reason(self) -> None:
        sources = [_source(name=f"Broker{i}") for i in range(3)]
        reasons = _build_ranking_reasons(sources, 0.0, "HOLD", 0.0, 50.0, None)
        self.assertTrue(any("brokerage BUY calls" in r for r in reasons))

    def test_strong_signal_score_is_called_out(self) -> None:
        reasons = _build_ranking_reasons([], 0.6, "HOLD", 0.0, 50.0, None)
        self.assertTrue(any("quant signal" in r for r in reasons))

    def test_high_upside_target_is_called_out(self) -> None:
        reasons = _build_ranking_reasons([], 0.0, "HOLD", 0.0, 50.0, upside_pct=15.0)
        self.assertTrue(any("upside" in r for r in reasons))

    def test_returns_at_most_four_reasons(self) -> None:
        sources = [_source(name=f"Broker{i}", article_age_days=0, credibility=0.99) for i in range(5)]
        reasons = _build_ranking_reasons(sources, 0.9, "BUY", 0.0, 90.0, upside_pct=20.0)
        self.assertLessEqual(len(reasons), 4)


class PhaseExtractGuardrailTest(unittest.TestCase):
    """The extraction LLM reads arbitrary third-party article text — a qualifying
    pick must cite a real reason per the prompt's own rules, so a pick with no
    reason (a hallucination, or the model getting steered off-task by injected
    article content) must be dropped rather than passed through to scoring.
    """

    def _raw_sources(self, title: str = "Reliance gets a Buy call") -> dict:
        return {
            "Test Source": {
                "articles": [{
                    "title": title, "summary": "summary", "url": "https://example.com/a",
                    "published_at": None,
                }],
            },
        }

    def _run_extract(self, llm_response: dict) -> list[dict]:
        pipeline = MarketPicksPipeline()
        with patch("market_picks_pipeline._llm_call", return_value=json.dumps(llm_response)), \
             patch("market_picks_pipeline._extraction_cache_get", return_value=None), \
             patch("market_picks_pipeline._extraction_cache_set"):
            return pipeline._phase_extract(self._raw_sources(), emit=lambda p: None)

    def test_pick_with_empty_reason_is_dropped(self) -> None:
        picks = self._run_extract({"picks": [
            {"company": "Reliance Industries", "ticker": "RELIANCE", "reason": "", "direction": "BUY"},
        ]})
        self.assertEqual(picks, [])

    def test_pick_with_whitespace_only_reason_is_dropped(self) -> None:
        picks = self._run_extract({"picks": [
            {"company": "Reliance Industries", "ticker": "RELIANCE", "reason": "   ", "direction": "BUY"},
        ]})
        self.assertEqual(picks, [])

    def test_valid_pick_with_reason_passes_through(self) -> None:
        picks = self._run_extract({"picks": [
            {"company": "Reliance Industries", "ticker": "RELIANCE",
             "reason": "Morgan Stanley Buy, target 3200", "direction": "BUY"},
        ]})
        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["ticker"], "RELIANCE")
        self.assertEqual(picks[0]["reason"], "Morgan Stanley Buy, target 3200")

    def test_oversized_fields_are_truncated_not_passed_through_raw(self) -> None:
        picks = self._run_extract({"picks": [
            {"company": "X" * 500, "ticker": "Y" * 100, "reason": "Z" * 5000, "direction": "BUY"},
        ]})
        self.assertEqual(len(picks), 1)
        self.assertLessEqual(len(picks[0]["company"]), 120)
        self.assertLessEqual(len(picks[0]["ticker"]), 15)
        self.assertLessEqual(len(picks[0]["reason"]), 300)

    def test_no_qualifying_picks_returns_empty_list(self) -> None:
        picks = self._run_extract({"picks": []})
        self.assertEqual(picks, [])

    def test_llm_failure_returns_empty_list_not_raise(self) -> None:
        pipeline = MarketPicksPipeline()
        with patch("market_picks_pipeline._llm_call", side_effect=RuntimeError("boom")), \
             patch("market_picks_pipeline._extraction_cache_get", return_value=None), \
             patch("market_picks_pipeline._extraction_cache_set"):
            picks = pipeline._phase_extract(self._raw_sources(), emit=lambda p: None)
        self.assertEqual(picks, [])


class PruneExtractCacheTest(unittest.TestCase):
    """output/_extract_cache/ is content-hash-keyed, so every distinct batch of
    articles a source ever serves creates a new file — _extraction_cache_get()
    treats an expired one as a cache miss, but nothing removed it from disk.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-extract-cache-prune-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        patch.object(market_picks_pipeline, "_EXTRACT_CACHE_DIR", Path(self._tmpdir)).start()
        self.addCleanup(patch.stopall)

    def test_removes_only_files_past_the_ttl(self) -> None:
        cache_dir = market_picks_pipeline._EXTRACT_CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        stale = cache_dir / "stale.json"
        fresh = cache_dir / "fresh.json"
        stale.write_text("{}")
        fresh.write_text("{}")

        old_mtime = time.time() - market_picks_pipeline._EXTRACT_CACHE_TTL - 3600
        import os
        os.utime(stale, (old_mtime, old_mtime))

        removed = _prune_extract_cache()

        self.assertEqual(removed, 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_missing_directory_returns_zero_not_raise(self) -> None:
        self.assertEqual(_prune_extract_cache(), 0)


if __name__ == "__main__":
    unittest.main()
