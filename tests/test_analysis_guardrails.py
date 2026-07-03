import json
import unittest
import sys
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
        self.assertIn("bull_factors", analysis["valuation"]["comment"])

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

    def test_llm_exception_returns_safe_fallback(self) -> None:
        with patch("litellm.completion", side_effect=RuntimeError("boom")):
            analysis = crew.run_analysis_with_fallback("TCS", {name: {} for name in crew.ALL_DATA_TASKS})

        self.assertEqual(analysis["recommendation"], "HOLD")
        self.assertIn("boom", analysis["valuation"]["comment"])


if __name__ == "__main__":
    unittest.main()
