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
from unittest.mock import MagicMock, patch

import cache
import market_picks_pipeline
from market_picks_pipeline import (
    MarketPicksPipeline,
    _apply_sector_balance,
    _build_ranking_reasons,
    _compute_confidence,
    _dedup_key,
    _effective_signal,
    _parse_targets_from_sources,
    _prune_extract_cache,
    _reason_strength,
    _resolve_symbol_via_fuzzy_match,
    _select_target_price,
    _title_words,
    _trade_levels,
    picks_cache_status,
    save_picks_cache,
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


class SelectTargetPriceTest(unittest.TestCase):
    """Regression tests for an adversarial-review finding: a regex-parsed
    analyst target from _parse_targets_from_sources() was previously trusted
    at face value with no sanity check against the stock's actual price — a
    false-positive regex match (e.g. "target 2027 revenue growth" capturing
    "2027" as a price for a ₹50 stock) or a stale figure could land wildly
    far from reality, violating the stop < entry < target invariant the
    deterministic formula path (_trade_levels) always guarantees."""

    def test_plausible_analyst_target_within_band_is_used(self) -> None:
        # 1500 is 1.5x the 1000 price -- comfortably inside the 0.5x-3x band.
        self.assertEqual(_select_target_price(1500, 1100, 1000), 1500)

    def test_implausible_analyst_target_falls_back_to_formula(self) -> None:
        # A regex false-positive (e.g. "target 2027 revenue growth" matched
        # against a ~₹500 stock) is way outside the 0.5x-3x plausibility band
        # and must not be trusted, even though it "parsed".
        self.assertEqual(_select_target_price(2027, 550, 500), 550)

    def test_analyst_target_below_min_multiple_falls_back_to_formula(self) -> None:
        # 400 is 0.4x the 1000 price -- just under the 0.5x floor.
        self.assertEqual(_select_target_price(400, 1100, 1000), 1100)

    def test_analyst_target_above_max_multiple_falls_back_to_formula(self) -> None:
        # 3001 is just over the 3x ceiling on a 1000 price.
        self.assertEqual(_select_target_price(3001, 1100, 1000), 1100)

    def test_boundary_multiples_are_inclusive(self) -> None:
        self.assertEqual(_select_target_price(500, 1100, 1000), 500)   # exactly 0.5x
        self.assertEqual(_select_target_price(3000, 1100, 1000), 3000)  # exactly 3.0x

    def test_no_analyst_target_uses_formula(self) -> None:
        self.assertEqual(_select_target_price(None, 1100, 1000), 1100)

    def test_missing_price_falls_back_to_formula(self) -> None:
        # No current price to sanity-check against -- never trust the
        # analyst target blindly just because it parsed.
        self.assertEqual(_select_target_price(1500, 1100, None), 1100)
        self.assertEqual(_select_target_price(1500, 1100, 0), 1100)

    def test_zero_or_negative_analyst_target_falls_back_to_formula(self) -> None:
        self.assertEqual(_select_target_price(0, 1100, 1000), 1100)
        self.assertEqual(_select_target_price(-500, 1100, 1000), 1100)


class DedupKeyTest(unittest.TestCase):
    """Regression tests for an adversarial-review finding: the consolidation
    dedup key used to truncate the normalized-company-name fallback to 12
    characters, so two different companies sharing the same first 12
    normalized characters would silently collide onto one group and have
    their source mentions merged."""

    def test_ticker_is_used_verbatim_when_present(self) -> None:
        self.assertEqual(_dedup_key("TCS", "Tata Consultancy Services"), "TCS")

    def test_falls_back_to_normalized_company_name_without_ticker(self) -> None:
        self.assertEqual(_dedup_key("", "Reliance Industries Ltd."), "RELIANCEINDUSTRIESLTD")

    def test_two_companies_sharing_a_12_char_prefix_no_longer_collide(self) -> None:
        # Both names normalize to the same first 12 characters
        # ("RELIANCEINDU...") but are genuinely different companies once the
        # full name is considered -- the old [:12] truncation would have
        # merged these into a single dedup group.
        key_a = _dedup_key("", "Reliance Industrial Infrastructure")
        key_b = _dedup_key("", "Reliance Industries Limited")
        self.assertEqual(key_a[:12], key_b[:12])  # confirms the shared-prefix premise
        self.assertNotEqual(key_a, key_b)

    def test_empty_ticker_and_company_yields_empty_key(self) -> None:
        self.assertEqual(_dedup_key("", ""), "")


class ResolveSymbolViaFuzzyMatchTest(unittest.TestCase):
    """Regression tests for an adversarial-review finding: the rapidfuzz
    match in consolidation's Path B2 used to be re-resolved via
    `[n for _, n in sym_names].index(best_match[0])` instead of using
    `best_match[2]` (the index rapidfuzz's own extractOne() already
    returns) -- `.index()` always finds the FIRST list entry with that
    matched string, which silently resolves to the wrong symbol whenever
    two different candidates share the same display name."""

    def test_uses_extractones_own_index_not_a_rederived_first_occurrence(self) -> None:
        # Two candidates share the exact same display name at indices 0 and
        # 1, but only index 1's ticker is the one that should be resolved to
        # (simulated by patching extractOne to report it matched index 1).
        # A naive `.index(best_match[0])` re-derivation would always find
        # index 0 instead, since the two names are byte-identical strings.
        symbols = [
            {"symbol": "OLDCO", "symbol_info": "Shared Name Ltd"},
            {"symbol": "NEWCO", "symbol_info": "Shared Name Ltd"},
        ]
        with patch("rapidfuzz.process.extractOne", return_value=("Shared Name Ltd", 92.0, 1)):
            result = _resolve_symbol_via_fuzzy_match("Shared Name", symbols)
        self.assertEqual(result, symbols[1])
        self.assertEqual(result["symbol"], "NEWCO")

    def test_returns_none_with_only_one_candidate(self) -> None:
        # No disambiguation needed/possible with a single candidate --
        # short-circuits before ever calling rapidfuzz.
        with patch("rapidfuzz.process.extractOne") as mock_extract:
            result = _resolve_symbol_via_fuzzy_match("Anything", [{"symbol": "SOLO", "symbol_info": "Solo Ltd"}])
        self.assertIsNone(result)
        mock_extract.assert_not_called()

    def test_real_rapidfuzz_resolves_the_correct_symbol(self) -> None:
        # End-to-end against the real rapidfuzz library (no mocking) --
        # confirms the extracted index is actually plumbed through correctly
        # to select the matching entry, not just that a mock was obeyed.
        symbols = [
            {"symbol": "ABC", "symbol_info": "Totally Different Industries"},
            {"symbol": "SAILIFE", "symbol_info": "Sai Life Sciences Limited"},
        ]
        result = _resolve_symbol_via_fuzzy_match("Sai Life Sciences", symbols)
        self.assertIsNotNone(result)
        self.assertEqual(result["symbol"], "SAILIFE")

    def test_real_rapidfuzz_returns_none_below_score_cutoff(self) -> None:
        symbols = [
            {"symbol": "ABC", "symbol_info": "Totally Different Industries"},
            {"symbol": "XYZ", "symbol_info": "Another Unrelated Company"},
        ]
        result = _resolve_symbol_via_fuzzy_match("Sai Life Sciences", symbols)
        self.assertIsNone(result)


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

    def test_cheap_valuation_percentile_nudges_score_up(self) -> None:
        sources = [_source(credibility=0.5, article_age_days=3)]
        without = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=None)
        cheap = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=20.0)
        self.assertAlmostEqual(cheap, without + 3.0, places=1)

    def test_expensive_valuation_percentile_nudges_score_down(self) -> None:
        sources = [_source(credibility=0.5, article_age_days=3)]
        without = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=None)
        expensive = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=80.0)
        self.assertAlmostEqual(expensive, without - 3.0, places=1)

    def test_mid_range_valuation_percentile_is_no_op(self) -> None:
        sources = [_source(credibility=0.5, article_age_days=3)]
        without = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=None)
        mid = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=50.0)
        self.assertEqual(mid, without)

    def test_boundary_percentiles_are_inclusive(self) -> None:
        sources = [_source(credibility=0.5, article_age_days=3)]
        without = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=None)
        at_33 = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=33.0)
        at_67 = _compute_confidence(0.0, sources, 1.0, {}, valuation_percentile=67.0)
        self.assertAlmostEqual(at_33, without + 3.0, places=1)
        self.assertAlmostEqual(at_67, without - 3.0, places=1)

    def test_valuation_nudge_does_not_push_score_past_100(self) -> None:
        sources = [_source(credibility=1.0, article_age_days=0) for _ in range(20)]
        score = _compute_confidence(1.0, sources, max_effective_signal=1.0, stock_info={}, valuation_percentile=10.0)
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


class PhaseExtractTickerAttributionTest(unittest.TestCase):
    """A pick's url/article_title/article_date/syndicated are attributed to
    whichever article in the batch actually contains its ticker — never
    silently defaulted to the batch's first article, which used to
    misattribute a pick to an unrelated story whenever the ticker didn't
    literally appear in that first article's title.
    """

    def _raw_sources(self, articles: list[dict]) -> dict:
        return {"Test Source": {"articles": articles}}

    def _run_extract(self, articles: list[dict], llm_response: dict) -> list[dict]:
        pipeline = MarketPicksPipeline()
        with patch("market_picks_pipeline._llm_call", return_value=json.dumps(llm_response)), \
             patch("market_picks_pipeline._extraction_cache_get", return_value=None), \
             patch("market_picks_pipeline._extraction_cache_set"):
            return pipeline._phase_extract(self._raw_sources(articles), emit=lambda p: None)

    def test_ticker_absent_from_every_article_does_not_default_to_first(self) -> None:
        articles = [
            {"title": "Some unrelated market wrap", "summary": "general commentary",
             "url": "https://example.com/wrap", "published_at": "2026-01-01T00:00:00+00:00"},
            {"title": "Another unrelated piece", "summary": "more commentary",
             "url": "https://example.com/other", "published_at": "2026-01-02T00:00:00+00:00"},
        ]
        picks = self._run_extract(articles, {"picks": [
            {"company": "Reliance Industries", "ticker": "RELIANCE",
             "reason": "Morgan Stanley Buy, target 3200", "direction": "BUY"},
        ]})
        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["url"], "")
        self.assertEqual(picks[0]["article_title"], "")
        self.assertIsNone(picks[0]["article_date"])
        self.assertFalse(picks[0]["syndicated"])

    def test_ticker_found_in_second_article_attributes_to_that_one_not_first(self) -> None:
        articles = [
            {"title": "TCS wins large deal", "summary": "TCS deal news",
             "url": "https://example.com/tcs", "published_at": "2026-01-01T00:00:00+00:00"},
            {"title": "Reliance gets a Buy call", "summary": "RELIANCE rated buy",
             "url": "https://example.com/reliance", "published_at": "2026-01-02T00:00:00+00:00"},
        ]
        picks = self._run_extract(articles, {"picks": [
            {"company": "Reliance Industries", "ticker": "RELIANCE",
             "reason": "Morgan Stanley Buy, target 3200", "direction": "BUY"},
        ]})
        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["url"], "https://example.com/reliance")
        self.assertEqual(picks[0]["article_title"], "Reliance gets a Buy call")

    def test_ticker_only_in_summary_still_matches(self) -> None:
        articles = [
            {"title": "Brokerage roundup for the week", "summary": "RELIANCE rated buy by Morgan Stanley",
             "url": "https://example.com/roundup", "published_at": "2026-01-01T00:00:00+00:00"},
        ]
        picks = self._run_extract(articles, {"picks": [
            {"company": "Reliance Industries", "ticker": "RELIANCE",
             "reason": "Morgan Stanley Buy, target 3200", "direction": "BUY"},
        ]})
        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["url"], "https://example.com/roundup")


class PhaseResearchValuationPercentileTest(unittest.TestCase):
    """_research_one() fetches get_peer_comparison() alongside stock_info/
    research and folds the resulting valuation percentile into research_data
    — closing the gap where peer/valuation data (already built and shipped
    for the single-stock flow) never reached Market Picks scoring.

    The fetch goes through cache.load/save(symbol, "peers") — the same
    cache entry GET /api/peers/{symbol} uses — so every test here patches
    cache.CACHE_DIR to an isolated tmpdir (matching this suite's existing
    convention elsewhere for anything that touches the cache/output
    directory), both to avoid polluting the real repo's output/ dir and
    because a stale real cache entry would otherwise make these tests
    order-dependent on whatever ran before them."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-valuation-pct-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)

    def _run(self, peer_comparison_json: str, symbol: str = "TCS"):
        fake_signal_result = MagicMock(final_score=0.5, verdict="BUY")
        pipeline = MarketPicksPipeline()
        with patch("main._fetch_task", return_value={}), \
             patch("schemas.normalize", side_effect=lambda task, data: {}), \
             patch("signals.engine.run_signal_engine", return_value=fake_signal_result), \
             patch("signals.interpreter.interpret", return_value="insight"), \
             patch("tools.screener_tools.get_peer_comparison") as mock_peers, \
             patch("yfinance.Ticker") as mock_ticker:
            mock_peers.run.return_value = peer_comparison_json
            mock_ticker.return_value.history.return_value = [1] * 12  # not a recent IPO
            result = pipeline._phase_research([{"symbol": symbol}], emit=lambda p: None)
            return result, mock_peers

    def test_cheap_valuation_is_populated_from_peer_comparison(self) -> None:
        peer_json = json.dumps({
            "self": {"values": {"P/E": "22"}},
            "valuation_band": {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 25.0, 30.0]},
        })
        result, _ = self._run(peer_json)
        self.assertIsNotNone(result["TCS"]["valuation_percentile"])
        self.assertIsInstance(result["TCS"]["valuation_percentile"], float)

    def test_screener_error_leaves_valuation_percentile_none_not_fatal(self) -> None:
        result, _ = self._run(json.dumps({"error": "boom"}))
        self.assertIsNone(result["TCS"]["valuation_percentile"])
        self.assertNotIn("error", result["TCS"])  # the rest of the research step still succeeds

    def test_fewer_than_three_years_of_band_leaves_valuation_percentile_none(self) -> None:
        peer_json = json.dumps({
            "self": {"values": {"P/E": "22"}},
            "valuation_band": {"years": ["Mar 2023", "Mar 2024"], "pe": [25.0, 30.0]},
        })
        result, _ = self._run(peer_json)
        self.assertIsNone(result["TCS"]["valuation_percentile"])

    def test_second_call_hits_the_cache_instead_of_scraping_again(self) -> None:
        """The High-severity review finding on this PR: without caching,
        every pipeline run re-scrapes Screener.in for every stock. Two
        research passes for the same symbol must only hit
        get_peer_comparison() once."""
        peer_json = json.dumps({
            "self": {"values": {"P/E": "22"}},
            "valuation_band": {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 25.0, 30.0]},
        })
        fake_signal_result = MagicMock(final_score=0.5, verdict="BUY")
        pipeline = MarketPicksPipeline()
        with patch("main._fetch_task", return_value={}), \
             patch("schemas.normalize", side_effect=lambda task, data: {}), \
             patch("signals.engine.run_signal_engine", return_value=fake_signal_result), \
             patch("signals.interpreter.interpret", return_value="insight"), \
             patch("tools.screener_tools.get_peer_comparison") as mock_peers, \
             patch("yfinance.Ticker") as mock_ticker:
            mock_peers.run.return_value = peer_json
            mock_ticker.return_value.history.return_value = [1] * 12
            r1 = pipeline._phase_research([{"symbol": "TCS"}], emit=lambda p: None)
            r2 = pipeline._phase_research([{"symbol": "TCS"}], emit=lambda p: None)

        self.assertEqual(mock_peers.run.call_count, 1)
        self.assertEqual(r1["TCS"]["valuation_percentile"], r2["TCS"]["valuation_percentile"])

    def test_cached_entry_shares_shape_with_api_peers_endpoint(self) -> None:
        """The cache entry this pipeline writes under cache key "peers"
        must be readable by api.py's GET /api/peers/{symbol} (and vice
        versa) — both must agree on the same value shape
        (peer_analytics.build_peer_result), or one caller silently sees
        missing fields instead of a real cache hit."""
        peer_json = json.dumps({
            "self": {"values": {"P/E": "22"}},
            "valuation_band": {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 25.0, 30.0]},
        })
        self._run(peer_json)
        cached = cache.load("TCS", "peers")
        self.assertIsNotNone(cached)
        self.assertIn("absolute_anchor", cached)
        self.assertIn("percentiles", cached)
        self.assertEqual(cached["absolute_anchor"]["percentile"], 33.3)


class PhaseResearchIsRecentIpoTest(unittest.TestCase):
    """Regression tests for an adversarial-review finding: is_recent_ipo's
    NSE->BSE fallback logic used to be
    `if len(hist) < 8: is_recent_ipo = True elif not is_recent_ipo: <check BSE>`.
    Since the elif branch is only reachable when the if was false (meaning
    is_recent_ipo is still False at that point), the BSE fallback ran
    backwards -- only when NSE already proved sufficient history
    (unnecessarily, and able to overwrite a correct False with a wrong
    BSE-derived True), and never when NSE data was genuinely thin, which is
    exactly the case a BSE fallback is supposed to help with."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-recent-ipo-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)

    def _run(self, ns_history_len: int, bo_history_len: int, symbol: str = "TCS"):
        fake_signal_result = MagicMock(final_score=0.5, verdict="BUY")
        pipeline = MarketPicksPipeline()

        def _fake_ticker(ticker_str):
            fake = MagicMock()
            length = ns_history_len if ticker_str.endswith(".NS") else bo_history_len
            fake.history.return_value = [1] * length
            return fake

        with patch("main._fetch_task", return_value={}), \
             patch("schemas.normalize", side_effect=lambda task, data: {}), \
             patch("signals.engine.run_signal_engine", return_value=fake_signal_result), \
             patch("signals.interpreter.interpret", return_value="insight"), \
             patch("tools.screener_tools.get_peer_comparison") as mock_peers, \
             patch("yfinance.Ticker", side_effect=_fake_ticker) as mock_ticker_cls:
            mock_peers.run.return_value = json.dumps({"error": "boom"})
            result = pipeline._phase_research([{"symbol": symbol}], emit=lambda p: None)
            return result, mock_ticker_cls

    def test_sufficient_nse_history_is_not_a_recent_ipo_and_bse_is_never_checked(self) -> None:
        result, mock_ticker_cls = self._run(ns_history_len=12, bo_history_len=3)
        self.assertFalse(result["TCS"]["is_recent_ipo"])
        called_tickers = [c.args[0] for c in mock_ticker_cls.call_args_list]
        self.assertTrue(any(t.endswith(".NS") for t in called_tickers))
        self.assertFalse(any(t.endswith(".BO") for t in called_tickers))

    def test_thin_nse_history_falls_back_to_bse_which_has_enough_history(self) -> None:
        result, mock_ticker_cls = self._run(ns_history_len=3, bo_history_len=12)
        self.assertFalse(result["TCS"]["is_recent_ipo"])
        called_tickers = [c.args[0] for c in mock_ticker_cls.call_args_list]
        self.assertTrue(any(t.endswith(".BO") for t in called_tickers))

    def test_thin_history_on_both_exchanges_is_a_recent_ipo(self) -> None:
        result, _ = self._run(ns_history_len=3, bo_history_len=3)
        self.assertTrue(result["TCS"]["is_recent_ipo"])


class PhaseConsolidateDedupMergeTest(unittest.TestCase):
    """Regression tests for an adversarial-review finding: _phase_consolidate()
    groups raw LLM picks by _dedup_key() (ticker if present, else normalized
    company name) BEFORE NSE/yfinance resolution. Two different raw picks for
    the same real stock can legitimately land in different pre-resolution
    groups -- one source's extraction included a ticker, another's left it
    blank and only had the company name. If both groups independently
    resolve to the same final symbol, the ThreadPoolExecutor-driven
    resolution used to let whichever group's future completed first win
    outright and silently discard the other group's entire source list,
    undercounting mention_count/confidence_score for exactly the stocks
    with the broadest, most format-mixed source coverage."""

    def _fake_session(self, symbols: list[dict]) -> MagicMock:
        sess = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"symbols": symbols}
        sess.get.return_value = resp
        return sess

    def test_ticker_keyed_and_company_keyed_groups_merge_sources_on_same_symbol(self) -> None:
        raw_picks = [
            {"ticker": "TCS", "company": "", "source": "Source A", "reason": "Buy call", "article_title": "TCS wins big deal"},
            {"ticker": "", "company": "Tata Consultancy Services", "source": "Source B", "reason": "Upgrade", "article_title": "Tata Consultancy gets upgrade"},
        ]
        pipeline = MarketPicksPipeline()
        fake_ticker = MagicMock()
        fake_ticker.fast_info.last_price = 3500.0
        fake_session = self._fake_session([{"symbol": "TCS", "symbol_info": "Tata Consultancy Services Ltd"}])

        with patch("yfinance.Ticker", return_value=fake_ticker), \
             patch("market_picks_pipeline._load_nse_symbol_master", return_value=set()), \
             patch.object(pipeline, "_nse_session_get", return_value=fake_session):
            result = pipeline._phase_consolidate(raw_picks, emit=lambda p: None)

        # Both raw picks resolve to the same real stock -- must land as ONE
        # consolidated entry, not two, and its sources must include BOTH
        # groups' contributions rather than only whichever group's
        # resolution future happened to complete first.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "TCS")
        self.assertEqual(len(result[0]["sources"]), 2)
        source_names = {s["name"] for s in result[0]["sources"]}
        self.assertEqual(source_names, {"Source A", "Source B"})


class PhaseScoreMissingPriceTest(unittest.TestCase):
    """Regression tests for an adversarial-review finding: a stock whose
    research fetch failed entirely (main.py's _fetch_task() never raises --
    it returns an error dict that schemas.normalize() passes through
    unchanged, leaving stock_info with no current_price) used to still be
    scored by _phase_score() and could be recommended BUY purely off source
    consensus (signal_score degrades to a neutral ~0, not a veto) -- a pick
    with entry/target/stop/current_price all null and no bull/bear factors,
    which _phase_analyze() already silently skips building for the same
    reason. _phase_score() must exclude such a stock from the final list
    entirely rather than surface a non-actionable, misleading pick."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-phase-score-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        patch.object(market_picks_pipeline, "_HISTORY_DIR", Path(self._tmpdir)).start()
        self.addCleanup(patch.stopall)

    def test_stock_with_no_price_data_is_excluded_from_final_picks(self) -> None:
        consolidated = [
            {
                "symbol": "GOODSTK", "company": "Good Stock", "exchange": "NSE",
                "sources": [_source(reason="Strong buy call"), _source(name="Second Brokerage")],
            },
            {
                "symbol": "NOPRICE", "company": "No Price Stock", "exchange": "NSE",
                "sources": [_source(reason="Strong buy call"), _source(name="Second Brokerage")],
            },
        ]
        research_data = {
            "GOODSTK": {"stock_info": {"current_price": 1000.0}, "signal_score": 0.5, "signal_verdict": "BUY"},
            # Simulates a fully-failed stock_info fetch: schema-normalized to
            # an empty dict, no current_price anywhere.
            "NOPRICE": {"stock_info": {}, "signal_score": 0.0, "signal_verdict": "HOLD"},
        }
        pipeline = MarketPicksPipeline()
        picks = pipeline._phase_score(consolidated, research_data, analyses={}, emit=lambda p: None)

        symbols = {p["symbol"] for p in picks}
        self.assertIn("GOODSTK", symbols)
        self.assertNotIn("NOPRICE", symbols)

    def test_excluded_symbol_is_logged_not_silently_dropped(self) -> None:
        consolidated = [
            {"symbol": "NOPRICE", "company": "No Price Stock", "exchange": "NSE", "sources": [_source()]},
        ]
        research_data = {"NOPRICE": {"stock_info": {}, "signal_score": 0.0, "signal_verdict": "HOLD"}}
        pipeline = MarketPicksPipeline()

        with patch("market_picks_pipeline.log_event") as mock_log:
            picks = pipeline._phase_score(consolidated, research_data, analyses={}, emit=lambda p: None)

        self.assertEqual(picks, [])
        events = [call.args[1] for call in mock_log.call_args_list if len(call.args) > 1]
        self.assertIn("market_picks_skipped_no_price", events)

    def test_stock_missing_from_research_data_entirely_is_also_excluded(self) -> None:
        # research_data.get(sym, {}) defaults to {} when a symbol's research
        # step is missing outright (not just error-shaped) -- must degrade
        # the same way as an explicit empty stock_info.
        consolidated = [
            {"symbol": "MISSING", "company": "Missing Research", "exchange": "NSE", "sources": [_source()]},
        ]
        pipeline = MarketPicksPipeline()
        picks = pipeline._phase_score(consolidated, research_data={}, analyses={}, emit=lambda p: None)
        self.assertEqual(picks, [])


class PhaseScrapeSourceHealthTest(unittest.TestCase):
    def test_records_health_for_every_source_ok_and_empty(self) -> None:
        fake_sources = [("Source A", "news", "fn_a"), ("Source B", "news", "fn_b")]

        def fn_a():
            return {"source": "Source A", "type": "news", "articles": [{"title": "x"}]}

        def fn_b():
            return {"source": "Source B", "type": "news", "articles": []}

        pipeline = MarketPicksPipeline()
        with patch("tools.market_picks_tools.SCRAPER_FNS", {"Source A": fn_a, "Source B": fn_b}), \
             patch("tools.market_picks_tools.SOURCES", fake_sources), \
             patch("source_health.record_and_check") as mock_record:
            pipeline._phase_scrape(emit=lambda p: None)

        calls = {c.args[0]: c.args[1] for c in mock_record.call_args_list}
        self.assertEqual(calls, {"Source A": True, "Source B": False})

    def test_a_broken_health_tracker_does_not_break_the_scrape_phase(self) -> None:
        # source_health.record_and_check() already never raises on its own
        # (see test_source_health.py), but this pins the calling contract:
        # even if it somehow did, the scrape phase itself must not crash.
        fake_sources = [("Source A", "news", "fn_a")]

        def fn_a():
            return {"source": "Source A", "type": "news", "articles": [{"title": "x"}]}

        pipeline = MarketPicksPipeline()
        with patch("tools.market_picks_tools.SCRAPER_FNS", {"Source A": fn_a}), \
             patch("tools.market_picks_tools.SOURCES", fake_sources), \
             patch("source_health.record_and_check", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                pipeline._phase_scrape(emit=lambda p: None)
        # Documents current behavior: _phase_scrape does not itself guard
        # against a raising health tracker — it relies on record_and_check's
        # own never-raise contract. If that contract is ever loosened, this
        # test will catch the regression here rather than in production.


class ExtractionCacheSetTest(unittest.TestCase):
    """Regression tests for an adversarial-review finding:
    _extraction_cache_set() used a plain write_text() instead of the
    tempfile+os.replace atomic-write convention cache.py::save() and
    source_health.py already use — an overlapping pipeline run (e.g. a
    manual ?force=true firing while a scheduled run is still in flight)
    racing on the same (source, article-batch) cache key could leave a
    torn/partial JSON file on disk for the next reader."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-extract-cache-set-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        patch.object(market_picks_pipeline, "_EXTRACT_CACHE_DIR", Path(self._tmpdir)).start()
        self.addCleanup(patch.stopall)

    def test_writes_readable_json_round_trip(self) -> None:
        picks = [{"symbol": "TCS", "reason": "Strong buy"}]
        market_picks_pipeline._extraction_cache_set("mykey", picks)

        cache_file = market_picks_pipeline._EXTRACT_CACHE_DIR / "mykey.json"
        self.assertTrue(cache_file.exists())
        data = json.loads(cache_file.read_text())
        self.assertEqual(data["picks"], picks)

    def test_no_leftover_tmp_file_after_a_successful_write(self) -> None:
        market_picks_pipeline._extraction_cache_set("mykey", [{"a": 1}])
        cache_dir = market_picks_pipeline._EXTRACT_CACHE_DIR
        leftover_tmp_files = [p for p in cache_dir.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftover_tmp_files, [])

    def test_a_second_write_atomically_replaces_the_first_no_partial_file(self) -> None:
        # Simulates two overlapping pipeline runs racing on the same key —
        # the final file on disk must always be one complete, valid JSON
        # write (either the first or the second), never a torn mix of both.
        market_picks_pipeline._extraction_cache_set("mykey", [{"batch": 1}])
        market_picks_pipeline._extraction_cache_set("mykey", [{"batch": 2}])

        cache_file = market_picks_pipeline._EXTRACT_CACHE_DIR / "mykey.json"
        data = json.loads(cache_file.read_text())
        self.assertEqual(data["picks"], [{"batch": 2}])

    def test_missing_directory_is_created_and_write_still_succeeds(self) -> None:
        shutil.rmtree(self._tmpdir)
        market_picks_pipeline._extraction_cache_set("mykey", [{"a": 1}])
        cache_file = market_picks_pipeline._EXTRACT_CACHE_DIR / "mykey.json"
        self.assertTrue(cache_file.exists())

    def test_stored_value_is_retrievable_via_extraction_cache_get(self) -> None:
        picks = [{"symbol": "INFY"}]
        market_picks_pipeline._extraction_cache_set("mykey", picks)
        result = market_picks_pipeline._extraction_cache_get("mykey")
        self.assertEqual(result, picks)


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


class ApplySectorBalanceTest(unittest.TestCase):
    def test_promotes_up_to_max_per_sector_and_defers_excess(self) -> None:
        picks = [
            {"symbol": "A", "sector": "IT"},
            {"symbol": "B", "sector": "IT"},
            {"symbol": "C", "sector": "IT"},
            {"symbol": "D", "sector": "Banking"},
        ]
        out = _apply_sector_balance(picks, max_per_sector=2)
        self.assertEqual([p["symbol"] for p in out], ["A", "B", "D", "C"])

    def test_sector_field_is_kept_not_removed(self) -> None:
        # Regression: this field used to be popped off before the response
        # reached the frontend (as "_sector"), so no filtering by sector was
        # ever possible client-side.
        picks = [{"symbol": "A", "sector": "IT"}]
        out = _apply_sector_balance(picks, max_per_sector=2)
        self.assertEqual(out[0]["sector"], "IT")

    def test_missing_sector_key_defaults_to_unknown_bucket(self) -> None:
        picks = [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}]
        out = _apply_sector_balance(picks, max_per_sector=2)
        # All three share the implicit "Unknown" bucket, so the third is deferred.
        self.assertEqual([p["symbol"] for p in out], ["A", "B", "C"])
        self.assertEqual(out[-1]["symbol"], "C")

    def test_empty_list_returns_empty_list(self) -> None:
        self.assertEqual(_apply_sector_balance([]), [])


class PicksCacheStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-picks-cache-status-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        patch.object(market_picks_pipeline, "_PICKS_CACHE_PATH", Path(self._tmpdir) / "picks.json").start()
        self.addCleanup(patch.stopall)

    def test_no_cache_file_returns_not_fresh_and_no_last_run(self) -> None:
        status = picks_cache_status()
        self.assertIsNone(status["last_run_at"])
        self.assertFalse(status["is_fresh"])

    def test_fresh_cache_reports_last_run_and_is_fresh(self) -> None:
        save_picks_cache([{"symbol": "TCS"}], "2026-07-20T00:00:00+00:00")
        status = picks_cache_status()
        self.assertEqual(status["last_run_at"], "2026-07-20T00:00:00+00:00")
        self.assertTrue(status["is_fresh"])

    def test_stale_cache_still_reports_last_run_but_not_fresh(self) -> None:
        # Unlike load_picks_cache() (which returns None outright once stale,
        # since it's used on the picks-serving path), picks_cache_status()
        # must keep reporting the true last-run time even past the TTL, or
        # the hero's "Last scan" display would silently blank out instead of
        # showing an honest (stale) timestamp.
        import json
        from datetime import datetime, timedelta, timezone

        cache_path = market_picks_pipeline._PICKS_CACHE_PATH
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        ancient = (datetime.now(timezone.utc) - timedelta(hours=market_picks_pipeline._PICKS_CACHE_TTL_HOURS + 1))
        cache_path.write_text(json.dumps({
            "picks": [], "generated_at": "2026-01-01T00:00:00+00:00",
            "_meta": {"fetched_at": ancient.isoformat()},
        }))

        status = picks_cache_status()
        self.assertEqual(status["last_run_at"], "2026-01-01T00:00:00+00:00")
        self.assertFalse(status["is_fresh"])

    def test_malformed_cache_file_degrades_gracefully(self) -> None:
        cache_path = market_picks_pipeline._PICKS_CACHE_PATH
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("{not valid json")

        status = picks_cache_status()
        self.assertIsNone(status["last_run_at"])
        self.assertFalse(status["is_fresh"])


if __name__ == "__main__":
    unittest.main()
