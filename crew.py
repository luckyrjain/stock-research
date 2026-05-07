"""Crew construction and analyst guardrails for stock research."""
# pylint: disable=line-too-long

import json
import os
import ast
import re
import time
from typing import Any, Tuple

from crewai import Agent, Task, Crew, Process, LLM
from config.crew_agents import AGENTS_FOR_TASK, BACKSTORIES
from config.crew_tasks import (
    ANALYST_DESCRIPTION_SUFFIX,
    ANALYST_SECTIONS,
    build_analysis_prompt,
    build_task_specs,
)
from config.crew_tasks import _analyst_cfg as _ANALYST_CFG
from schemas import validate as schema_validate
from observability import get_logger, log_event

LOGGER = get_logger("crew")

_DEFAULTS = {
    "anthropic":   "claude-haiku-4-5-20251001",
    "openai":      "gpt-4o-mini",
    "groq":        "groq/llama-3.1-8b-instant",
    "google":      "gemini/gemini-2.5-flash",
    "ollama":      "ollama/llama3.2",
    "openrouter":  "openrouter/meta-llama/llama-3.1-8b-instruct",
}
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


# ── LLM resolution ───────────────────────────────────────────────────────────

def _resolve_llm(analyst: bool = False) -> LLM:
    provider = os.getenv("LLM_PROVIDER", "").lower()
    if not provider:
        # Auto-detect from whichever API key is present
        for p, env in _API_KEY_ENV.items():
            if os.getenv(env):
                provider = p
                break
        if not provider:
            raise EnvironmentError(
                "No API key found. Set one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                "GROQ_API_KEY, GOOGLE_API_KEY, OPENROUTER_API_KEY — or set LLM_PROVIDER=ollama for local."
            )

    defaults = _ANALYST_DEFAULTS if analyst else _DEFAULTS
    env_var  = "ANALYST_MODEL" if analyst else "LLM_MODEL"
    model    = os.getenv(env_var, defaults.get(provider, ""))

    if not model:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{provider}'. "
            "Supported: anthropic, openai, groq, google, ollama, openrouter."
        )

    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return LLM(model=model, base_url=base_url)

    api_key = os.getenv(_API_KEY_ENV.get(provider, ""), "")
    return LLM(model=model, api_key=api_key or None)


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


def _guard_data(task_name: str):
    """
    Factory returning a guardrail for data-fetching tasks.
    Delegates field validation to schemas.validate() — no field names hardcoded here.
    """
    def guard(output) -> Tuple[bool, Any]:
        raw = output.raw if hasattr(output, "raw") else str(output)
        data = parse_json_object(raw)
        if data is None:
            return False, (
                "Your response must be a valid JSON object only — no markdown, no prose. "
                "Return the exact JSON string from the tool."
            )
        ok, err = schema_validate(task_name, data)
        if not ok:
            return False, f"{err}. Return the complete JSON from the tool without modification."
        return True, output
    return guard


def _source_text(all_data: dict[str, dict]) -> str:
    stock_info = all_data.get("stock_info", {}) or {}
    research = all_data.get("research", {}) or {}
    news = all_data.get("news", {}) or {}

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
            # downgrade instead of fail
            continue

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


def _safe_analysis_fallback(symbol: str, reason: str) -> dict:
    return {
        "symbol": symbol,
        "recommendation": "HOLD",
        "confidence": "LOW",
        "summary": (
            f"Automated analysis for {symbol} could not be fully structured because the analyst model "
            f"returned an invalid format. A neutral HOLD fallback was used while preserving fetched market data."
        ),
        "valuation": {
            "verdict": "Fairly Valued",
            "comment": f"Structured valuation output was unavailable due to analyst formatting failure: {reason}",
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


# pylint: disable=too-many-locals
def run_analysis_with_fallback(
    symbol: str,
    all_data: dict[str, dict],
    signal_context=None,
    run_id: str | None = None,
) -> dict:
    """Run analyst via direct LLM call. Retries once after waiting if rate-limited."""
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
    provider = os.getenv("LLM_PROVIDER", "").lower()
    if not provider:
        for p, env in _API_KEY_ENV.items():
            if os.getenv(env):
                provider = p
                break
    model   = os.getenv("ANALYST_MODEL", _ANALYST_DEFAULTS.get(provider, "claude-sonnet-4-6"))
    api_key = os.getenv(_API_KEY_ENV.get(provider, ""), "") or None

    import litellm

    for attempt in range(2):
        try:
            started_at = time.perf_counter()
            log_event(LOGGER, "analyst_llm_started", run_id=run_id, symbol=symbol, attempt=attempt + 1)
            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
            )
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            text = response.choices[0].message.content or ""
            parsed = parse_json_object(text)
            ok, validated = _validate_analysis_payload(parsed, all_data)
            if ok:
                log_event(LOGGER, "analyst_llm_succeeded", run_id=run_id, symbol=symbol, latency_ms=elapsed_ms)
                return parsed

            log_event(
                LOGGER, "analyst_llm_failed", level="warning",
                run_id=run_id, symbol=symbol, latency_ms=elapsed_ms,
                error=str(validated), failure_stage="guardrail",
            )
            return _safe_analysis_fallback(symbol, str(validated))

        except Exception as exc:  # pylint: disable=broad-exception-caught
            if _is_rate_limit(exc) and attempt == 0:
                wait = _rate_limit_wait_secs(exc)
                log_event(
                    LOGGER, "analyst_rate_limited", level="warning",
                    run_id=run_id, symbol=symbol, wait_seconds=wait, error=str(exc),
                )
                time.sleep(wait)
                continue

            log_event(
                LOGGER, "analyst_llm_failed", level="error",
                run_id=run_id, symbol=symbol, error=str(exc), failure_stage="exception",
            )
            return _safe_analysis_fallback(symbol, str(exc))

    return _safe_analysis_fallback(symbol, "analyst failed after rate-limit retry")
# pylint: enable=too-many-locals


# ── Main builder ──────────────────────────────────────────────────────────────

def build_crew(
    symbol: str,
    active_tasks: set[str] | None = None,
    cached_data: dict[str, dict] | None = None,
    run_analysis: bool = True,
) -> Crew:
    # pylint: disable=too-many-locals
    """
    Build the research crew for *symbol*.

    active_tasks  – names of data tasks to actually run (None = all five).
    cached_data   – pre-loaded results for tasks NOT in active_tasks; these are
                    inlined into the analyst description so the LLM sees them.
    run_analysis  – whether to append the analyst task.

    Note:
    The production fetch path does not rely on CrewAI for concurrency. Data
    collection happens in Python threads in main.py / api.py, and this Crew is
    retained primarily for the analyst step plus compatibility with any direct
    callers that still use build_crew().
    """
    if active_tasks is None:
        active_tasks = set(ALL_DATA_TASKS)
    cached_data = cached_data or {}

    llm = _resolve_llm(analyst=False)
    analyst_llm = _resolve_llm(analyst=True)

    # Build one Agent per data task (always — even for cached tasks the agent
    # object is needed in the Crew's agent list).
    agents: dict[str, Agent] = {}
    for name, (role, tools) in AGENTS_FOR_TASK.items():
        agents[name] = Agent(
            role=role,
            goal=f"Complete the {name} data-fetching task for {symbol}",
            backstory=BACKSTORIES[role],
            tools=tools,
            llm=llm,
            verbose=False,
        )

    _a = _ANALYST_CFG["agent"]
    analyst_agent = Agent(
        role=_a["role"],
        goal=_a["goal"],
        backstory=_a["backstory"],
        tools=[],
        llm=analyst_llm,
        verbose=False,
    )

    # ── Data tasks (only active ones are added to the crew) ──────────────
    specs = build_task_specs(symbol, _guard_data)
    active_list = [n for n in ALL_DATA_TASKS if n in active_tasks]
    tasks: list[Task] = []
    task_by_name: dict[str, Task] = {}

    for name in active_list:
        # CrewAI is not the performance-critical fetch path in this app.
        # Actual parallel data collection is handled explicitly with Python
        # threads before the analyst step runs.
        t = Task(agent=agents[name], async_execution=False, **specs[name])
        tasks.append(t)
        task_by_name[name] = t

    # ── Analyst task ─────────────────────────────────────────────────────
    if run_analysis:
        context_tasks: list[Task] = []
        inline_parts: list[str] = []

        for name, label in ANALYST_SECTIONS.items():
            if name in cached_data:
                clean = {k: v for k, v in cached_data[name].items() if k != "_meta"}
                inline_parts.append(f"### {label}\n{json.dumps(clean, indent=2)}")
            elif name in task_by_name:
                context_tasks.append(task_by_name[name])
                # Placeholder so the analyst knows this section will be in context
                inline_parts.append(f"### {label}\n[provided via agent context]")

        analyst_desc = (
            f"You have been given all available data on the NSE-listed stock {symbol}.\n\n"
            + "\n\n".join(inline_parts)
        )

        task_kwargs: dict = {
            "description": analyst_desc,
            "expected_output": _ANALYST_CFG["expected_output"],
            "agent": analyst_agent,
            "guardrail": _guard_analysis(cached_data),
            "max_retries": 3,
            "async_execution": False,
        }
        if context_tasks:
            task_kwargs["context"] = context_tasks

        tasks.append(Task(**task_kwargs))

    return Crew(
        agents=list(agents.values()) + [analyst_agent],
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
    )
