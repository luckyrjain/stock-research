"""Crew construction and analyst guardrails for stock research."""
# pylint: disable=line-too-long

import json
import os
import ast
import re
import time
from typing import Any, Tuple

from config.crew_tasks import build_analysis_prompt
from observability import get_logger, log_event

LOGGER = get_logger("crew")

_ANALYST_DEFAULTS = {
    "anthropic":   "claude-sonnet-4-6",
    "openai":      "gpt-4o",
    "groq":        "groq/llama-3.3-70b-versatile",
    "google":      "gemini/gemini-2.5-flash",
    "ollama":      "ollama/llama3.1:8b",
    "openrouter":  "openrouter/meta-llama/llama-3.3-70b-instruct",
}

_API_KEY_ENV = {
    "anthropic":  "ANTHROPIC_API_KEY",
    "openai":     "OPENAI_API_KEY",
    "groq":       "GROQ_API_KEY",
    "google":     "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# Canonical order used for indexing task outputs
ALL_DATA_TASKS = ("stock_info", "research", "news", "shareholding", "mf_holdings", "filings")


# ── Guardrails ────────────────────────────────────────────────────────────────

def _unwrap_json(raw: str) -> str:
    """Extract the first balanced JSON object from raw text, handling markdown fences."""

    def _extract_balanced_object(text: str) -> str | None:
        start = text.find("{")
        while start != -1:
            depth = 0
            in_string = False
            escape = False

            for idx in range(start, len(text)):
                char = text[idx]

                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue

                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start:idx + 1]

            start = text.find("{", start + 1)

        return None

    # Try markdown fence first so we prefer the intended payload when present.
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if fence:
        fenced = _extract_balanced_object(fence.group(1))
        if fenced:
            return fenced

    extracted = _extract_balanced_object(raw)
    return extracted if extracted else raw


def parse_json_object(raw: str) -> dict | None:
    """
    Best-effort parser for LLM JSON output.
    Accepts strict JSON first, then repairs a few common model mistakes such as
    Python-style dict literals and trailing commas.
    """
    text = _unwrap_json(raw).strip()
    if not text:
        return None

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    # Common LLM mistake: trailing commas before } or ]
    cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    # Fallback for Python-style dict output using single quotes / True / False / None
    try:
        data = ast.literal_eval(cleaned)
        return data if isinstance(data, dict) else None
    except (ValueError, SyntaxError):
        return None


def _source_text(all_data: dict[str, dict]) -> str:
    stock_info = all_data.get("stock_info", {}) or {}
    research = all_data.get("research", {}) or {}
    news = all_data.get("news", {}) or {}
    filings = all_data.get("filings", {}) or {}

    text_parts = [
        str(stock_info.get("about", "")),
        str(research.get("about", "")),
        str(stock_info.get("sector", "")),
        str(stock_info.get("industry", "")),
    ]

    for article in news.get("articles", []) or []:
        if not isinstance(article, dict):
            continue
        text_parts.append(str(article.get("title", "")))
        text_parts.append(str(article.get("description", "")))

    # Filings are now part of the analyst prompt (see ANALYST_SECTIONS in
    # config/analyst.json) and its instructions explicitly tell the analyst
    # to cite material filings (regulatory action, litigation, etc.) as risk
    # evidence — without this, a legitimate filings-grounded claim would be
    # flagged as an unsupported benchmark/regulatory/competition claim below,
    # since this function is what "supported by source data" checks against.
    for filing in filings.get("filings", []) or []:
        if not isinstance(filing, dict):
            continue
        text_parts.append(str(filing.get("title", "")))
        text_parts.append(str(filing.get("desc", "")))
        text_parts.append(str(filing.get("category", "")))

    return " ".join(part for part in text_parts if part).lower()


def _analysis_support_issues(data: dict | None, all_data: dict[str, dict] | None) -> list[str]:
    if data is None or not all_data:
        return []

    issues: list[str] = []
    source_text = _source_text(all_data)
    shareholding = (all_data.get("shareholding", {}) or {}).get("shareholding_pattern", {}) or {}
    has_single_snapshot = bool(shareholding) and isinstance(shareholding, dict)

    institutional_trend = str(data.get("institutional_trend", "")).lower()
    if has_single_snapshot and re.search(r"\b(rising|falling|increasing|decreasing|improving|declining|uptrend|downtrend)\b", institutional_trend):
        issues.append("institutional_trend inferred direction from a single shareholding snapshot")

    benchmark_fields = " ".join(
        [
            str(data.get("summary", "")),
            str((data.get("valuation") or {}).get("comment", "")),
            " ".join(str(item) for item in data.get("bull_factors", []) or []),
            " ".join(str(item) for item in data.get("bear_factors", []) or []),
        ]
    ).lower()
    if re.search(r"\b(sector average|peer average|industry average|benchmark|peers)\b", benchmark_fields):
        issues.append("analysis referenced external benchmarks or peers that were not provided")

    grounded_checks = [
        (
            "regulatory risk",
            ("regulatory", "regulation", "regulator", "rbi", "usfda", "fda"),
            ("regulatory", "regulation", "regulator", "rbi", "usfda", "fda"),
        ),
        (
            "customer concentration risk",
            ("major client", "few large clients", "customer concentration", "client concentration"),
            ("client", "customer", "concentration"),
        ),
        (
            "competition risk",
            ("global players", "pricing power", "market share", "intensifying competition"),
            ("competition", "competitive", "market share", "pricing power"),
        ),
    ]
    analysis_text = " ".join(
        [
            str(data.get("summary", "")),
            str(data.get("business_quality", "")),
            str(data.get("news_highlights", "")),
            str(data.get("institutional_trend", "")),
            " ".join(str(item) for item in data.get("bull_factors", []) or []),
            " ".join(str(item) for item in data.get("bear_factors", []) or []),
            " ".join(str(item) for item in data.get("key_risks", []) or []),
        ]
    ).lower()
    for label, trigger_phrases, source_terms in grounded_checks:
        if any(phrase in analysis_text for phrase in trigger_phrases) and not any(term in source_text for term in source_terms):
            issues.append(f"{label} claim is not supported by the provided source data")

    return issues


def _validate_analysis_payload(  # pylint: disable=too-many-return-statements
    data: dict | None,
    all_data: dict[str, dict] | None = None,
    signal_context=None,
) -> Tuple[bool, Any]:
    if data is None:
        return False, (
            "Your response must be a single valid JSON object with no surrounding text or markdown."
        )
    if data.get("recommendation") not in {"BUY", "SELL", "HOLD"}:
        return False, "Field 'recommendation' must be exactly 'BUY', 'SELL', or 'HOLD'."
    if data.get("confidence") not in {"HIGH", "MEDIUM", "LOW"}:
        return False, "Field 'confidence' must be exactly 'HIGH', 'MEDIUM', or 'LOW'."
    for field in ("summary", "business_quality", "bull_factors", "bear_factors",
                  "key_risks", "news_highlights", "institutional_trend"):
        if not data.get(field):
            return False, f"Field '{field}' is required and cannot be empty."

    if len(data.get("bull_factors", [])) < 3:
        return False, "Field 'bull_factors' must contain at least 3 items."

    if len(data.get("bear_factors", [])) < 2:
        return False, "Field 'bear_factors' must contain at least 2 items."

    if len(data.get("key_risks", [])) < 3:
        return False, "Field 'key_risks' must contain at least 3 items."
    if signal_context:
        if signal_context["final_score"] > 0.5 and data["recommendation"] == "SELL":
            return False, "Recommendation contradicts strong positive signals"
    support_issues = _analysis_support_issues(data, all_data)
    if support_issues:
        return False, f"Unsupported claims found: {'; '.join(support_issues)}."
    return True, data


def _guard_analysis(all_data: dict[str, dict] | None = None):
    """Validates the analyst JSON: correct enum values and required fields."""
    def guard(output) -> Tuple[bool, Any]:
        raw = output.raw if hasattr(output, "raw") else str(output)
        data = parse_json_object(raw)
        return _validate_analysis_payload(data, all_data)
    return guard


def _safe_analysis_fallback(symbol: str, reason: str) -> dict:  # pylint: disable=unused-argument
    # `reason` is already logged server-side (see analyst_llm_failed events) with the
    # run_id for correlation — it's deliberately not echoed into this client-facing
    # payload, since it can carry raw provider/exception text.
    return {
        "symbol": symbol,
        "recommendation": "HOLD",
        "confidence": "LOW",
        "_degraded": True,
        "summary": (
            f"Automated analysis for {symbol} could not be fully structured because the analyst model "
            f"returned an invalid format. A neutral HOLD fallback was used while preserving fetched market data."
        ),
        "valuation": {
            "verdict": "Fairly Valued",
            "comment": "Structured valuation output was unavailable due to an analyst formatting failure.",
        },
        "business_quality": "Structured business-quality commentary was unavailable from the analyst model.",
        "bull_factors": [
            "Market data was fetched successfully.",
            "Fundamental and ownership datasets remain available for manual review.",
            "The pipeline preserved the underlying stock data despite analysis formatting failure.",
        ],
        "bear_factors": [
            "The analyst model did not return a valid JSON object.",
            "Recommendation confidence is reduced because structured reasoning could not be recovered.",
        ],
        "key_risks": [
            "Analyst formatting failure prevented a fully structured recommendation.",
            "Qualitative conclusions may be incomplete until the analysis is rerun successfully.",
            "Manual review is advised before acting on the fallback recommendation.",
        ],
        "news_sentiment": "Neutral",
        "news_highlights": "News analysis was unavailable because the analyst response format could not be parsed.",
        "institutional_trend": "Institutional trend commentary was unavailable because the analyst response format could not be parsed.",
    }


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "rate_limit" in msg or "ratelimit" in msg or "rate limit" in msg


def _rate_limit_wait_secs(exc: Exception) -> float:
    # Groq: "try again in 34.86s"
    m = re.search(r"try again in (\d+\.?\d*)s", str(exc), re.IGNORECASE)
    if m:
        return float(m.group(1)) + 2.0
    # Gemini: "Retry after X seconds" or retryDelay "Xs"
    m2 = re.search(r"retry.{0,10}?(\d+\.?\d*)\s*s", str(exc), re.IGNORECASE)
    if m2:
        return float(m2.group(1)) + 2.0
    return 60.0


def _call_direct_llm(analyst_llm, prompt: str):
    if hasattr(analyst_llm, "call"):
        return analyst_llm.call(prompt)
    if hasattr(analyst_llm, "invoke"):
        return analyst_llm.invoke(prompt)
    if callable(analyst_llm):
        return analyst_llm(prompt)
    return None


def _resolve_provider() -> str:
    """LLM_PROVIDER wins if explicitly set; otherwise the first provider
    (in _API_KEY_ENV's declared order) with a usable API key."""
    provider = os.getenv("LLM_PROVIDER", "").lower()
    if provider:
        return provider
    for p, env in _API_KEY_ENV.items():
        if os.getenv(env):
            return p
    return ""


def _configured_providers() -> list[str]:
    """Every provider with a usable API key, in _API_KEY_ENV's declared
    order — used to pick a cross-provider failover candidate below."""
    return [p for p, env in _API_KEY_ENV.items() if os.getenv(env)]


def _resolve_model_and_key(provider: str, is_primary: bool) -> tuple[str, str | None]:
    if is_primary:
        # ANALYST_MODEL only ever applies to the primary provider — it's a
        # model string for one specific provider, and blindly reusing it
        # for a *different* provider's failover attempt would very likely
        # be an invalid model string for that provider. The failover
        # attempt always uses that provider's own documented default.
        model = os.getenv("ANALYST_MODEL", _ANALYST_DEFAULTS.get(provider, "claude-sonnet-4-6"))
    else:
        model = _ANALYST_DEFAULTS.get(provider, "claude-sonnet-4-6")
    api_key = os.getenv(_API_KEY_ENV.get(provider, ""), "") or None
    return model, api_key


def _attempt_provider(
    provider: str, model: str, api_key: str | None, prompt: str,
    all_data: dict, signal_context, symbol: str, run_id: str | None, is_failover: bool,
) -> Tuple[dict | None, str | None]:
    """One provider's full attempt — its own guardrail retry (once) and
    rate-limit retry (once), exactly the same shape run_analysis_with_fallback
    used to run inline before cross-provider failover existed. Extracted so
    the outer function can run this against a second, differently-configured
    provider without duplicating the retry logic. Returns (result, error) —
    exactly one of the two is non-None."""
    import litellm

    messages: list[dict] = [{"role": "user", "content": prompt}]
    rate_limit_retry_used = False
    guardrail_retry_used = False

    while True:
        try:
            started_at = time.perf_counter()
            log_event(LOGGER, "analyst_llm_started", run_id=run_id, symbol=symbol,
                      provider=provider, model=model, is_failover=is_failover,
                      guardrail_retry=guardrail_retry_used)
            response = litellm.completion(
                model=model,
                messages=messages,
                api_key=api_key,
            )
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)

            # Cost is recorded for every completion call, not just the one
            # that ultimately validates — a guardrail-retry or a failover
            # attempt that later fails still spent real tokens. Never lets
            # a broken cost tracker affect the analysis itself (see
            # llm_cost.py's own "never raise" convention).
            import llm_cost
            usage = getattr(response, "usage", None)
            llm_cost.record_call_cost(
                symbol=symbol, model=model, provider=provider,
                cost_usd=llm_cost.estimate_cost_usd(response, model),
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                run_id=run_id,
            )

            text = response.choices[0].message.content or ""
            parsed = parse_json_object(text)
            ok, validated = _validate_analysis_payload(parsed, all_data, signal_context)
            if ok:
                log_event(LOGGER, "analyst_llm_succeeded", run_id=run_id, symbol=symbol,
                          provider=provider, model=model, latency_ms=elapsed_ms)
                return parsed, None

            if not guardrail_retry_used:
                guardrail_retry_used = True
                log_event(
                    LOGGER, "analyst_guardrail_retry", level="warning",
                    run_id=run_id, symbol=symbol, provider=provider, latency_ms=elapsed_ms, error=str(validated),
                )
                messages = [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        f"Your previous response failed validation: {validated} "
                        "Return only the corrected JSON object — no markdown, no prose."
                    )},
                ]
                continue

            error = str(validated)
            log_event(
                LOGGER, "analyst_provider_failed", level="warning",
                run_id=run_id, symbol=symbol, provider=provider, latency_ms=elapsed_ms,
                error=error, failure_stage="guardrail",
            )
            return None, error

        except Exception as exc:  # pylint: disable=broad-exception-caught
            if _is_rate_limit(exc) and not rate_limit_retry_used:
                rate_limit_retry_used = True
                wait = _rate_limit_wait_secs(exc)
                log_event(
                    LOGGER, "analyst_rate_limited", level="warning",
                    run_id=run_id, symbol=symbol, provider=provider, wait_seconds=wait, error=str(exc),
                )
                time.sleep(wait)
                continue

            error = str(exc)
            log_event(
                LOGGER, "analyst_provider_failed", level="warning",
                run_id=run_id, symbol=symbol, provider=provider, error=error, failure_stage="exception",
            )
            return None, error


def run_analysis_with_fallback(
    symbol: str,
    all_data: dict[str, dict],
    signal_context=None,
    run_id: str | None = None,
) -> dict:
    """Run analyst via direct LLM call. Retries once after waiting if
    rate-limited, once more after a guardrail validation failure — and, new
    here, once more against a second configured provider if one exists.

    Previously a full provider outage (not a formatting hiccup — the
    provider's API itself unreachable, rate-limited past the single retry,
    or erroring outright) converged straight to the generic safe-HOLD
    fallback, indistinguishable from the fallback a formatting failure on a
    perfectly healthy provider also produces. If this deployment has more
    than one provider's API key configured (see .env.example — most
    deployments set exactly one, but nothing stops setting two), the second
    one gets exactly one full attempt (its own guardrail/rate-limit retries
    included) before falling through to the safe fallback. With only one
    key configured — the common case — this is a no-op: behavior is
    unchanged from before failover existed.
    """
    prompt = build_analysis_prompt(symbol, all_data)

    if signal_context:
       prompt += f"""

        =====================
        QUANT SIGNALS (REFERENCE ONLY)
        =====================
        {json.dumps(signal_context, indent=2)}

        Rules:
        - Use signals only to support reasoning
        - Do NOT change output schema
        - Do NOT introduce new fields
        """

    # Resolve model + key without going through CrewAI's LLM wrapper
    # (avoids optional native provider imports like google-genai)
    primary_provider = _resolve_provider()
    fallback_provider = next(
        (p for p in _configured_providers() if p != primary_provider), None,
    )
    providers_to_try = [primary_provider] + ([fallback_provider] if fallback_provider else [])

    last_error: str | None = None
    for i, provider in enumerate(providers_to_try):
        model, api_key = _resolve_model_and_key(provider, is_primary=(i == 0))
        result, error = _attempt_provider(
            provider, model, api_key, prompt, all_data, signal_context, symbol, run_id,
            is_failover=(i > 0),
        )
        if result is not None:
            if i > 0:
                log_event(
                    LOGGER, "analyst_provider_failover_succeeded", run_id=run_id, symbol=symbol,
                    failed_provider=providers_to_try[0], succeeded_provider=provider,
                )
            return result
        last_error = error

    log_event(
        LOGGER, "analyst_llm_failed", level="error",
        run_id=run_id, symbol=symbol, error=str(last_error),
        failure_stage="all_providers_exhausted", providers_tried=providers_to_try,
    )
    return _safe_analysis_fallback(symbol, str(last_error))
