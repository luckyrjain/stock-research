import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.modules.setdefault(
    "crewai",
    SimpleNamespace(Agent=object, Task=object, Crew=object, Process=object, LLM=object),
)
sys.modules.setdefault(
    "tools.nse_tools",
    SimpleNamespace(get_stock_quote=object(), get_mf_holdings=object()),
)
sys.modules.setdefault(
    "tools.screener_tools",
    SimpleNamespace(get_fundamentals=object(), get_holdings=object()),
)
sys.modules.setdefault(
    "tools.news_tools",
    SimpleNamespace(get_latest_news=object()),
)

import crew
from state_store_harness import isolated_state_store


def _llm_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class AnalysisGuardrailFallbackTest(unittest.TestCase):
    _INVALID_PAYLOAD = {
        "symbol": "TCS",
        "recommendation": "HOLD",
        "confidence": "LOW",
        "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
        "valuation": {"verdict": "Fairly Valued", "comment": "P/E 20, ROCE 25, ROE 18."},
        "business_quality": "Reasonable return ratios.",
        "bull_factors": ["Only one", "Only two"],
        "bear_factors": ["Risk one", "Risk two"],
        "key_risks": ["Risk A", "Risk B", "Risk C"],
        "news_highlights": "Headline summary",
        "institutional_trend": "Promoters 50%, FIIs 10%, DIIs 12%",
        "news_sentiment": "Neutral",
    }

    _VALID_PAYLOAD = {
        "symbol": "SAILIFE",
        "recommendation": "HOLD",
        "confidence": "LOW",
        "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
        "valuation": {"verdict": "Fairly Valued", "comment": "P/E 64.8, P/B 9.4, ROCE 14.1, ROE 11.0."},
        "business_quality": "ROCE is 14.1 and ROE is 11.0.",
        "bull_factors": ["P/E is 64.8.", "ROCE is 14.1.", "DIIs hold 31.54%."],
        "bear_factors": ["P/B is 9.4.", "Promoters hold 34.61%."],
        "key_risks": ["Premium valuation at P/E 64.8.", "Limited news coverage.", "Promoter ownership is 34.61%."],
        "news_highlights": "One RSI-based headline was available.",
        "institutional_trend": "Promoters hold 34.61%, FIIs 21.17%, DIIs 31.54%.",
        "news_sentiment": "Neutral",
    }

    def setUp(self) -> None:
        # crew.py records LLM cost on every completion() call via llm_cost.py —
        # point its persistence at a throwaway in-memory DB so this test file
        # never touches a real one, same convention as every other stateful
        # module's own test file.
        self.addCleanup(isolated_state_store().close)

        self.all_data = {
            "stock_info": {
                "symbol": "SAILIFE",
                "sector": "Healthcare",
                "industry": "Diagnostics & Research",
                "about": (
                    "Sai Life Sciences Limited operates as a contract research, development, "
                    "and manufacturing organization in India and internationally."
                ),
            },
            "research": {
                "symbol": "SAILIFE",
                "about": "Sai Life Sciences carries out contract research and manufacturing activities.",
            },
            "news": {
                "articles": [
                    {
                        "title": "Sai Life Sciences among stocks showing bullish RSI upswing",
                        "description": "The article highlights a technical RSI signal.",
                    }
                ]
            },
            "shareholding": {
                "shareholding_pattern": {
                    "Promoters": 34.61,
                    "FIIs": 21.17,
                    "DIIs": 31.54,
                    "Public": 12.69,
                }
            },
        }

    def test_validate_analysis_payload_rejects_too_few_bull_factors(self) -> None:
        payload = {
            "symbol": "TCS",
            "recommendation": "HOLD",
            "confidence": "LOW",
            "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
            "valuation": {"verdict": "Fairly Valued", "comment": "P/E 20, ROCE 25, ROE 18."},
            "business_quality": "Reasonable return ratios.",
            "bull_factors": ["Only one", "Only two"],
            "bear_factors": ["Risk one", "Risk two"],
            "key_risks": ["Risk A", "Risk B", "Risk C"],
            "news_highlights": "Headline summary",
            "institutional_trend": "Promoters 50%, FIIs 10%, DIIs 12%",
            "news_sentiment": "Neutral",
        }

        ok, message = crew._validate_analysis_payload(payload, self.all_data)
        self.assertFalse(ok)
        self.assertEqual(message, "Field 'bull_factors' must be a list with at least 3 items.")

    def test_validate_analysis_payload_rejects_non_list_bull_factors(self) -> None:
        # Regression test for an adversarial-review finding: bull_factors/
        # bear_factors/key_risks were only checked via truthiness + len(),
        # never isinstance(list). An LLM occasionally returns comma-joined
        # prose instead of a JSON array for a list field -- a non-empty
        # string of sufficient length passed both the truthiness check and
        # the len() >= N check unnoticed, reaching results-dashboard.tsx's
        # .map() call on this field and crashing the render with a
        # TypeError, since a string isn't an array.
        payload = dict(
            self._VALID_PAYLOAD,
            bull_factors="Strong ROCE of 20%, improving margins, and robust FCF drive the bull case",
        )
        ok, message = crew._validate_analysis_payload(payload, self.all_data)
        self.assertFalse(ok)
        self.assertIn("bull_factors", message)
        self.assertIn("list", message)

    def test_validate_analysis_payload_rejects_missing_symbol_valuation_news_sentiment(self) -> None:
        # Regression test: config/analyst.json's own output_schema (and
        # frontend/types/index.ts's Analysis interface) require symbol,
        # valuation, and news_sentiment -- none of which were previously
        # enforced by this guardrail at all, so a payload missing any of
        # them still passed as "valid".
        for field in ("symbol", "valuation", "news_sentiment"):
            payload = dict(self._VALID_PAYLOAD)
            del payload[field]
            ok, message = crew._validate_analysis_payload(payload, self.all_data)
            self.assertFalse(ok, f"payload missing '{field}' should be rejected")
            self.assertIn(field, message)

    def test_validate_analysis_payload_rejects_invalid_news_sentiment_enum(self) -> None:
        payload = dict(self._VALID_PAYLOAD, news_sentiment="Bullish")
        ok, message = crew._validate_analysis_payload(payload, self.all_data)
        self.assertFalse(ok)
        self.assertIn("news_sentiment", message)

    def test_validate_analysis_payload_rejects_malformed_valuation_shape(self) -> None:
        payload = dict(self._VALID_PAYLOAD, valuation={"verdict": "Fairly Valued"})  # missing 'comment'
        ok, message = crew._validate_analysis_payload(payload, self.all_data)
        self.assertFalse(ok)
        self.assertIn("valuation", message)

    def test_validate_analysis_payload_rejects_directional_shareholding_claim_without_trend_data(self) -> None:
        payload = {
            "symbol": "SAILIFE",
            "recommendation": "HOLD",
            "confidence": "LOW",
            "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
            "valuation": {"verdict": "Fairly Valued", "comment": "P/E 64.8, P/B 9.4, ROCE 14.1, ROE 11.0."},
            "business_quality": "ROCE is 14.1 and ROE is 11.0.",
            "bull_factors": ["P/E is 64.8.", "ROCE is 14.1.", "DIIs hold 31.54%."],
            "bear_factors": ["P/B is 9.4.", "Promoters hold 34.61%."],
            "key_risks": ["Premium valuation at P/E 64.8.", "Limited news coverage.", "Promoter ownership is 34.61%."],
            "news_highlights": "One RSI-based headline was available.",
            "institutional_trend": "FIIs are rising and DIIs are improving, with FIIs at 21.17% and DIIs at 31.54%.",
            "news_sentiment": "Neutral",
        }

        ok, message = crew._validate_analysis_payload(payload, self.all_data)
        self.assertFalse(ok)
        self.assertIn("single shareholding snapshot", message)

    def test_validate_analysis_payload_rejects_unsupported_regulatory_risk(self) -> None:
        payload = {
            "symbol": "SAILIFE",
            "recommendation": "HOLD",
            "confidence": "LOW",
            "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
            "valuation": {"verdict": "Fairly Valued", "comment": "P/E 64.8, P/B 9.4, ROCE 14.1, ROE 11.0."},
            "business_quality": "ROCE is 14.1 and ROE is 11.0.",
            "bull_factors": ["P/E is 64.8.", "ROCE is 14.1.", "DIIs hold 31.54%."],
            "bear_factors": ["P/B is 9.4.", "Promoters hold 34.61%."],
            "key_risks": [
                "Regulatory changes could hurt CRDMO demand.",
                "The stock trades at 64.8 times earnings.",
                "News flow is limited to one recent article.",
            ],
            "news_highlights": "One RSI-based headline was available.",
            "institutional_trend": "Promoters hold 34.61%, FIIs 21.17%, and DIIs 31.54%; trend data is unavailable from this snapshot.",
            "news_sentiment": "Neutral",
        }

        ok, message = crew._validate_analysis_payload(payload, self.all_data)
        self.assertFalse(ok)
        self.assertIn("regulatory risk", message)

    def test_validate_analysis_payload_accepts_regulatory_risk_grounded_in_filings(self) -> None:
        # Regression test: config/analyst.json's filings instruction tells the
        # analyst to cite material filings (including regulatory action) as
        # risk evidence, but crew._source_text() used to only look at
        # stock_info/research/news — never all_data["filings"] — so a claim
        # grounded entirely in real filings data would be wrongly flagged as
        # "unsupported" by this same guardrail. This exercises the same
        # regulatory-risk wording as the rejection test above, but with a
        # filings entry that actually supports it.
        all_data_with_filings = {
            **self.all_data,
            "filings": {
                "filings": [
                    {
                        "title": "USFDA issues Form 483 observations",
                        "desc": "The company received regulatory observations following a USFDA inspection.",
                        "date": "2026-07-20",
                        "category": "Regulatory",
                    }
                ]
            },
        }
        payload = {
            "symbol": "SAILIFE",
            "recommendation": "HOLD",
            "confidence": "LOW",
            "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
            "valuation": {"verdict": "Fairly Valued", "comment": "P/E 64.8, P/B 9.4, ROCE 14.1, ROE 11.0."},
            "business_quality": "ROCE is 14.1 and ROE is 11.0.",
            "bull_factors": ["P/E is 64.8.", "ROCE is 14.1.", "DIIs hold 31.54%."],
            "bear_factors": ["P/B is 9.4.", "Promoters hold 34.61%."],
            "key_risks": [
                "Regulatory risk from a recent USFDA Form 483 observation.",
                "The stock trades at 64.8 times earnings.",
                "News flow is limited to one recent article.",
            ],
            "news_highlights": "One RSI-based headline was available.",
            "institutional_trend": "Promoters hold 34.61%, FIIs 21.17%, and DIIs 31.54%; trend data is unavailable from this snapshot.",
            "news_sentiment": "Neutral",
        }

        ok, result = crew._validate_analysis_payload(payload, all_data_with_filings)
        self.assertTrue(ok, result)

    def test_regulatory_risk_check_does_not_false_trigger_on_a_neutral_mention(self) -> None:
        # Regression test for an adversarial-review finding: the regulatory-
        # risk entry in crew._analysis_support_issues's grounded_checks list
        # previously reused the exact same six-word list
        # ("regulatory", "regulation", "regulator", "rbi", "usfda", "fda")
        # as BOTH its trigger_phrases and its source_terms (a copy-paste
        # bug) — unlike the customer-concentration/competition entries,
        # which always used specific multi-word trigger phrases paired with
        # broader single-word source terms. That meant ANY bare mention of
        # "regulatory" anywhere in the analyst's own text — even a neutral,
        # non-risk statement like "regulatory filings are up to date" —
        # tripped the trigger and, absent identical wording in the source
        # data, got misclassified as an "unsupported regulatory risk claim"
        # and rejected. This payload's key_risks entry mentions "regulatory"
        # in a neutral context (not a risk assertion), and self.all_data has
        # no regulatory-related source text at all — with the fix, this
        # must validate cleanly since the text never actually asserts a
        # specific regulatory risk.
        payload = {
            "symbol": "SAILIFE",
            "recommendation": "HOLD",
            "confidence": "LOW",
            "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
            "valuation": {"verdict": "Fairly Valued", "comment": "P/E 64.8, P/B 9.4, ROCE 14.1, ROE 11.0."},
            "business_quality": "ROCE is 14.1 and ROE is 11.0.",
            "bull_factors": ["P/E is 64.8.", "ROCE is 14.1.", "DIIs hold 31.54%."],
            "bear_factors": ["P/B is 9.4.", "Promoters hold 34.61%."],
            "key_risks": [
                "Regulatory filings are up to date.",
                "The stock trades at 64.8 times earnings.",
                "News flow is limited to one recent article.",
            ],
            "news_highlights": "One RSI-based headline was available.",
            "institutional_trend": "Promoters hold 34.61%, FIIs 21.17%, and DIIs 31.54%; trend data is unavailable from this snapshot.",
            "news_sentiment": "Neutral",
        }

        ok, result = crew._validate_analysis_payload(payload, self.all_data)
        self.assertTrue(ok, result)

    def test_parse_json_object_extracts_balanced_payload_from_wrapper_text(self) -> None:
        raw = """
        tool log: starting analysis
        {"symbol": "TCS", "summary": "Plain payload", "details": {"pe": 20}}
        tool log: done
        """

        parsed = crew.parse_json_object(raw)
        self.assertEqual(parsed["symbol"], "TCS")
        self.assertEqual(parsed["details"]["pe"], 20)

    def test_parse_json_object_prefers_last_balanced_object_over_an_earlier_fragment(self) -> None:
        # Regression test for an adversarial-review finding: a verbose/
        # reasoning-style LLM completion can legitimately contain an
        # earlier, small JSON-like fragment before its real structured
        # answer -- e.g. explaining its approach with a small example
        # object, then giving the actual full payload. The parser
        # previously returned the FIRST balanced {...} object found, which
        # silently discarded the real answer and handed the guardrail a
        # fragment missing every required field.
        raw = (
            'Here is my reasoning: I will use {"P/E": 20} as context.\n\n'
            'Final answer:\n'
            '{"symbol": "TCS", "recommendation": "BUY", "confidence": "HIGH"}'
        )
        parsed = crew.parse_json_object(raw)
        self.assertEqual(parsed, {"symbol": "TCS", "recommendation": "BUY", "confidence": "HIGH"})

    def test_parse_json_object_handles_braces_inside_strings(self) -> None:
        raw = """
        prefix noise
        {"symbol": "TCS", "summary": "Value mentions {braces} in text", "ok": true}
        suffix noise
        """

        parsed = crew.parse_json_object(raw)
        self.assertEqual(parsed["symbol"], "TCS")
        self.assertEqual(parsed["summary"], "Value mentions {braces} in text")
        self.assertTrue(parsed["ok"])

    def test_invalid_structured_analysis_falls_back_safely(self) -> None:
        # Both attempts return an invalid payload (too few bull_factors) →
        # guardrail retry fires once, then the safe HOLD fallback is used.
        with patch("litellm.completion", return_value=_llm_response(json.dumps(self._INVALID_PAYLOAD))) as mock_completion:
            analysis = crew.run_analysis_with_fallback("TCS", {name: {} for name in crew.ALL_DATA_TASKS})

        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(analysis["recommendation"], "HOLD")
        self.assertEqual(analysis["confidence"], "LOW")
        self.assertEqual(len(analysis["bull_factors"]), 3)
        # The raw guardrail error is logged server-side (see analyst_llm_failed),
        # not echoed into the client-facing comment.
        self.assertNotIn("bull_factors", analysis["valuation"]["comment"])
        self.assertTrue(analysis["_degraded"])

    def test_guardrail_failure_retries_once_then_succeeds(self) -> None:
        responses = [
            _llm_response(json.dumps(self._INVALID_PAYLOAD)),
            _llm_response(json.dumps(self._VALID_PAYLOAD)),
        ]
        with patch("litellm.completion", side_effect=responses) as mock_completion:
            analysis = crew.run_analysis_with_fallback("SAILIFE", self.all_data)

        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(analysis["bull_factors"], self._VALID_PAYLOAD["bull_factors"])
        second_messages = mock_completion.call_args_list[1].kwargs["messages"]
        self.assertIn("failed validation", second_messages[-1]["content"])

    def test_validate_analysis_payload_rejects_sell_against_strong_positive_signals(self) -> None:
        payload = dict(self._VALID_PAYLOAD, recommendation="SELL")

        ok, message = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"final_score": 0.8}
        )
        self.assertFalse(ok)
        self.assertEqual(message, "Recommendation contradicts strong positive signals")

    def test_validate_analysis_payload_tolerates_a_signal_context_missing_final_score(self) -> None:
        # Regression test: a signal_context dict without a "final_score" key
        # (a differently-shaped dict from some future/other caller) must
        # degrade by skipping the three quant-cross-check guards, not raise
        # a KeyError — which a broad try/except elsewhere would otherwise
        # silently misclassify as a provider outage.
        #
        # Uses a non-empty dict that's missing the key (not {}) — an empty
        # dict is already falsy under the old buggy code's own `if
        # signal_context:` guard, so it never actually reached the bracket
        # access that raised KeyError and wouldn't have caught the original
        # bug. A non-empty dict missing "final_score" is truthy, so it DOES
        # reach (and previously broke) that access.
        payload = dict(self._VALID_PAYLOAD, recommendation="BUY", confidence="HIGH")

        ok, validated = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"some_other_field": 1}
        )
        self.assertTrue(ok)
        self.assertEqual(validated, payload)

    def test_validate_analysis_payload_rejects_buy_against_strong_negative_signals(self) -> None:
        payload = dict(self._VALID_PAYLOAD, recommendation="BUY")

        ok, message = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"final_score": -0.8}
        )
        self.assertFalse(ok)
        self.assertEqual(message, "Recommendation contradicts strong negative signals")

    def test_validate_analysis_payload_rejects_buy_at_the_exact_sell_tier_boundary(self) -> None:
        # Regression test for an adversarial-review finding: signals/engine.py's
        # own SELL tier is score <= -0.6 (inclusive — the `else` branch after
        # `score > -0.6` for AVOID), and final_score is rounded to 2 decimals,
        # so an exact -0.6 is a real, reachable value, not just a theoretical
        # edge. A strict `<` here let a BUY at exactly -0.6 slip through
        # untouched — the one boundary case this check exists to catch.
        payload = dict(self._VALID_PAYLOAD, recommendation="BUY")

        ok, message = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"final_score": -0.6}
        )
        self.assertFalse(ok)
        self.assertEqual(message, "Recommendation contradicts strong negative signals")

    def test_validate_analysis_payload_rejects_high_confidence_against_a_near_neutral_score(self) -> None:
        # Regression test for the deep gap analysis finding: the two checks
        # above only catch a *directional* contradiction (BUY/SELL vs. a
        # strongly opposite score) — neither says anything about confidence
        # *magnitude*. A "HIGH confidence" BUY/SELL against a score the
        # quant engine itself found almost nothing directional in (well
        # inside its own HOLD band) previously passed identical validation
        # to a HIGH-confidence call backed by a strong score.
        payload = dict(self._VALID_PAYLOAD, recommendation="BUY", confidence="HIGH")

        ok, message = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"final_score": 0.05}
        )
        self.assertFalse(ok)
        self.assertIn("near-neutral quant signal score", message)

    def test_validate_analysis_payload_allows_medium_confidence_against_a_near_neutral_score(self) -> None:
        # The check is specifically about confidence == HIGH — MEDIUM/LOW
        # calls against a marginal score are exactly what "not very
        # confident" should look like, not a violation.
        payload = dict(self._VALID_PAYLOAD, recommendation="BUY", confidence="MEDIUM")

        ok, _ = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"final_score": 0.05}
        )
        self.assertTrue(ok)

    def test_validate_analysis_payload_allows_high_confidence_against_a_strong_score(self) -> None:
        payload = dict(self._VALID_PAYLOAD, recommendation="BUY", confidence="HIGH")

        ok, _ = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"final_score": 0.7}
        )
        self.assertTrue(ok)

    def test_validate_analysis_payload_allows_hold_against_strong_negative_signals(self) -> None:
        # The guard is specifically BUY-vs-negative and SELL-vs-positive —
        # HOLD is never rejected by either direction of this check.
        payload = dict(self._VALID_PAYLOAD, recommendation="HOLD")

        ok, _ = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"final_score": -0.8}
        )
        self.assertTrue(ok)

    def test_run_analysis_passes_signal_context_to_guardrail(self) -> None:
        sell_payload = dict(self._VALID_PAYLOAD, recommendation="SELL")
        responses = [
            _llm_response(json.dumps(sell_payload)),
            _llm_response(json.dumps(self._VALID_PAYLOAD)),
        ]
        with patch("litellm.completion", side_effect=responses) as mock_completion:
            analysis = crew.run_analysis_with_fallback(
                "SAILIFE", self.all_data, signal_context={"final_score": 0.8}
            )

        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(analysis["recommendation"], "HOLD")
        second_messages = mock_completion.call_args_list[1].kwargs["messages"]
        self.assertIn("contradicts strong positive signals", second_messages[-1]["content"])

    def test_llm_exception_returns_safe_fallback(self) -> None:
        with patch("litellm.completion", side_effect=RuntimeError("boom")):
            analysis = crew.run_analysis_with_fallback("TCS", {name: {} for name in crew.ALL_DATA_TASKS})

        self.assertEqual(analysis["recommendation"], "HOLD")
        # The raw exception is logged server-side (see analyst_llm_failed),
        # not echoed into the client-facing comment.
        self.assertNotIn("boom", analysis["valuation"]["comment"])
        self.assertTrue(analysis["_degraded"])

    def test_records_cost_on_a_successful_call(self) -> None:
        with patch("litellm.completion", return_value=_llm_response(json.dumps(self._VALID_PAYLOAD))), \
             patch("llm_cost.estimate_cost_usd", return_value=0.0123), \
             patch("llm_cost.record_call_cost") as mock_record:
            crew.run_analysis_with_fallback("SAILIFE", self.all_data)

        mock_record.assert_called_once()
        self.assertEqual(mock_record.call_args.kwargs["symbol"], "SAILIFE")
        self.assertEqual(mock_record.call_args.kwargs["cost_usd"], 0.0123)

    def test_parse_ratio_number_handles_percent_comma_and_dash(self) -> None:
        self.assertEqual(crew._parse_ratio_number("18.5 %"), 18.5)
        self.assertEqual(crew._parse_ratio_number("1,234.5"), 1234.5)
        self.assertIsNone(crew._parse_ratio_number("-"))
        self.assertIsNone(crew._parse_ratio_number(""))
        self.assertIsNone(crew._parse_ratio_number(None))

    def test_analysis_numeric_issues_flags_dividend_yield_misread(self) -> None:
        # Reproduces the live QA bug: source dividend_yield_pct=0.46, analyst
        # prose cites "47%" (a ~100x misread).
        data = {
            "summary": "Solid company.",
            "business_quality": "Stable.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["The stock has a high dividend yield of 47%."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"dividend_yield_pct": 0.46}}
        issues = crew._analysis_numeric_issues(data, all_data)
        self.assertEqual(len(issues), 1)
        self.assertIn("dividend yield", issues[0])

    def test_analysis_numeric_issues_allows_within_tolerance_pe(self) -> None:
        data = {
            "summary": "",
            "business_quality": "Trading at a P/E of 25, close to fair value.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Reasonable valuation."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"pe_ratio": 24.8}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_skips_missing_source(self) -> None:
        data = {
            "summary": "",
            "business_quality": "ROE is 18%, well above peers.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Strong returns."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {}, "research": {"ratios": {}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_handles_ratios_none_without_raising(self) -> None:
        # Regression test: schemas._norm_research can produce "ratios": None
        # (not just a missing key), which previously crashed with AttributeError.
        data = {
            "summary": "",
            "business_quality": "ROE is 18%, well above peers.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Strong returns."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {}, "research": {"ratios": None}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_skips_uncited_field(self) -> None:
        data = {
            "summary": "No commentary on returns.",
            "business_quality": "",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Good management."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"ROE": "18.5 %"}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_skips_zero_source(self) -> None:
        data = {
            "summary": "",
            "business_quality": "",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["The dividend yield is 1.5%."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"dividend_yield_pct": 0}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_handles_comma_formatted_book_value(self) -> None:
        data = {
            "summary": "",
            "business_quality": "The book value is roughly ₹1,234 per share.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Solid asset base."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"book_value": 1234}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_flags_negative_growth_misread(self) -> None:
        # Regression: the old `cited < source/2 or cited > source*2` tolerance
        # inverted for negative source values (a -10% source made the "valid"
        # window [-5, -20], excluding -10 itself). A 5x misread must still be
        # caught, and a dead-on citation must NOT be flagged.
        data = {
            "summary": "",
            "business_quality": "Sales growth has been -50% over the period, a real slowdown.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Turnaround expected."],
            "bear_factors": ["Declining sales."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Sales growth 3Y": "-10 %", "Sales growth 5Y": "-8 %"}}}
        issues = crew._analysis_numeric_issues(data, all_data)
        self.assertEqual(len(issues), 1)
        self.assertIn("sales growth", issues[0])

    def test_analysis_numeric_issues_allows_correct_negative_citation(self) -> None:
        data = {
            "summary": "",
            "business_quality": "Sales growth has been -10% over the period.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Turnaround expected."],
            "bear_factors": ["Declining sales."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Sales growth 3Y": "-10 %", "Sales growth 5Y": "-8 %"}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_sales_growth_accepts_either_window(self) -> None:
        # Analyst prose rarely states 3Y vs 5Y explicitly — a citation close
        # to EITHER known window must pass, not just the first one checked.
        data = {
            "summary": "",
            "business_quality": "Sales growth of 22% shows strong momentum.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Strong growth."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Sales growth 3Y": "9 %", "Sales growth 5Y": "20 %"}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_flags_profit_growth_off_both_windows(self) -> None:
        data = {
            "summary": "",
            "business_quality": "Profit growth of 90% is exceptional.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Strong profit growth."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Profit growth 3Y": "9 %", "Profit growth 5Y": "12 %"}}}
        issues = crew._analysis_numeric_issues(data, all_data)
        self.assertEqual(len(issues), 1)
        self.assertIn("profit growth", issues[0])

    def test_analysis_numeric_issues_flags_ebitda_margin_misread(self) -> None:
        data = {
            "summary": "",
            "business_quality": "EBITDA margin of 60% is best-in-class.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["High margins."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"EBITDA margin": "15 %"}}}
        issues = crew._analysis_numeric_issues(data, all_data)
        self.assertEqual(len(issues), 1)
        self.assertIn("EBITDA margin", issues[0])

    def test_analysis_numeric_issues_flags_lakh_crore_market_cap_misread(self) -> None:
        data = {
            "summary": "",
            "business_quality": "The company's market cap stands at ₹1.5 lakh crore, among the largest in its sector.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Large-cap stability."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Market Cap": "13220"}}}
        issues = crew._analysis_numeric_issues(data, all_data)
        self.assertEqual(len(issues), 1)
        self.assertIn("market cap", issues[0])

    def test_analysis_numeric_issues_allows_within_tolerance_market_cap_in_crore(self) -> None:
        data = {
            "summary": "",
            "business_quality": "Market cap of ₹13,500 crore reflects steady growth.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Fairly valued."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Market Cap": "13220"}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_allows_within_tolerance_market_cap_in_usd_billion(self) -> None:
        data = {
            "summary": "",
            "business_quality": "Market cap of $1.6 billion makes it a mid-cap play.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Reasonably sized."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Market Cap": "13220"}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_skips_market_cap_without_unit_word(self) -> None:
        # Also a regression guard: "increase" contains the substring "cr" —
        # word-boundary matching must not treat that as a "crore" unit.
        data = {
            "summary": "",
            "business_quality": "Market cap continues to increase steadily this quarter.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Growing steadily."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Market Cap": "13220"}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_market_cap_ignores_cr_substring_in_words(self) -> None:
        # Regression: "increased" contains the substring "cr" — a naive
        # (non-word-boundary) unit match would treat that as the "crore"
        # abbreviation "cr" and parse the citation. With a real number
        # present in the window but no real unit word, the citation must
        # stay unparseable (None) and be skipped, not compared to source
        # (which is a very different number and would falsely flag).
        data = {
            "summary": "",
            "business_quality": "Market cap of 500 has increased steadily as revenue grew this quarter.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Growing steadily."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"Market Cap": "13220"}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_flags_fabricated_sector_average_pe(self) -> None:
        data = {
            "summary": "Sector average P/E is 60x, versus this stock's much lower multiple.",
            "business_quality": "",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Trades at a discount to sector."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"sector": "IT - Software"}}
        issues = crew._analysis_numeric_issues(data, all_data)
        self.assertEqual(len(issues), 1)
        self.assertIn("sector-average", issues[0])

    def test_analysis_numeric_issues_sector_pattern_ignores_pe_substring_in_words(self) -> None:
        # Regression: "expensive" contains the substring "pe" — unbounded
        # matching in _SECTOR_COMPARISON_PATTERN previously misread this as
        # a P/E citation. Real number present, no real "P/E"/"ROE" token.
        data = {
            "summary": "The stock looks expensive versus the sector average of 3.",
            "business_quality": "",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Strong fundamentals."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"sector": "IT - Software"}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_allows_plausible_sector_average_pe(self) -> None:
        data = {
            "summary": "",
            "business_quality": "The stock trades at a P/E of 22, above sector average of 18.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Premium to peers, justified by growth."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"sector": "IT - Software", "pe_ratio": 22}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_allows_negative_sector_average_roe_within_padding(self) -> None:
        data = {
            "summary": "ROE below sector average of -8%, reflecting a tough year for the segment.",
            "business_quality": "",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Turnaround expected."],
            "bear_factors": ["Weak segment-wide returns."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"sector": "Telecom"}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_skips_unmapped_sector(self) -> None:
        data = {
            "summary": "Sector average P/E is 3x, a huge discount.",
            "business_quality": "",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Cheap relative to peers."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"sector": "Miscellaneous Trading Co"}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_validate_analysis_payload_rejects_dividend_yield_misread(self) -> None:
        payload = dict(
            self._VALID_PAYLOAD,
            bull_factors=self._VALID_PAYLOAD["bull_factors"] + ["High dividend yield of 47%."],
        )
        all_data = dict(self.all_data)
        all_data["stock_info"] = dict(all_data.get("stock_info", {}), dividend_yield_pct=0.46)

        ok, message = crew._validate_analysis_payload(payload, all_data)
        self.assertFalse(ok)
        self.assertIn("dividend yield", message)


class CrossProviderFailoverTest(unittest.TestCase):
    """A full provider outage (not a formatting hiccup on an otherwise
    healthy provider) previously converged straight to the generic
    safe-HOLD fallback, indistinguishable from the fallback a working
    provider's guardrail failure also produces — see crew.py's own
    run_analysis_with_fallback docstring. These cover the failover path
    that now runs when a second provider's API key is also configured."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-llm-cost-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.addCleanup(isolated_state_store().close)

        # clear=False alone isn't enough for isolation: a real developer
        # .env can set LLM_PROVIDER (explicit pin disables failover per
        # crew._resolve_provider) or GROQ_API_KEY/GOOGLE_API_KEY/
        # OPENROUTER_API_KEY (widens _configured_providers() beyond the two
        # keys this test cares about) — either leaks through clear=False and
        # makes this test's outcome depend on whatever's in the real
        # environment. `crew._attempt_provider()` imports `litellm` lazily
        # (not at module load time), and litellm's own import triggers a
        # fresh `dotenv.load_dotenv()` call — so even after popping these
        # vars here, the *first* `run_analysis_with_fallback()` call in this
        # test (which is also, alphabetically, often the first anywhere in
        # the whole suite to reach that lazy import) can silently reload
        # them straight from the real .env file mid-test. Patching
        # `dotenv.load_dotenv` to a no-op closes that reload path outright,
        # on top of popping the already-loaded values below.
        self._dotenv_patch = patch("dotenv.load_dotenv")
        self._dotenv_patch.start()
        self.addCleanup(self._dotenv_patch.stop)

        self._env_patch = patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "fake-anthropic-key", "OPENAI_API_KEY": "fake-openai-key"},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        for var in ("LLM_PROVIDER", "GROQ_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
            if var in os.environ:
                self.addCleanup(os.environ.__setitem__, var, os.environ[var])
                del os.environ[var]

        self.all_data = {name: {} for name in crew.ALL_DATA_TASKS}

    _VALID_PAYLOAD = AnalysisGuardrailFallbackTest._VALID_PAYLOAD

    def test_primary_provider_exception_fails_over_to_the_second_configured_provider(self) -> None:
        responses = [RuntimeError("anthropic is down"), _llm_response(json.dumps(self._VALID_PAYLOAD))]
        with patch("litellm.completion", side_effect=responses) as mock_completion:
            analysis = crew.run_analysis_with_fallback("SAILIFE", self.all_data)

        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(analysis["recommendation"], self._VALID_PAYLOAD["recommendation"])
        self.assertNotIn("_degraded", analysis)  # a genuine successful call, not the safe fallback

    def test_both_providers_failing_still_falls_back_safely(self) -> None:
        with patch("litellm.completion", side_effect=RuntimeError("every provider is down")) as mock_completion:
            analysis = crew.run_analysis_with_fallback("TCS", self.all_data)

        self.assertEqual(mock_completion.call_count, 2)  # one attempt per configured provider
        self.assertEqual(analysis["recommendation"], "HOLD")
        self.assertTrue(analysis["_degraded"])

    def test_only_one_configured_provider_never_attempts_failover(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)  # patch.dict restores it on exit
            with patch("litellm.completion", side_effect=RuntimeError("boom")) as mock_completion:
                crew.run_analysis_with_fallback("TCS", self.all_data)
        self.assertEqual(mock_completion.call_count, 1)

    def test_configured_providers_reflects_every_key_present(self) -> None:
        self.assertEqual(set(crew._configured_providers()), {"anthropic", "openai"})

    def test_fallback_provider_uses_its_own_default_model_not_analyst_model_override(self) -> None:
        # ANALYST_MODEL is only meant for the primary provider — reusing it
        # for a failover attempt against a *different* provider would very
        # likely be an invalid model string for that provider.
        with patch.dict("os.environ", {"ANALYST_MODEL": "claude-some-specific-snapshot"}, clear=False):
            model, _key = crew._resolve_model_and_key("openai", is_primary=False)
        self.assertEqual(model, crew._ANALYST_DEFAULTS["openai"])

    def test_explicit_llm_provider_pin_disables_failover_even_with_a_second_key_present(self) -> None:
        # Regression test: an operator setting LLM_PROVIDER (e.g. to pin a
        # local-only Ollama deployment for data residency) is a deliberate
        # single-provider choice, not "whichever key happened to be
        # configured first." A stray second key left in the same
        # environment for an unrelated reason must not cause this
        # analysis's fetched data to silently be sent to that other
        # provider on a transient failure of the pinned one.
        with patch.dict("os.environ", {"LLM_PROVIDER": "anthropic"}, clear=False):
            with patch("litellm.completion", side_effect=RuntimeError("anthropic is down")) as mock_completion:
                analysis = crew.run_analysis_with_fallback("TCS", self.all_data)

        self.assertEqual(mock_completion.call_count, 1)  # no failover attempt at all
        self.assertTrue(analysis["_degraded"])


if __name__ == "__main__":
    unittest.main()
