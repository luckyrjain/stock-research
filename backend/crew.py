"""Crew construction and analyst guardrails for stock research."""
# pylint: disable=line-too-long

import json
import os
import ast
import re
import threading
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
        # Collects EVERY top-level balanced {...} object in the text and
        # returns the LAST one, not the first. A verbose/reasoning-style
        # completion can legitimately contain an earlier, small JSON-like
        # fragment before its real structured answer (e.g. "I'll use
        # {\"P/E\": 20} as context. Final answer: {...full payload...}") --
        # returning the first-found object there would silently discard the
        # actual response and hand the guardrail a fragment missing every
        # required field, burning the one guardrail retry for nothing even
        # though the model's real answer was fully valid. The common case
        # (exactly one JSON object in the text) is unaffected either way.
        candidates: list[str] = []
        start = text.find("{")
        while start != -1:
            depth = 0
            in_string = False
            escape = False
            end = None

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
                        end = idx
                        break

            if end is not None:
                candidates.append(text[start:end + 1])
                start = text.find("{", end + 1)
            else:
                start = text.find("{", start + 1)

        return candidates[-1] if candidates else None

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

    # Each entry is (label, trigger_phrases, source_terms): trigger_phrases
    # are the specific risk-*assertion* wording the LLM would actually write
    # when making this kind of claim (e.g. "faces regulatory scrutiny"),
    # deliberately more specific than source_terms — the broader topic
    # words that count as this claim having *some* grounding in the
    # fetched data. The regulatory-risk entry previously reused the exact
    # same six-word list for both roles (a copy-paste bug, unlike the
    # customer-concentration/competition entries below, which were always
    # written with genuinely distinct trigger vs. source lists) — that made
    # it trivially satisfied by routine "Regulation 30" SEBI-disclosure
    # boilerplate appearing anywhere in a stock's filings/news, which says
    # nothing about whether the LLM's specific asserted risk (e.g. "facing
    # intensifying FDA scrutiny") is actually evidenced. Fixed to match the
    # sibling entries' pattern.
    #
    # Disclosed limitation shared by all three checks: this is still
    # keyword/phrase matching, not real claim verification — it can't
    # confirm the SPECIFIC substance of a claim (a named agency, client, or
    # competitor) is what the source text actually supports, only that the
    # general topic isn't completely absent from it. Same "approximate
    # heuristic, not a claim-verification model" instinct as this
    # codebase's other keyword-based classifiers (e.g.
    # signals/filings_classifier.py).
    grounded_checks = [
        (
            "regulatory risk",
            ("regulatory risk", "regulatory scrutiny", "regulatory action", "regulatory change",
             "regulatory hurdle", "regulatory challenge", "regulatory headwind", "faces regulation",
             "compliance risk", "under investigation"),
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


def _parse_ratio_number(value: Any) -> float | None:
    """Parses a scraped ratio string (e.g. '18.5 %', '1,234.5', '-') into a float."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first_cited_number(text: str, field_pattern: str) -> float | None:
    match = re.search(field_pattern, text)
    if not match:
        return None
    window = text[match.end(): match.end() + 20].replace(",", "")
    number_match = re.search(r"-?\d+\.?\d*", window)
    if not number_match:
        return None
    try:
        return float(number_match.group(0))
    except ValueError:
        return None


def _ratio(ad: dict, key: str) -> float | None:
    return _parse_ratio_number(((ad.get("research", {}) or {}).get("ratios") or {}).get(key))


def _in_tolerance(cited: float, source: float) -> bool:
    """Sign-agnostic 2x-tolerance check. min/max instead of source/2..source*2
    directly — for negative source (e.g. -10% growth), source/2=-5 is the
    upper bound and source*2=-20 is the lower bound, the reverse of the
    positive case, so a plain range would reject even an exact citation."""
    lo, hi = min(source / 2, source * 2), max(source / 2, source * 2)
    return lo <= cited <= hi


_CR_PER_LAKH_CRORE = 100_000.0
_INR_PER_USD = 83.0  # fixed approximate rate — 2x tolerance absorbs real-world FX drift

_MARKET_CAP_PATTERN = r"market\s*cap(?:italization)?"
_MARKET_CAP_UNITS_INR = [
    (re.compile(r"lakh\s*crore"), _CR_PER_LAKH_CRORE),
    (re.compile(r"lac\s*crore"), _CR_PER_LAKH_CRORE),
    (re.compile(r"\bcrore\b"), 1.0),
    (re.compile(r"\bcr\b"), 1.0),
]
_MARKET_CAP_UNITS_USD = [
    (re.compile(r"\btrillion\b"), 100_000.0 * _INR_PER_USD),
    (re.compile(r"\bbillion\b"), 100.0 * _INR_PER_USD),
]
_USD_MARKER = re.compile(r"\$|\busd\b|\bdollar")


def _parse_cited_market_cap(text: str, field_pattern: str) -> float | None:
    """Finds a market-cap citation and normalizes it to crores. INR units
    (crore/lakh crore) convert directly. USD units (billion/trillion) only
    convert if a $/usd/dollar marker also appears in the window — a bare
    '50 billion' with no currency marker is ambiguous, so it's skipped
    rather than guessed, same as the other checks' skip-on-unparseable rule."""
    match = re.search(field_pattern, text)
    if not match:
        return None
    window = text[match.end(): match.end() + 60]
    number_match = re.search(r"-?\d+\.?\d*", window.replace(",", ""))
    if not number_match:
        return None
    try:
        value = float(number_match.group(0))
    except ValueError:
        return None

    for pattern, multiplier in _MARKET_CAP_UNITS_INR:
        if pattern.search(window):
            return value * multiplier
    for pattern, multiplier in _MARKET_CAP_UNITS_USD:
        if pattern.search(window) and _USD_MARKER.search(window):
            return value * multiplier
    return None


_NUMERIC_FIELD_CHECKS = [
    ("dividend yield", r"dividend\s*yield",
     lambda ad: (ad.get("stock_info", {}) or {}).get("dividend_yield_pct"), None),
    ("P/E ratio", r"\bp/?e\b(?:\s*ratio)?|price[- ]to[- ]earnings",
     lambda ad: (ad.get("stock_info", {}) or {}).get("pe_ratio"), None),
    ("ROE", r"\broe\b|return on equity",
     lambda ad: _ratio(ad, "ROE"), None),
    ("ROCE", r"\broce\b|return on capital employed",
     lambda ad: _ratio(ad, "ROCE"), None),
    ("book value", r"book value",
     lambda ad: (ad.get("stock_info", {}) or {}).get("book_value"), None),
    # Sales/profit growth: the analyst text rarely states which trailing
    # window (3Y vs 5Y) it means, so accept either — flag only if the cited
    # number is off by 2x from BOTH known windows, not just one.
    ("sales growth", r"sales\s*growth|revenue\s*growth",
     lambda ad: [_ratio(ad, "Sales growth 3Y"), _ratio(ad, "Sales growth 5Y")], None),
    ("profit growth", r"profit\s*growth|earnings\s*growth",
     lambda ad: [_ratio(ad, "Profit growth 3Y"), _ratio(ad, "Profit growth 5Y")], None),
    ("EBITDA margin", r"ebitda\s*margin|operating\s*margin",
     lambda ad: _ratio(ad, "EBITDA margin"), None),
    ("market cap", _MARKET_CAP_PATTERN,
     lambda ad: _ratio(ad, "Market Cap"), _parse_cited_market_cap),
]


def _analysis_numeric_issues(data: dict | None, all_data: dict[str, dict] | None) -> list[str]:
    """Compares numbers the analyst LLM cites in prose against the actual
    source data, catching transcription errors like a 0.46 dividend yield
    being written as "47%". A 2x-tolerance mismatch is flagged; anything
    closer is assumed to be legitimate rounding/rephrasing."""
    if data is None or not all_data:
        return []

    issues: list[str] = []
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

    for label, field_pattern, source_getter, cite_parser in _NUMERIC_FIELD_CHECKS:
        parser = cite_parser or _first_cited_number
        cited = parser(analysis_text, field_pattern)
        if cited is None:
            continue
        source = source_getter(all_data)
        candidates = source if isinstance(source, list) else [source]
        candidates = [c for c in candidates if c is not None and c != 0]
        if not candidates:
            continue
        if not any(_in_tolerance(cited, c) for c in candidates):
            issues.append(
                f"{label} cited as {cited} but source data shows {candidates} — off by more than 2x from all known values"
            )

    issues.extend(_sector_range_issues(analysis_text, all_data))
    return issues


_SECTOR_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "IT / Software":                       {"pe": (15, 40), "roe": (15, 35)},
    "Banking (Private)":                   {"pe": (8, 25),  "roe": (10, 20)},
    "Banking (PSU) / NBFC":                {"pe": (5, 18),  "roe": (8, 18)},
    "Pharma / Healthcare":                 {"pe": (15, 45), "roe": (10, 25)},
    "FMCG / Consumer":                     {"pe": (25, 70), "roe": (15, 40)},
    "Auto / Auto Ancillary":               {"pe": (10, 35), "roe": (8, 25)},
    "Metals & Mining":                     {"pe": (4, 20),  "roe": (5, 25)},
    "Oil & Gas / Energy":                  {"pe": (5, 20),  "roe": (8, 20)},
    "Power / Utilities":                   {"pe": (6, 25),  "roe": (8, 18)},
    "Cement":                              {"pe": (10, 35), "roe": (6, 18)},
    "Capital Goods / Infra / Engineering": {"pe": (15, 45), "roe": (8, 20)},
    "Chemicals":                           {"pe": (10, 40), "roe": (8, 25)},
    "Realty":                              {"pe": (10, 50), "roe": (5, 20)},
    "Telecom":                             {"pe": (15, 60), "roe": (-5, 15)},
    "Media & Entertainment":               {"pe": (10, 40), "roe": (5, 20)},
    "Textiles":                            {"pe": (8, 30),  "roe": (5, 18)},
}
_SECTOR_RANGE_PAD = 0.25

_SECTOR_COMPARISON_PATTERN = re.compile(
    r"(sector|industry|peer[s]?)\s+average.{0,40}(\bp/?e\b|\broe\b)"
    r"|(\bp/?e\b|\broe\b).{0,40}(sector|industry|peer[s]?)\s+average"
)


_unmatched_sector_buckets_logged: set[str] = set()
_unmatched_sector_buckets_lock = threading.Lock()


def _log_unmatched_sector_bucket_once(sector: str) -> None:
    """One-time-per-process warning when a real sector value doesn't match
    any _SECTOR_RANGES bucket — same "validate the yfinance sector-taxonomy
    assumption against real production traffic" instinct as
    signals/engine.py::_log_unmatched_sector_once(), kept as a separate
    counter/event rather than reusing that one directly: this guardrail's
    bucket names (_SECTOR_RANGES) and signals/engine.py's own weight-
    override groups are two independent consumers of the same disputed
    `stock_info.sector` field, and conflating their telemetry would make it
    impossible to tell which one actually saw an unmatched sector."""
    with _unmatched_sector_buckets_lock:
        if sector in _unmatched_sector_buckets_logged:
            return
        _unmatched_sector_buckets_logged.add(sector)
    log_event(LOGGER, "sector_range_bucket_unmatched", level="warning", sector=sector)


def _resolve_sector_bucket(sector: str | None) -> str | None:
    """Fuzzy-matches a free-text sector string (e.g. Screener.in's "IT -
    Software") against _SECTOR_RANGES's bucket names. Returns None on no
    confident match rather than guessing — a stock in an unmapped sector
    simply doesn't get this check."""
    if not sector:
        return None
    from rapidfuzz import fuzz, process
    from rapidfuzz import utils as rf_utils
    match = process.extractOne(
        sector, list(_SECTOR_RANGES.keys()),
        scorer=fuzz.token_set_ratio, processor=rf_utils.default_process, score_cutoff=85,
    )
    if match:
        return match[0]
    _log_unmatched_sector_bucket_once(sector)
    return None


def _padded_range(lo: float, hi: float) -> tuple[float, float]:
    width = hi - lo
    pad = _SECTOR_RANGE_PAD * width
    return lo - pad, hi + pad


def _sector_range_issues(analysis_text: str, all_data: dict[str, dict]) -> list[str]:
    """Flags an analyst-cited 'sector/peer average P/E or ROE' figure that
    falls outside a static plausible range for the stock's own sector —
    this codebase fetches no real peer-benchmark data for the single-stock
    flow, so any such citation is either a fabrication or a genuine outlier
    worth a human's attention; either way it isn't grounded in fetched data."""
    sector = (all_data.get("stock_info", {}) or {}).get("sector")
    bucket = _resolve_sector_bucket(sector)
    if bucket is None:
        return []

    ranges = _SECTOR_RANGES[bucket]
    issues: list[str] = []
    for match in _SECTOR_COMPARISON_PATTERN.finditer(analysis_text):
        metric = (match.group(2) or match.group(3) or "")
        field = "pe" if metric.startswith("p") else "roe"
        window = analysis_text[match.end(): match.end() + 30].replace(",", "")
        number_match = re.search(r"-?\d+\.?\d*", window)
        if not number_match:
            continue
        try:
            cited = float(number_match.group(0))
        except ValueError:
            continue
        lo, hi = _padded_range(*ranges[field])
        if not (lo <= cited <= hi):
            label = "P/E" if field == "pe" else "ROE"
            issues.append(
                f"cited sector-average {label} of {cited} falls outside plausible "
                f"range for {bucket} sector ({lo:.2f}-{hi:.2f} with padding) — "
                "check for a fabricated comparison"
            )
    return issues


# Well inside signals/engine.py's own HOLD band (-0.3 to 0.1) — a score
# with this small a magnitude means the quant engine found almost nothing
# directional, so a HIGH-confidence call against it is a real inconsistency,
# not a borderline judgment call. See _validate_analysis_payload's own
# comment on this check.
_MARGINAL_SCORE_ABS = 0.15


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
    # symbol/valuation/news_sentiment are all required by config/analyst.json's
    # own output_schema (and frontend/types/index.ts's Analysis interface has
    # all three as non-optional) but were previously never checked here at
    # all -- a payload missing any of them still passed guardrails and was
    # cached/returned as a "valid" analysis, silently violating the documented
    # contract every other consumer of this schema relies on.
    for field in ("symbol", "summary", "business_quality", "bull_factors", "bear_factors",
                  "key_risks", "news_highlights", "institutional_trend", "news_sentiment"):
        if not data.get(field):
            return False, f"Field '{field}' is required and cannot be empty."
    if data.get("news_sentiment") not in {"Positive", "Neutral", "Negative"}:
        return False, "Field 'news_sentiment' must be exactly 'Positive', 'Neutral', or 'Negative'."
    if not isinstance(data.get("valuation"), dict) or not data["valuation"].get("verdict") or not data["valuation"].get("comment"):
        return False, "Field 'valuation' must be an object with non-empty 'verdict' and 'comment'."

    # A field can be present and truthy (passing the loop above) while still
    # being the WRONG TYPE -- an LLM occasionally returns comma-joined prose
    # instead of a JSON array for a list field. len() doesn't distinguish a
    # 3-item list from a 40-character string, so a truthiness + len() check
    # alone lets a string/dict through unnoticed. frontend/components/
    # results-dashboard.tsx calls .map() directly on these fields, which
    # throws on anything that isn't a real array.
    if not isinstance(data.get("bull_factors"), list) or len(data["bull_factors"]) < 3:
        return False, "Field 'bull_factors' must be a list with at least 3 items."

    if not isinstance(data.get("bear_factors"), list) or len(data["bear_factors"]) < 2:
        return False, "Field 'bear_factors' must be a list with at least 2 items."

    if not isinstance(data.get("key_risks"), list) or len(data["key_risks"]) < 3:
        return False, "Field 'key_risks' must be a list with at least 3 items."
    # .get(), not signal_context["final_score"] — this function is called
    # from more than one independent pipeline (main.py, watchlist_alerts.py,
    # api.py), and a signal_context dict missing the key (a future caller
    # passing a differently-shaped dict, a partial signal-engine failure)
    # must degrade by skipping these three quant-cross-check guards, not
    # raise a KeyError that a broad try/except elsewhere would silently
    # misclassify as a provider outage and burn a failover attempt on.
    final_score = signal_context.get("final_score") if signal_context else None
    if final_score is not None:
        if final_score > 0.5 and data["recommendation"] == "SELL":
            return False, "Recommendation contradicts strong positive signals"
        # Symmetric check — the SELL-vs-strong-positive-signals guard above
        # had no negative-side counterpart, so a BUY against a quant score
        # deep in the engine's own SELL tier (final_score <= -0.6, the same
        # threshold signals/engine.py::run_signal_engine uses for "SELL" —
        # note <=, not <: the engine's own tier boundary is inclusive, and
        # since it rounds final_score to 2 decimals, an exact -0.6 is a
        # real value this needs to catch, not just an unreachable edge)
        # previously passed validation untouched.
        if final_score <= -0.6 and data["recommendation"] == "BUY":
            return False, "Recommendation contradicts strong negative signals"
        # A "HIGH confidence" claim against a near-neutral quant score is
        # its own kind of unsupported claim — the two checks above catch a
        # directional contradiction (BUY/SELL vs. a strongly opposite
        # score), but say nothing about confidence *magnitude*: a HIGH-
        # confidence BUY at final_score=0.11 (barely past the WATCHLIST
        # threshold) and a HIGH-confidence BUY at 0.9 previously passed
        # identical validation. _MARGINAL_SCORE_ABS is well inside
        # signals/engine.py's own HOLD band (-0.3 to 0.1), so this only
        # fires when the quant engine itself found almost nothing
        # directional either way.
        if abs(final_score) < _MARGINAL_SCORE_ABS and data["confidence"] == "HIGH":
            return False, (
                "Confidence 'HIGH' is not supported by a near-neutral quant signal score "
                f"({final_score}); use MEDIUM or LOW instead."
            )
    support_issues = _analysis_support_issues(data, all_data) + _analysis_numeric_issues(data, all_data)
    if support_issues:
        return False, f"Unsupported claims found: {'; '.join(support_issues)}."
    return True, data


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

    Failover is skipped entirely when `LLM_PROVIDER` is explicitly set —
    that env var is this deployment's own deliberate choice to pin one
    provider (e.g. a local-only Ollama deployment kept off the cloud on
    purpose for data residency), not merely "whichever key happened to be
    configured first." A stray second provider's key left over in the same
    environment for an unrelated reason (shared with another service,
    leftover from testing) must not silently send this analysis's fetched
    market/fundamentals/news/filings data to that other provider on a
    transient failure of the pinned one — that would cross a boundary the
    operator explicitly drew. Failover only ever engages when the primary
    was auto-detected (no explicit `LLM_PROVIDER`), the same case where
    "which provider is even primary" was already just "whichever key came
    first," so trying a second one on failure is a resilience improvement,
    not a boundary violation.
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
    # Only auto-detected primaries get a failover candidate — an explicit
    # LLM_PROVIDER is a deliberate single-provider pin (see this function's
    # own docstring above for why a stray second key must not override it).
    fallback_provider = None if os.getenv("LLM_PROVIDER") else next(
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
