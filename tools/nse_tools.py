import contextlib
import io
import json
import re
from datetime import datetime
from urllib.parse import quote, urlparse

import requests
import yfinance as yf
from lxml import etree
from crewai.tools import tool

from tools._nse_session import NSE_BASE_URL as _NSE_BASE
from tools._nse_session import get_nse_session

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com",
}

_ALLOWED_XBRL_HOST = "nseindia.com"


def _nse_session() -> requests.Session:
    return get_nse_session(timeout=10)


def _is_nse_host(url: str) -> bool:
    """Guards the two places this module fetches an XBRL attachment URL that
    came out of NSE's own API response, not a hardcoded endpoint — same
    reasoning as tools/trendlyne_scraper.py::_is_trendlyne_host: an
    unvalidated absolute URL taken from a scraped/API payload and fetched
    server-side is an SSRF vector regardless of how unlikely NSE's own
    response is to actually carry one. Accepts any nseindia.com subdomain
    (NSE serves attachments from more than one), never a bare host-suffix
    match that a lookalike domain like "evilnseindia.com" could satisfy."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    return host == _ALLOWED_XBRL_HOST or host.endswith("." + _ALLOWED_XBRL_HOST)


def _parse_xbrl_xml(content: bytes):
    """Parses externally-fetched XBRL XML with entity resolution disabled —
    defense in depth against an XXE/entity-expansion payload smuggled into
    an attachment this module doesn't control the origin of, on top of the
    host check in _is_nse_host. Never raises here; a malformed document
    surfaces as the same XMLSyntaxError the bare etree.fromstring() call
    would already raise, caught by each call site's own try/except."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.fromstring(content, parser=parser)


def _percent_from_ambiguous_value(value: float | None, plausible_max: float) -> float | None:
    """Best-effort percent from a source value that's ambiguously either
    already a percent or a 0-1 fraction (yfinance's dividendYield field is
    documented to vary in format across versions/tickers; NSE's XBRL
    shareholding percent facts are conventionally fractions but not
    guaranteed). A value already >1 is unambiguous (can't be a valid
    fraction) and is returned as-is when plausible.

    Rather than trust a *transformed* value blindly — a genuine sub-1%
    figure already in percent form would otherwise get wrongly multiplied
    into a wildly implausible percentage (e.g. a real 0.5% yield read as a
    50% one) — any candidate result past `plausible_max` returns None
    ("never invent") instead of a wrong number, since a real dividend yield
    or single fund's shareholding this high would be extraordinary, so a
    value past the ceiling more likely means the format guess was wrong
    than that the number is real. Returns None (not 0) when the source
    value itself is missing/negative, so a caller can tell "no reliable
    value" apart from a genuine 0%.
    """
    if value is None or value < 0:
        return None
    if value == 0:
        return 0.0
    candidate = value * 100 if value <= 1 else value
    return round(candidate, 4) if candidate <= plausible_max else None


def _is_valid_quote(info: dict) -> bool:
    """Return True if yfinance info has usable price and market cap data."""
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    mkt_cap = info.get("marketCap")
    return bool(price and price < 500_000 and mkt_cap)


def _build_quote_payload(sym: str, exchange: str, info: dict) -> dict:
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    prev_close = info.get("previousClose") or price
    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
    market_cap = info.get("marketCap")
    company_name = (
        info.get("longName")
        or info.get("shortName")
        or info.get("displayName")
        or info.get("name")
        or ""
    )

    return {
        "symbol": sym,
        "exchange": exchange,
        "company_name": company_name,
        "current_price": price,
        "previous_close": prev_close,
        "change_pct": change_pct,
        "volume": info.get("volume"),
        "avg_volume_10d": info.get("averageVolume10days"),
        "market_cap_cr": round(market_cap / 1e7, 0) if market_cap else None,
        "pe_ratio": info.get("trailingPE"),
        "eps": info.get("trailingEps"),
        "book_value": info.get("bookValue"),
        "price_to_book": info.get("priceToBook"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        # yfinance should return dividendYield as a decimal (0.25 = 25%).
        # Some tickers return it already in percentage form (e.g. 258.65) —
        # _percent_from_ambiguous_value drops any implausible result (>25%,
        # an extraordinary dividend yield for a real equity) rather than
        # trusting a wrong format guess.
        "dividend_yield_pct": _percent_from_ambiguous_value(info.get("dividendYield"), plausible_max=25.0) or 0,
        "beta": info.get("beta"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "about": (info.get("longBusinessSummary") or "")[:600],
    }


@tool("Get NSE Stock Quote")
def get_stock_quote(symbol: str) -> str:
    """Get current market data for an Indian stock listed on NSE or BSE.
    Returns price, change %, market cap, PE ratio, EPS, 52-week range, sector, industry, and description.
    Input: stock symbol without suffix, e.g. RELIANCE, TCS, INFY, HDFCBANK."""
    sym = symbol.upper()
    last_err = ""
    quotes_by_exchange = {}
    for suffix, exchange in (("NS", "NSE"), ("BO", "BSE")):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                info = yf.Ticker(f"{sym}.{suffix}").info
            if not _is_valid_quote(info):
                last_err = f"No valid market data for {sym} on {exchange}"
                continue
            quotes_by_exchange[exchange] = _build_quote_payload(sym, exchange, info)
        except Exception as e:
            last_err = str(e)

    if quotes_by_exchange:
        primary_exchange = "NSE" if "NSE" in quotes_by_exchange else next(iter(quotes_by_exchange))
        primary_quote = quotes_by_exchange[primary_exchange]
        return json.dumps({
            **primary_quote,
            "primary_exchange": primary_exchange,
            "prices_by_exchange": quotes_by_exchange,
        })

    return json.dumps({"error": last_err or f"No market data found for {sym}", "symbol": sym})


def _fetch_shareholding_xbrl(symbol: str):
    """Shared first half of get_mf_holdings/get_shareholding_detail — locates
    the most recent shareholding XBRL filing on NSE's corporate-share-
    holdings-master endpoint and fetches+parses it. Returns (root, as_of_date).
    Raises on any failure (missing records, missing/off-host XBRL URL, fetch
    error, malformed XML) — every caller wraps this in its own try/except and
    turns the exception into its own {"error": ...} payload, same "tools
    never raise past their own public surface" convention as the rest of
    this module. Extracted so a genuinely new consumer of this same NSE
    filing (see get_shareholding_detail) doesn't have to re-fetch the same
    document a second time within one call, nor duplicate the SSRF/XXE
    hardening (_is_nse_host pre- and post-redirect, resolve_entities=False)
    that fetch already needs."""
    session = _nse_session()
    master_url = f"{_NSE_BASE}/api/corporate-share-holdings-master?index=equities&symbol={symbol.upper()}"
    master_resp = session.get(master_url, headers=_NSE_HEADERS, timeout=15)
    master_resp.raise_for_status()
    records = master_resp.json()

    if not records:
        raise ValueError("No shareholding records found")

    # Pick the most recent record (last by date)
    latest = sorted(records, key=lambda r: r.get("date", ""), reverse=True)[0]
    xbrl_url = latest.get("xbrl", "")
    if not xbrl_url or not _is_nse_host(xbrl_url):
        raise ValueError("No XBRL URL in shareholding record")

    xbrl_resp = requests.get(xbrl_url, headers={"User-Agent": _NSE_HEADERS["User-Agent"]}, timeout=20)
    xbrl_resp.raise_for_status()
    # Re-check the post-redirect URL too, not just the one requested — a
    # redirect encountered on this specific fetch could otherwise land off
    # nseindia.com with no check at all.
    if not _is_nse_host(xbrl_resp.url):
        raise ValueError("XBRL URL redirected off nseindia.com")
    root = _parse_xbrl_xml(xbrl_resp.content)
    return root, latest.get("date", "")


@tool("Get NSE Mutual Fund Holdings")
def get_mf_holdings(symbol: str) -> str:
    """Fetch the top mutual funds holding an NSE-listed Indian stock from NSE's shareholding XBRL data.
    Returns fund names, number of shares, and percentage of total shares held.
    Input: NSE stock symbol, e.g. RELIANCE, TCS, INFY."""
    try:
        root, as_of_date = _fetch_shareholding_xbrl(symbol)

        # Find MF context IDs: typedMember > scenario > context(id)
        ns_di = "http://xbrl.org/2006/xbrldi"
        mf_ctx_ids = set()
        for tm in root.findall(f".//{{{ns_di}}}typedMember"):
            child = tm[0] if len(tm) else None
            if child is None or "MutualFunds" not in etree.QName(child.tag).localname:
                continue
            # Walk up to the xbrli:context element that carries the id
            ctx_elem = tm
            for _ in range(5):
                ctx_elem = ctx_elem.getparent()
                if ctx_elem is None:
                    break
                ctx_id = ctx_elem.get("id", "")
                if ctx_id:
                    mf_ctx_ids.add(ctx_id)  # includes both "D_" and non-"D_" contexts
                    break

        # Build map: detail_ctx -> fund info
        ctx_name: dict[str, str] = {}
        ctx_pct: dict[str, float] = {}

        for elem in root.iter():
            ctx_ref = elem.get("contextRef", "")
            if not ctx_ref:
                continue
            local = etree.QName(elem.tag).localname

            if ctx_ref in mf_ctx_ids and local == "NameOfTheShareholder" and elem.text:
                base_id = ctx_ref.removeprefix("D_")
                ctx_name[base_id] = elem.text.strip()

            if local == "ShareholdingAsAPercentageOfTotalNumberOfShares" and elem.text:
                # ctx_ref without "D_" prefix is the numeric context. One
                # non-numeric fact anywhere in a document that can carry
                # dozens/hundreds of shareholder records must not abort the
                # whole result — skip just that fact.
                try:
                    ctx_pct[ctx_ref] = float(elem.text)
                except ValueError:
                    continue

        funds = []
        for ctx_id, name in ctx_name.items():
            pct = ctx_pct.get(ctx_id)
            if pct is not None:
                # A single fund holding >30% of a company's total shares
                # outstanding would be extraordinary — same "drop rather
                # than trust a wrong format guess" reasoning as
                # dividend_yield_pct above. holding_pct can legitimately be
                # 0.0 (distinct from None, the "unreliable" case), which
                # is why this checks `is not None` rather than truthiness.
                holding_pct = _percent_from_ambiguous_value(pct, plausible_max=30.0)
                if holding_pct is not None:
                    funds.append({"fund": name, "holding_pct": holding_pct})

        funds.sort(key=lambda x: x["holding_pct"], reverse=True)

        return json.dumps({
            "symbol": symbol.upper(),
            "as_of_date": as_of_date,
            "mutual_funds": funds[:15],
        })
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


def _humanize_category(raw: str) -> str:
    """Turns an XBRL typedMember child's raw PascalCase localname (e.g.
    "ForeignPortfolioInvestorsMember") into a readable label ("Foreign
    Portfolio Investors") — this app has no fixed enum of NSE's
    shareholding-category taxonomy to translate against (see
    get_shareholding_detail's own disclosed limitation below), so the raw
    XBRL tag name IS the category, just cleaned up for display: a trailing
    "Member" is stripped (a generic XBRL dimensional-modeling convention —
    explicit/typed dimension member concepts are conventionally suffixed
    "Member" per the XBRL spec itself, not a guess specific to NSE's own
    taxonomy) and the remainder is word-spaced rather than shown as
    unbroken PascalCase."""
    trimmed = raw[:-len("Member")] if raw.endswith("Member") and len(raw) > len("Member") else raw
    # Splits at a lowercase/digit→uppercase boundary ("Portfolio" + "Investors")
    # and at an acronym-run→Titlecase-word boundary ("FII" + "Category" in
    # "FIICategory"), but NOT inside a run of consecutive capitals — NSE
    # shareholding categories commonly use short acronyms verbatim (FII,
    # NRI, HUF, IEPF), which a plain "space before every capital" split
    # would otherwise break into single spaced-out letters.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", trimmed)
    return spaced.strip() or raw


@tool("Get NSE Detailed Shareholding")
def get_shareholding_detail(symbol: str) -> str:
    """Fetch every individually-named shareholder disclosed in an NSE-listed
    Indian stock's shareholding XBRL filing — promoters by name, plus every
    other named-shareholder category the filing contains (mutual funds,
    FPIs, insurance companies, etc., whatever NSE's own filing actually
    tags). A more granular view than get_holdings' aggregate Promoters/
    FIIs/DIIs/Public percentages or get_mf_holdings' mutual-fund-only list.
    Input: NSE stock symbol, e.g. RELIANCE, TCS, INFY.

    Reuses get_mf_holdings' own proven extraction mechanism
    (NameOtTheShareholder + ShareholdingAsAPercentageOfTotalNumberOfShares
    facts, keyed off the typedMember context an XBRL shareholder record
    lives under), generalized to every category rather than filtered to
    contexts whose typedMember child tag contains "MutualFunds" — this
    means it doesn't need to guess NSE's exact category tag spellings for
    "Insurance Companies"/"Alternate Investment Funds"/etc. up front: it
    groups by whatever category label the filing actually uses, and the
    only tag-name assumption this function adds on top of the already-
    working MF extraction is that a promoter/promoter-group category's
    XBRL tag contains the substring "Promoter" (case-insensitive) —
    consistent with how the MF filter already matches on "MutualFunds"
    appearing in the tag, not an exact tag string.

    Disclosed limitation: like every other NSE/Screener/Trendlyne/RBI
    scraper in this codebase, the exact XBRL category tag names NSE's real
    shareholding filings use beyond "MutualFunds" (already proven by
    get_mf_holdings) were not verified against a live filing in this
    sandbox (no outbound internet). If a real filing's promoter category
    tag doesn't contain "Promoter", those records fall through into
    shareholder_categories under their own raw label instead of the
    dedicated promoters field — a degraded-but-not-wrong result, never a
    fabricated one, same "never invent" convention as everywhere else in
    this file.
    """
    try:
        root, as_of_date = _fetch_shareholding_xbrl(symbol)

        ns_di = "http://xbrl.org/2006/xbrldi"
        # Unlike get_mf_holdings (which only keeps typedMember contexts whose
        # child tag contains "MutualFunds"), this walks every category the
        # filing has — the whole point of this function is not being scoped
        # to one category.
        category_by_ctx: dict[str, str] = {}
        for tm in root.findall(f".//{{{ns_di}}}typedMember"):
            child = tm[0] if len(tm) else None
            if child is None:
                continue
            category_raw = etree.QName(child.tag).localname
            ctx_elem = tm
            for _ in range(5):
                ctx_elem = ctx_elem.getparent()
                if ctx_elem is None:
                    break
                ctx_id = ctx_elem.get("id", "")
                if ctx_id:
                    category_by_ctx[ctx_id] = category_raw
                    break

        # Unlike get_mf_holdings — where ctx_name only ever collects
        # MF-tagged contexts, so a foreign category's context id could
        # never land in the same dict — this walk is document-wide across
        # every category, which reopens a real (if rare) hazard: NSE's own
        # "D_" dimensional-context-id convention only guarantees a context
        # literally named "D_<n>" pairs with a plain "<n>" sibling; nothing
        # guarantees two DIFFERENT shareholder records across two DIFFERENT
        # categories can't independently reduce to the same "<n>" after
        # stripping "D_". A plain dict would let the later-processed
        # category silently clobber the earlier one's name/category
        # attribution with no error. Collect every (name, category) pair
        # seen per base id instead of overwriting, so a genuine collision
        # can be detected and the ambiguous entries dropped below — never
        # invent which candidate the shared percentage fact actually
        # belongs to, same "drop rather than guess" convention
        # _percent_from_ambiguous_value's own plausibility ceiling uses.
        ctx_candidates: dict[str, list[tuple[str, str]]] = {}
        ctx_pct: dict[str, float] = {}

        for elem in root.iter():
            ctx_ref = elem.get("contextRef", "")
            if not ctx_ref:
                continue
            local = etree.QName(elem.tag).localname

            if ctx_ref in category_by_ctx and local == "NameOfTheShareholder" and elem.text:
                base_id = ctx_ref.removeprefix("D_")
                ctx_candidates.setdefault(base_id, []).append((elem.text.strip(), category_by_ctx[ctx_ref]))

            if local == "ShareholdingAsAPercentageOfTotalNumberOfShares" and elem.text:
                # One non-numeric fact anywhere in a document that can carry
                # hundreds of shareholder records must not abort the whole
                # result — skip just that fact, same as get_mf_holdings.
                try:
                    ctx_pct[ctx_ref] = float(elem.text)
                except ValueError:
                    continue

        promoters = []
        by_category: dict[str, list[dict]] = {}
        for base_id, candidates in ctx_candidates.items():
            # More than one distinct (name, category) pair reduced to the
            # same base id — a genuine context-id collision this
            # generalized walk can't safely resolve (which candidate does
            # the one shared percentage fact actually belong to?). Drop
            # both/all rather than silently picking whichever was
            # processed last.
            if len(set(candidates)) != 1:
                continue
            name, category_raw = candidates[0]
            pct = ctx_pct.get(base_id)
            if pct is None:
                continue
            is_promoter = "promoter" in category_raw.lower()
            # A promoter/promoter-group entity can plausibly hold up to (in
            # principle) 100% of a closely-held company; any other single
            # named institutional/individual holder above ~30% would be
            # extraordinary — same "drop rather than trust a wrong format
            # guess" reasoning _percent_from_ambiguous_value's own docstring
            # describes, just with get_mf_holdings' existing 30% ceiling
            # applied to every non-promoter category, not only mutual funds.
            holding_pct = _percent_from_ambiguous_value(pct, plausible_max=100.0 if is_promoter else 30.0)
            if holding_pct is None:
                continue
            entry = {"name": name, "holding_pct": holding_pct}
            if is_promoter:
                promoters.append(entry)
            else:
                label = _humanize_category(category_raw) if category_raw else "Other"
                by_category.setdefault(label, []).append(entry)

        promoters.sort(key=lambda x: x["holding_pct"], reverse=True)
        shareholder_categories = [
            {
                "category": label,
                "holders": sorted(holders, key=lambda x: x["holding_pct"], reverse=True)[:20],
            }
            for label, holders in sorted(
                by_category.items(), key=lambda kv: sum(h["holding_pct"] for h in kv[1]), reverse=True,
            )
        ]

        return json.dumps({
            "symbol": symbol.upper(),
            "as_of_date": as_of_date,
            "promoters": promoters[:20],
            "shareholder_categories": shareholder_categories,
        })
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


# Standard Ind-AS XBRL tag localnames a "Financial Results" filing's basic
# EPS fact is commonly reported under. Matched by localname only, ignoring
# namespace — the same approach get_mf_holdings() above already uses
# successfully for shareholding XBRL, since NSE's own XBRL namespace URIs
# for results filings weren't verifiable in this sandbox either.
_XBRL_EPS_TAGS = (
    "BasicEarningsLossPerShareFromContinuingOperations",
    "BasicEarningsPerShareFromContinuingOperations",
    "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
    "BasicEarningsPerEquityShare",
)


def _parse_filing_date(date_str: object) -> str | None:
    """NSE's corporate-announcements `date` field is `dd-Mon-yyyy[ HH:MM]`,
    which does NOT sort lexically in calendar order (the same drift
    tools/nse_insider_trades.py::_parse_pit_date already documents and
    fixes for its own date field) — a bare string sort of raw NSE dates
    would silently pick the wrong "most recent" filing. Returns an
    ISO-8601 string sortable in true chronological order, or None if the
    format doesn't match any of these (never guessed)."""
    if not isinstance(date_str, str):
        return None
    for fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).isoformat()
        except ValueError:
            continue
    return None


def get_nse_basic_ratios(symbol: str) -> dict:
    """Best-effort fallback for a stock's basic EPS, sourced from NSE's own
    XBRL-tagged financial-results filings rather than Screener.in. Not a
    @tool — not one of the six ALL_DATA_TASKS, called only from
    tools/screener_tools.py::get_fundamentals() when Screener's own ratios
    table came back completely empty (e.g. a recent IPO Screener hasn't
    indexed yet, but NSE already has a results filing for). Returns {}
    (never invented) on any failure, missing filing, missing XBRL
    attachment, or an unrecognized tag — the same "never invent" convention
    every other tool in this codebase follows.

    **Deliberately EPS-only, not "EPS, sales, profit"**: a per-share figure
    like EPS is self-scaled (always "rupees and paise per share") with no
    unit ambiguity. Sales/profit are aggregate rupee figures that XBRL
    reports at a `decimals`/`unitRef`-dependent scale (absolute rupees,
    lakhs, or crore) — correctly resolving that would require parsing the
    XBRL unit/decimals metadata against a real NSE filing to confirm the
    convention it actually uses, which could not be verified in this
    sandbox (no outbound internet — same disclosed-limitation pattern as
    every other NSE/BSE scraper in this codebase). Guessing a scale and
    getting it wrong would inject a confidently-incorrect figure (e.g. off
    by 100x) — actively worse than the missing-data case this fallback
    exists to improve on, so those two fields are intentionally left out
    of this first pass rather than shipped as a guess.

    **Disclosed limitation**: neither NSE's corporate-announcements
    response shape under `reqXbrl=true` (which field, if any, carries the
    XBRL attachment URL — this tries several plausible field names) nor
    the exact Ind-AS tag names a real filing uses were verified against a
    live response in this sandbox. Worth spot-checking before this is
    relied on in production; until then it degrades to {} exactly like an
    unreachable endpoint would, so a wrong guess here is invisible/safe
    rather than actively misleading.
    """
    try:
        session = _nse_session()
        sym = symbol.upper().strip()
        url = (
            f"{_NSE_BASE}/api/corporate-announcements?"
            f"index=equities&symbol={quote(sym)}&reqXbrl=true"
        )
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        filings = resp.json()
        if not isinstance(filings, list) or not filings:
            return {}

        results_filings = [
            f for f in filings
            if isinstance(f, dict)
            and "financial results" in str(f.get("desc") or f.get("subject") or "").lower()
        ]
        candidates = sorted(
            results_filings or filings,
            key=lambda f: _parse_filing_date(f.get("date")) or "",
            reverse=True,
        )

        xbrl_url = None
        for f in candidates:
            if not isinstance(f, dict):
                continue
            for key in ("xbrl", "xbrlUrl", "xbrl_attachment", "attchmntFile"):
                val = f.get(key)
                if val and str(val).lower().endswith((".xml", ".xbrl")):
                    xbrl_url = val
                    break
            if xbrl_url:
                break
        if not xbrl_url or not _is_nse_host(xbrl_url):
            return {}

        xbrl_resp = requests.get(xbrl_url, headers={"User-Agent": _NSE_HEADERS["User-Agent"]}, timeout=20)
        xbrl_resp.raise_for_status()
        if not _is_nse_host(xbrl_resp.url):
            return {}
        root = _parse_xbrl_xml(xbrl_resp.content)

        eps = None
        for elem in root.iter():
            if etree.QName(elem.tag).localname in _XBRL_EPS_TAGS and elem.text:
                try:
                    eps = float(elem.text.strip())
                    break
                except ValueError:
                    continue

        return {"eps": eps, "source": "nse_xbrl", "as_of_date": candidates[0].get("date")} if eps is not None else {}
    except Exception:
        return {}
