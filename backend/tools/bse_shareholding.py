"""BSE India shareholding-pattern (inline-XBRL) scraper — the BSE-only
fallback for tools/nse_tools.py::get_shareholding_detail(), used when a
stock isn't NSE-listed at all (NSE's corporate-share-holdings-master
genuinely returns zero records for a BSE-only stock — a real, permanent
"not there," not a transient fetch failure; see api.py's
/api/shareholding-detail docstring for how the two are told apart).

Confirmed against a live filing in this session (AG Ventures Ltd,
scripcode 506579), not a guess:

- BSE's SHPQNewFormat endpoint (`{scripcode}` keyed, not symbol-keyed —
  unlike every NSE endpoint in tools/nse_tools.py) lists every quarterly
  shareholding filing; the latest one's `xbrlurl` points at the actual
  filing.
- That filing is served as **inline XBRL** — facts as
  `<ix:nonNumeric name='in-bse-shp:Concept' contextRef='...'>value</ix:...>`
  (numeric facts under `ix:nonFraction` instead) embedded in an XHTML
  wrapper — not a plain XBRL XML document like NSE's, so it needs its own
  parser. It parses cleanly as well-formed XML (confirmed), so this reuses
  tools.nse_tools' XXE-safe `etree.XMLParser(resolve_entities=False)`
  settings rather than an HTML parser (which would lowercase attribute
  names like `contextRef` and silently break every lookup below).
- BSE's own SEBI-mandated shareholding taxonomy (`in-bse-shp`) happens to
  use the *exact same* concept names as NSE's XBRL
  (`NameOfTheShareholder`, `ShareholdingAsAPercentageOfTotalNumberOfShares`)
  and the same "D_<ctx>" (name/type facts) vs bare "<ctx>" (numeric facts)
  contextRef pairing convention tools.nse_tools.get_shareholding_detail()
  already relies on — ported directly rather than reinvented. One real
  structural difference: BSE bakes the shareholder category into the
  contextRef itself (`D_IndividualsOrHUF_Context15`), so unlike NSE's
  typedMember-dimension walk there's no way for two different categories'
  context ids to collide after stripping "D_" — no collision-guard needed
  here (NSE's own has one; this doesn't, deliberately, not an oversight).
  Promoter status is a per-shareholder fact (`TypeOfPromoterShareholding`
  present) rather than a category-tag substring match.
- BSE's API hangs indefinitely (no response, no clean reject) on a
  connection-pooled/keep-alive `requests` call to at least the
  SHPQNewFormat endpoint — confirmed live. `Connection: close` on every
  request here is the actual fix, not cosmetic.
"""

import json
import re

import requests
from lxml import etree

from core.observability import get_logger, log_event
from tools.nse_tools import _humanize_category
from tools.securities_master import _BSE_HEADERS

LOGGER = get_logger("tools.bse_shareholding")

_BSE_API_BASE = "https://api.bseindia.com/BseIndiaAPI/api"
_BSE_FILES_HOST = "www.bseindia.com"
_IX_NAME_RE = re.compile(r"^in-bse-shp:(.+)$")


def _plausible_holding_pct(value: float, plausible_max: float) -> float | None:
    """Unlike tools.nse_tools._percent_from_ambiguous_value, this does NOT
    guess whether `value` needs multiplying by 100 — confirmed live against
    a real filing that BSE's ShareholdingAsAPercentageOfTotalNumberOfShares
    fact is always already in percent form ('1.00' means 1%, not 0.01;
    '2.00' means 2%), unlike NSE's own equivalent fact, which that other
    helper's own docstring documents as "conventionally fractions but not
    guaranteed." Applying NSE's ambiguous-fraction heuristic here silently
    turned a real 1% holding into a fabricated 100% one — this exists so
    that mistake can't come back."""
    if value < 0:
        return None
    return round(value, 4) if value <= plausible_max else None


def _bse_get(url: str, **kwargs) -> requests.Response:
    headers = {**_BSE_HEADERS, "Connection": "close"}
    return requests.get(url, headers=headers, timeout=kwargs.pop("timeout", 15), **kwargs)


def _is_bse_files_host(url: str) -> bool:
    """Same SSRF guard as tools.nse_tools._is_nse_host, for the same
    reason: `xbrl_path` below comes out of BSE's own API response, not a
    hardcoded endpoint, so it's fetched server-side without first proving
    it still points at bseindia.com."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    return host == _BSE_FILES_HOST or host.endswith("." + _BSE_FILES_HOST)


def get_shareholding_detail(scripcode: str) -> str:
    """BSE counterpart to tools.nse_tools.get_shareholding_detail — same
    output shape ({"symbol", "as_of_date", "promoters", "shareholder_categories"}
    on success, {"error": ...} on failure), never raises. Takes a BSE
    numeric scrip code, not a trading symbol: BSE's own shareholding API is
    scripcode-keyed (tools.securities_master.fetch_bse_main_board's own
    `code` field is where a caller gets one for a given symbol)."""
    try:
        index_resp = _bse_get(f"{_BSE_API_BASE}/SHPQNewFormat/w", params={"scripcode": scripcode})
        index_resp.raise_for_status()
        filings = index_resp.json().get("Table") or []
        if not filings:
            return json.dumps({"error": "No shareholding filings found", "symbol": scripcode})

        # Sort explicitly by qtrid rather than trust response ordering —
        # same "never guess an alignment" instinct as
        # tools/screener_tools.py's own header-position checks.
        latest = sorted(filings, key=lambda f: f.get("qtrid") or 0, reverse=True)[0]
        xbrl_path = latest.get("xbrlurl") or ""
        as_of_date = latest.get("filing_date_time") or ""
        xbrl_url = f"https://{_BSE_FILES_HOST}{xbrl_path}"
        if not xbrl_path.startswith("/") or not _is_bse_files_host(xbrl_url):
            return json.dumps({"error": "No usable XBRL URL in latest shareholding filing", "symbol": scripcode})

        xbrl_resp = _bse_get(xbrl_url)
        xbrl_resp.raise_for_status()
        if not _is_bse_files_host(xbrl_resp.url):
            return json.dumps({"error": "XBRL URL redirected off bseindia.com", "symbol": scripcode})

        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(xbrl_resp.content, parser=parser)

        # contextRef -> {concept_localname: raw text}. Inline XBRL carries
        # the concept in the `name` attribute, not the element's own tag
        # (which is just the generic ix:nonNumeric/ix:nonFraction), so this
        # keys off `name` instead of tools.nse_tools's tag-localname match.
        facts_by_ctx: dict[str, dict[str, str]] = {}
        for el in root.iter():
            local = etree.QName(el.tag).localname
            if local not in ("nonNumeric", "nonFraction"):
                continue
            match = _IX_NAME_RE.match(el.get("name") or "")
            ctx_ref = el.get("contextRef") or ""
            if not match or not ctx_ref or not (el.text or "").strip():
                continue
            facts_by_ctx.setdefault(ctx_ref, {})[match.group(1)] = el.text.strip()

        promoters: list[dict] = []
        by_category: dict[str, list[dict]] = {}
        for ctx_ref, facts in facts_by_ctx.items():
            name = facts.get("NameOfTheShareholder")
            if not name or not ctx_ref.startswith("D_"):
                continue
            base_id = ctx_ref.removeprefix("D_")
            pct_raw = facts_by_ctx.get(base_id, {}).get("ShareholdingAsAPercentageOfTotalNumberOfShares")
            if pct_raw is None:
                continue
            try:
                pct_val = float(pct_raw)
            except ValueError:
                continue
            is_promoter = "TypeOfPromoterShareholding" in facts
            holding_pct = _plausible_holding_pct(pct_val, plausible_max=100.0 if is_promoter else 30.0)
            if holding_pct is None:
                continue
            entry = {"name": name, "holding_pct": holding_pct}
            if is_promoter:
                promoters.append(entry)
            else:
                category_raw = re.sub(r"_Context\d+$", "", base_id)
                label = _humanize_category(category_raw) if category_raw else "Other"
                by_category.setdefault(label, []).append(entry)

        promoters.sort(key=lambda x: x["holding_pct"], reverse=True)
        shareholder_categories = [
            {"category": label, "holders": sorted(holders, key=lambda x: x["holding_pct"], reverse=True)[:20]}
            for label, holders in sorted(
                by_category.items(), key=lambda kv: sum(h["holding_pct"] for h in kv[1]), reverse=True,
            )
        ]

        return json.dumps({
            "symbol": scripcode,
            "as_of_date": as_of_date,
            "promoters": promoters[:20],
            "shareholder_categories": shareholder_categories,
        })
    except Exception as e:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "bse_shareholding_detail_failed", level="warning", scripcode=scripcode, error=str(e))
        return json.dumps({"error": str(e), "symbol": scripcode})
