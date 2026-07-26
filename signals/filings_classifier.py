"""Best-effort classification of already-fetched NSE corporate filings —
no new scrape, just text classification over the same `filings` list
`tools/nse_filings_tools.py::get_nse_filings()` already returns (title,
desc, category, date). Extracts three independently-optional signals:

- `corporate_actions`: dividend / split / bonus / buyback filings, by
  keyword match on title+desc+category.
- `rating_action`: the most recent credit-rating filing (agency name +
  upgrade/downgrade/reaffirmed direction, best-effort from/to rating
  strings), if any.
- `next_results_date`: a future results date parsed out of the most
  recent "board meeting to consider financial results" filing's own
  free text, if one is present and parses cleanly.

Every field is `None`/`[]` (never guessed) when nothing matches — this is
free-text classification over filing titles/descriptions NSE writes in
whatever house style it chooses that day, so a miss is expected to be
common, not a bug. **Disclosed limitation**: the exact category/title
vocabulary NSE uses for these filing types was not verified against a
live response in this sandbox (no outbound internet — same disclosure
pattern as every other NSE/BSE scraper in this codebase); the keyword
lists here are a reasonable first pass, not a confirmed match against
real NSE text.
"""
import re
from datetime import datetime

_CORPORATE_ACTION_KEYWORDS = {
    "dividend": ("dividend",),
    "split": ("stock split", "share split", "sub-division of", "sub division of"),
    "bonus": ("bonus issue", "bonus shares", "issue of bonus"),
    "buyback": ("buyback", "buy back", "buy-back"),
}

_RATING_AGENCIES = (
    "crisil", "icra", "care ratings", "care", "india ratings", "ind-ra",
    "moody's", "moodys", "s&p", "fitch", "brickwork", "acuite",
)
_RATING_ACTION_KEYWORDS = {
    "upgrade": ("upgraded", "upgrade"),
    "downgrade": ("downgraded", "downgrade"),
    "reaffirmed": ("reaffirmed", "reaffirms", "reaffirm"),
}
_RATING_FROM_TO_RE = re.compile(
    r"from\s+['\"]?([A-Za-z0-9+\-/ ]{2,15}?)['\"]?\s+to\s+['\"]?([A-Za-z0-9+\-/ ]{2,15}?)['\"]?[\s.,]",
    re.IGNORECASE,
)

_RESULTS_BOARD_MEETING_KEYWORDS = ("board meeting", "intimation")
_RESULTS_KEYWORDS = ("financial results", "quarterly results", "unaudited results", "audited results")

_DATE_PATTERNS = (
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), "%d-%m-%Y"),
    (
        re.compile(
            r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "%d-%b-%Y",
    ),
)


def _filing_text(f: dict) -> str:
    return " ".join(str(f.get(k) or "") for k in ("title", "desc", "category")).lower()


def _parse_filing_date(date_str: object) -> datetime | None:
    if not isinstance(date_str, str):
        return None
    for fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def classify_corporate_actions(filings: list[dict]) -> list[dict]:
    """Every filing whose title/desc/category matches a known dividend /
    split / bonus / buyback keyword, newest first. A single filing only
    ever contributes one entry even if it happens to match more than one
    category's keywords — the first category checked (dict order above)
    wins, since a real corporate-action filing announces exactly one
    action, and matching two would more likely mean the text is generic
    (e.g. a results filing that mentions "dividend" in passing) rather
    than genuinely dual-purpose."""
    actions = []
    for f in filings:
        if not isinstance(f, dict):
            continue
        text = _filing_text(f)
        for action_type, keywords in _CORPORATE_ACTION_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                actions.append({
                    "type": action_type,
                    "date": f.get("date"),
                    "title": f.get("title"),
                })
                break

    actions.sort(key=lambda a: _parse_filing_date(a["date"]) or datetime.min, reverse=True)
    return actions


def classify_rating_action(filings: list[dict]) -> dict | None:
    """The single most recent credit-rating filing, or None if this
    symbol's filings window has no rating-agency mention at all. `from_`/
    `to` are best-effort — populated only when a "from X to Y" phrase is
    cleanly present in the filing's own text, never guessed at."""
    candidates = []
    for f in filings:
        if not isinstance(f, dict):
            continue
        text = _filing_text(f)
        agency = next((a for a in _RATING_AGENCIES if a in text), None)
        if not agency:
            continue
        action = next(
            (action for action, keywords in _RATING_ACTION_KEYWORDS.items() if any(kw in text for kw in keywords)),
            None,
        )
        if not action:
            continue
        candidates.append((f, agency, action, text))

    if not candidates:
        return None

    candidates.sort(key=lambda c: _parse_filing_date(c[0].get("date")) or datetime.min, reverse=True)
    f, agency, action, text = candidates[0]

    match = _RATING_FROM_TO_RE.search(text)
    from_rating = match.group(1).strip().upper() if match else None
    to_rating = match.group(2).strip().upper() if match else None

    return {
        "agency": agency.title(),
        "action": action,
        "from_rating": from_rating,
        "to_rating": to_rating,
        "date": f.get("date"),
        "title": f.get("title"),
    }


def extract_next_results_date(filings: list[dict]) -> str | None:
    """A future results date parsed out of the most recent "board meeting
    to consider financial results" filing's own free text, or None if no
    such filing exists or its text doesn't contain a cleanly-parseable
    date. The extracted date must not fall before the filing's own date —
    a board-meeting intimation is always dated ahead of the meeting it
    announces, so a date parsed as earlier is more likely an unrelated
    date mentioned in the same text (e.g. a fiscal-year reference) than
    the actual meeting date, and is dropped rather than trusted."""
    board_meeting_filings = [
        f for f in filings
        if isinstance(f, dict)
        and any(kw in _filing_text(f) for kw in _RESULTS_BOARD_MEETING_KEYWORDS)
        and any(kw in _filing_text(f) for kw in _RESULTS_KEYWORDS)
    ]
    if not board_meeting_filings:
        return None

    board_meeting_filings.sort(key=lambda f: _parse_filing_date(f.get("date")) or datetime.min, reverse=True)
    latest = board_meeting_filings[0]
    filing_date = _parse_filing_date(latest.get("date"))
    text = " ".join(str(latest.get(k) or "") for k in ("title", "desc"))

    for pattern, fmt in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            if fmt == "%d-%m-%Y":
                candidate = datetime.strptime(f"{match.group(1)}-{match.group(2)}-{match.group(3)}", "%d-%m-%Y")
            else:
                candidate = datetime.strptime(f"{match.group(1)}-{match.group(2)}-{match.group(3)}", "%d-%b-%Y")
        except ValueError:
            continue
        if filing_date is not None and candidate.date() < filing_date.date():
            continue
        return candidate.date().isoformat()

    return None


def classify_filings(filings: list[dict]) -> dict:
    """Convenience wrapper combining all three classifiers — the single
    call site both main.py::_build_report() (frontend display) and
    signals/filings.py::filings_signal() (rating-change nudge) use, so
    the same text-classification logic is never duplicated between the
    two independent call sites."""
    return {
        "corporate_actions": classify_corporate_actions(filings),
        "rating_action": classify_rating_action(filings),
        "next_results_date": extract_next_results_date(filings),
    }
