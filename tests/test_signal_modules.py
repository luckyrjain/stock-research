"""Direct branch-level tests for the individual signal modules.

These previously had no dedicated coverage: tests/test_signal_engine.py only
exercises run_signal_engine() with Signal objects patched in wholesale, so
none of these modules' own threshold logic was ever actually executed.
"""
import unittest

from signals.volume import volume_signal
from signals.valuation import valuation_signal
from signals.filings import filings_signal
from signals.growth import growth_signal, _to_float


class VolumeSignalTest(unittest.TestCase):
    def test_missing_volume_returns_unknown(self) -> None:
        sig = volume_signal({"avg_volume": 1000})
        self.assertEqual(sig.value, "UNKNOWN")
        self.assertEqual(sig.score, 0)

    def test_missing_avg_volume_returns_unknown(self) -> None:
        sig = volume_signal({"volume": 1000})
        self.assertEqual(sig.value, "UNKNOWN")

    def test_zero_avg_volume_returns_unknown_not_division_error(self) -> None:
        sig = volume_signal({"volume": 1000, "avg_volume": 0})
        self.assertEqual(sig.value, "UNKNOWN")

    def test_ratio_above_3_is_strong_accumulation(self) -> None:
        sig = volume_signal({"volume": 3100, "avg_volume": 1000})
        self.assertEqual(sig.value, "STRONG_ACCUMULATION")
        self.assertEqual(sig.score, 1.0)
        self.assertAlmostEqual(sig.meta["ratio"], 3.1)

    def test_ratio_above_2_is_accumulation(self) -> None:
        sig = volume_signal({"volume": 2500, "avg_volume": 1000})
        self.assertEqual(sig.value, "ACCUMULATION")
        self.assertEqual(sig.score, 0.7)

    def test_ratio_below_half_is_drying_volume(self) -> None:
        sig = volume_signal({"volume": 400, "avg_volume": 1000})
        self.assertEqual(sig.value, "DRYING_VOLUME")
        self.assertEqual(sig.score, -0.5)

    def test_ratio_in_normal_band_is_normal(self) -> None:
        sig = volume_signal({"volume": 1500, "avg_volume": 1000})
        self.assertEqual(sig.value, "NORMAL")
        self.assertEqual(sig.score, 0)

    def test_ratio_exactly_at_boundaries_does_not_tip_over(self) -> None:
        # ratio == 3 should NOT be STRONG_ACCUMULATION (strict >)
        self.assertEqual(volume_signal({"volume": 3000, "avg_volume": 1000}).value, "ACCUMULATION")
        # ratio == 2 should NOT be ACCUMULATION (strict >)
        self.assertEqual(volume_signal({"volume": 2000, "avg_volume": 1000}).value, "NORMAL")
        # ratio == 0.5 should NOT be DRYING_VOLUME (strict <)
        self.assertEqual(volume_signal({"volume": 500, "avg_volume": 1000}).value, "NORMAL")

    def test_high_volume_on_a_down_day_is_distribution_not_accumulation(self) -> None:
        # Regression test for an adversarial-review finding: a volume spike
        # alone doesn't say which direction the volume was on — heavy
        # buying and heavy panic-selling/distribution both show up as an
        # above-average ratio. Previously this signal had no visibility
        # into price direction at all, so a stock crashing on 3x average
        # volume (a classic distribution/capitulation pattern) scored the
        # same maximally bullish STRONG_ACCUMULATION as a genuine rally on
        # heavy volume.
        sig = volume_signal({"volume": 3100, "avg_volume": 1000, "change_pct": -5.0})
        self.assertEqual(sig.value, "DISTRIBUTION")
        self.assertEqual(sig.score, -1.0)

        sig = volume_signal({"volume": 2500, "avg_volume": 1000, "change_pct": -2.0})
        self.assertEqual(sig.value, "DISTRIBUTION")
        self.assertEqual(sig.score, -0.7)

    def test_high_volume_on_an_up_day_is_still_accumulation(self) -> None:
        sig = volume_signal({"volume": 3100, "avg_volume": 1000, "change_pct": 4.0})
        self.assertEqual(sig.value, "STRONG_ACCUMULATION")
        self.assertEqual(sig.score, 1.0)

    def test_missing_change_pct_defaults_to_accumulation_not_a_guess(self) -> None:
        # No price-direction data available (e.g. a caller that never
        # passed change_pct) must not guess a direction -- preserves the
        # pre-existing "assume accumulation on a spike" behavior rather
        # than defaulting to DISTRIBUTION or UNKNOWN.
        sig = volume_signal({"volume": 3100, "avg_volume": 1000})
        self.assertEqual(sig.value, "STRONG_ACCUMULATION")

    def test_flat_day_with_high_volume_is_still_accumulation_not_distribution(self) -> None:
        # change_pct == 0.0 (a flat day) is not a "down day".
        sig = volume_signal({"volume": 3100, "avg_volume": 1000, "change_pct": 0.0})
        self.assertEqual(sig.value, "STRONG_ACCUMULATION")


class ValuationSignalTest(unittest.TestCase):
    def test_missing_pe_returns_unknown(self) -> None:
        sig = valuation_signal({})
        self.assertEqual(sig.value, "UNKNOWN")

    def test_zero_pe_returns_unknown(self) -> None:
        # pe == 0 is falsy, so it must be treated the same as "missing" rather
        # than as a (nonsensical) extremely-cheap valuation.
        sig = valuation_signal({"pe": 0})
        self.assertEqual(sig.value, "UNKNOWN")

    def test_pe_above_60_is_extreme_overvalued(self) -> None:
        sig = valuation_signal({"pe": 75})
        self.assertEqual(sig.value, "EXTREME_OVERVALUED")
        self.assertEqual(sig.score, -1.0)

    def test_pe_above_40_is_overvalued(self) -> None:
        sig = valuation_signal({"pe": 50})
        self.assertEqual(sig.value, "OVERVALUED")
        self.assertEqual(sig.score, -0.6)

    def test_pe_below_15_is_undervalued(self) -> None:
        sig = valuation_signal({"pe": 10})
        self.assertEqual(sig.value, "UNDERVALUED")
        self.assertEqual(sig.score, 0.6)

    def test_pe_in_fair_band_is_fair(self) -> None:
        sig = valuation_signal({"pe": 25})
        self.assertEqual(sig.value, "FAIR")
        self.assertEqual(sig.score, 0)

    def test_pe_exactly_at_boundaries_does_not_tip_over(self) -> None:
        self.assertEqual(valuation_signal({"pe": 60}).value, "OVERVALUED")
        self.assertEqual(valuation_signal({"pe": 40}).value, "FAIR")
        self.assertEqual(valuation_signal({"pe": 15}).value, "FAIR")

    def test_negative_pe_returns_unknown_not_undervalued(self) -> None:
        # Regression test: a negative P/E (loss-making company) used to pass
        # the `not pe` guard (a negative number is truthy) and then satisfy
        # `pe < 15`, misreading it as UNDERVALUED (+0.6, the largest bullish
        # score this signal can produce) instead of degrading to UNKNOWN.
        sig = valuation_signal({"pe": -8})
        self.assertEqual(sig.value, "UNKNOWN")
        self.assertEqual(sig.score, 0)


class FilingsSignalTest(unittest.TestCase):
    def test_no_filings_returns_none(self) -> None:
        sig = filings_signal({"filings": []})
        self.assertEqual(sig.value, "NONE")
        self.assertEqual(sig.score, 0)

    def test_missing_filings_key_returns_none(self) -> None:
        sig = filings_signal({})
        self.assertEqual(sig.value, "NONE")

    def test_single_keyword_hit_is_weak_signal(self) -> None:
        sig = filings_signal({"filings": [{"title": "New order received", "desc": ""}]})
        self.assertEqual(sig.value, "WEAK_SIGNAL")
        self.assertEqual(sig.score, 0.3)
        self.assertEqual(sig.meta["hits"], ["order"])

    def test_two_or_more_keyword_hits_is_strong_deal_flow(self) -> None:
        sig = filings_signal({"filings": [
            {"title": "New order received", "desc": ""},
            {"title": "", "desc": "Signed a partnership agreement"},
        ]})
        self.assertEqual(sig.value, "STRONG_DEAL_FLOW")
        self.assertEqual(sig.score, 0.8)
        self.assertEqual(len(sig.meta["hits"]), 3)  # order, partnership, agreement

    def test_matching_is_case_insensitive(self) -> None:
        sig = filings_signal({"filings": [{"title": "NEW CONTRACT AWARD", "desc": ""}]})
        self.assertEqual(sig.value, "STRONG_DEAL_FLOW")

    def test_irrelevant_filing_is_none(self) -> None:
        sig = filings_signal({"filings": [{"title": "Board meeting intimation", "desc": "Routine"}]})
        self.assertEqual(sig.value, "NONE")

    def test_none_title_or_desc_does_not_raise(self) -> None:
        # Regression test: NSE sometimes omits/nulls a filing's subject or
        # description, which used to reach here as an actual None (not
        # simply absent), and f.get(key, "") + str raised a TypeError,
        # crashing the whole analysis for that symbol.
        sig = filings_signal({"filings": [{"title": None, "desc": None}]})
        self.assertEqual(sig.value, "NONE")

        sig = filings_signal({"filings": [{"title": "New order received", "desc": None}]})
        self.assertEqual(sig.value, "WEAK_SIGNAL")

    def test_rating_upgrade_nudges_score_up(self) -> None:
        sig = filings_signal({"filings": [
            {"title": "CRISIL upgrades rating", "desc": "", "date": "01-Jan-2026"},
        ]})
        # Regression test: the label must reflect what actually moved the
        # score off zero — a badge literally labeled "NONE" while still
        # nonzero/colored would be misleading in the UI.
        self.assertEqual(sig.value, "RATING_UPGRADE")
        self.assertAlmostEqual(sig.score, 0.15)
        self.assertEqual(sig.meta["rating_action"]["action"], "upgrade")

    def test_rating_downgrade_nudges_score_down(self) -> None:
        sig = filings_signal({"filings": [
            {"title": "ICRA downgrades rating", "desc": "", "date": "01-Jan-2026"},
        ]})
        self.assertEqual(sig.value, "RATING_DOWNGRADE")
        self.assertAlmostEqual(sig.score, -0.15)

    def test_rating_reaffirmed_does_not_move_score(self) -> None:
        sig = filings_signal({"filings": [
            {"title": "CARE reaffirms rating", "desc": "", "date": "01-Jan-2026"},
        ]})
        self.assertEqual(sig.score, 0.0)
        self.assertEqual(sig.meta["rating_action"]["action"], "reaffirmed")

    def test_rating_nudge_combines_with_deal_flow_score(self) -> None:
        sig = filings_signal({"filings": [
            {"title": "New order received", "desc": "", "date": "01-Jan-2026"},
            {"title": "CRISIL upgrades rating", "desc": "", "date": "02-Jan-2026"},
        ]})
        self.assertEqual(sig.value, "WEAK_SIGNAL")
        self.assertAlmostEqual(sig.score, 0.45)  # 0.3 base + 0.15 nudge

    def test_no_rating_mention_leaves_meta_without_rating_action_key(self) -> None:
        sig = filings_signal({"filings": [{"title": "New order received", "desc": ""}]})
        self.assertNotIn("rating_action", sig.meta)


class ToFloatTest(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        self.assertIsNone(_to_float(None))

    def test_percent_string_is_stripped_and_converted(self) -> None:
        self.assertEqual(_to_float("18.5%"), 18.5)

    def test_plain_number_string_converts(self) -> None:
        self.assertEqual(_to_float("42"), 42.0)

    def test_numeric_passthrough(self) -> None:
        self.assertEqual(_to_float(7), 7.0)

    def test_unparseable_string_returns_none_not_raise(self) -> None:
        self.assertIsNone(_to_float("N/A"))

    def test_non_numeric_type_returns_none_not_raise(self) -> None:
        self.assertIsNone(_to_float(["not", "a", "number"]))


class GrowthSignalTest(unittest.TestCase):
    def test_missing_sales_growth_returns_unknown(self) -> None:
        sig = growth_signal({"ratios": {"Profit growth 3Y": "15%"}})
        self.assertEqual(sig.value, "UNKNOWN")

    def test_missing_profit_growth_returns_unknown(self) -> None:
        sig = growth_signal({"ratios": {"Sales growth 3Y": "15%"}})
        self.assertEqual(sig.value, "UNKNOWN")

    def test_unparseable_ratios_returns_unknown(self) -> None:
        sig = growth_signal({"ratios": {"Sales growth 3Y": "N/A", "Profit growth 3Y": "N/A"}})
        self.assertEqual(sig.value, "UNKNOWN")

    def test_high_sales_and_profit_with_strong_margin_is_high_quality_growth(self) -> None:
        sig = growth_signal({"ratios": {
            "Sales growth 3Y": "25%", "Profit growth 3Y": "30%", "EBITDA margin": "28%",
        }})
        self.assertEqual(sig.value, "HIGH_QUALITY_GROWTH")
        self.assertEqual(sig.score, 0.9)

    def test_high_sales_and_profit_without_strong_margin_is_high_growth(self) -> None:
        sig = growth_signal({"ratios": {
            "Sales growth 3Y": "25%", "Profit growth 3Y": "30%", "EBITDA margin": "10%",
        }})
        self.assertEqual(sig.value, "HIGH_GROWTH")
        self.assertEqual(sig.score, 0.7)

    def test_high_sales_and_profit_with_missing_margin_is_high_growth(self) -> None:
        sig = growth_signal({"ratios": {"Sales growth 3Y": "25%", "Profit growth 3Y": "30%"}})
        self.assertEqual(sig.value, "HIGH_GROWTH")

    def test_moderate_sales_growth_is_moderate_growth(self) -> None:
        sig = growth_signal({"ratios": {"Sales growth 3Y": "12%", "Profit growth 3Y": "5%"}})
        self.assertEqual(sig.value, "MODERATE_GROWTH")
        self.assertEqual(sig.score, 0.3)

    def test_low_sales_growth_is_low_growth(self) -> None:
        sig = growth_signal({"ratios": {"Sales growth 3Y": "5%", "Profit growth 3Y": "2%"}})
        self.assertEqual(sig.value, "LOW_GROWTH")
        self.assertEqual(sig.score, -0.5)

    def test_decent_sales_growth_with_collapsing_profit_is_not_moderate_growth(self) -> None:
        # Regression test for an adversarial-review finding: the
        # MODERATE_GROWTH tier only checked sales_3y, never profit_3y — a
        # company growing revenue while its profit is collapsing (margin
        # compression, an unsustainable growth push, one-off costs) used to
        # score the same positive +0.3 as a genuinely healthy moderate
        # grower. +15% sales with -90% profit must not read as a positive
        # growth signal.
        sig = growth_signal({"ratios": {"Sales growth 3Y": "15%", "Profit growth 3Y": "-90%"}})
        self.assertNotEqual(sig.value, "MODERATE_GROWTH")
        self.assertLessEqual(sig.score, 0)

    def test_negative_growth_is_low_growth_not_a_crash(self) -> None:
        sig = growth_signal({"ratios": {"Sales growth 3Y": "-10%", "Profit growth 3Y": "-20%"}})
        self.assertEqual(sig.value, "LOW_GROWTH")


if __name__ == "__main__":
    unittest.main()
