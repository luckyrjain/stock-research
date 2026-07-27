import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
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
import llm_cost


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
        # crew.py now records LLM cost on every completion() call via
        # llm_cost.py — redirect its file writes to a scratch directory so
        # this test file never touches the real repo's output/ tree, same
        # convention as every other stateful module's own test file.
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-llm-cost-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cost_dir_patch = patch.object(llm_cost, "_COST_DIR", Path(self._tmpdir))
        self._cost_dir_patch.start()
        self.addCleanup(self._cost_dir_patch.stop)

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
        self.assertEqual(message, "Field 'bull_factors' must contain at least 3 items.")

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

    def test_parse_json_object_extracts_balanced_payload_from_wrapper_text(self) -> None:
        raw = """
        tool log: starting analysis
        {"symbol": "TCS", "summary": "Plain payload", "details": {"pe": 20}}
        tool log: done
        """

        parsed = crew.parse_json_object(raw)
        self.assertEqual(parsed["symbol"], "TCS")
        self.assertEqual(parsed["details"]["pe"], 20)

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

    def test_validate_analysis_payload_rejects_buy_against_strong_negative_signals(self) -> None:
        payload = dict(self._VALID_PAYLOAD, recommendation="BUY")

        ok, message = crew._validate_analysis_payload(
            payload, self.all_data, signal_context={"final_score": -0.8}
        )
        self.assertFalse(ok)
        self.assertEqual(message, "Recommendation contradicts strong negative signals")

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
        self._cost_dir_patch = patch.object(llm_cost, "_COST_DIR", Path(self._tmpdir))
        self._cost_dir_patch.start()
        self.addCleanup(self._cost_dir_patch.stop)

        self._env_patch = patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "fake-anthropic-key", "OPENAI_API_KEY": "fake-openai-key"},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

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
