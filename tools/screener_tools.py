import json
import re
import requests
from urllib.parse import quote
from bs4 import BeautifulSoup
from crewai.tools import tool

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _resolve_screener_slug(symbol: str) -> str:
    """Return the Screener slug for a symbol, falling back to search if direct URL 404s."""
    upper = symbol.upper()
    resp = requests.get(f"https://www.screener.in/company/{quote(upper)}/", headers=_HEADERS, timeout=10)
    if resp.status_code == 200:
        return upper
    # 404 or redirect — search for the slug
    search = requests.get(
        "https://www.screener.in/api/company/search/",
        params={"q": upper},
        headers={"User-Agent": _HEADERS["User-Agent"]},
        timeout=6,
    )
    results = search.json() if search.ok else []
    if results:
        parts = (results[0].get("url") or "").strip("/").split("/")
        slug = parts[1] if len(parts) >= 2 else parts[0] if parts else ""
        if slug:
            return slug
    return upper  # best effort


def _fetch_soup(symbol: str, slug: str | None = None) -> BeautifulSoup:
    slug = slug or _resolve_screener_slug(symbol)
    url = f"https://www.screener.in/company/{quote(slug)}/"
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def _clean(text: str) -> str:
    return text.strip().replace("₹", "").replace(",", "").strip()


def _extract_compounded_growth(text: str, block_label: str, period_label: str) -> str:
    """Extract CAGR-style growth percentages from Screener summary blocks."""
    pattern = rf"{re.escape(block_label)}.*?{re.escape(period_label)}\s*:\s*([+-]?\d+(?:\.\d+)?%)"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def _extract_growth_metrics(soup: BeautifulSoup) -> dict[str, str]:
    """Extract 3Y/5Y sales and profit growth from Screener growth blocks."""
    metrics: dict[str, str] = {}
    block_map = {
        "Compounded Sales Growth": "Sales growth",
        "Compounded Profit Growth": "Profit growth",
    }

    for block_label, output_prefix in block_map.items():
        block_text = ""
        other_labels_lower = [lbl.lower() for lbl in block_map if lbl != block_label]
        # Pick the SHORTEST matching container, not the first one in
        # document order. find_all() visits ancestors before their
        # children, so a wrapping div/section around both the Sales-growth
        # and Profit-growth widgets would otherwise win immediately (its
        # combined text trivially contains both labels) and silently hand
        # back whichever growth block's numbers happen to appear first in
        # that combined text — mislabeling Profit growth with Sales growth
        # values or vice versa. The most specific container that still
        # contains the label is always the shortest one, since any
        # ancestor's text is a superset of it.
        for tag in soup.find_all(["section", "div", "table"]):
            text = " ".join(tag.stripped_strings)
            text_lower = text.lower()
            if block_label.lower() not in text_lower:
                continue
            # Must also carry at least one period marker itself — otherwise
            # this candidate is just the label's own leaf element (no
            # numbers alongside it), and picking it would leave block_text
            # non-empty but un-extractable, silently skipping this block
            # instead of falling through to the full-text-scan fallback.
            if "3 years" not in text_lower and "5 years" not in text_lower:
                continue
            # A candidate that ALSO contains a sibling block's own label is
            # a shared container spanning multiple growth blocks (e.g. every
            # growth metric laid out as flat sibling rows inside one table,
            # with no per-block wrapper) — the "shortest" one here can still
            # be the only candidate for both labels, and a later regex
            # search over it would grab whichever period appears first,
            # mislabeling one block with another's values. Skip it and let
            # this block fall through to the label-anchored full-text regex
            # fallback below, whose pattern only starts matching after this
            # exact block's own label and so isn't fooled by a shared table.
            if any(other in text_lower for other in other_labels_lower):
                continue
            if not block_text or len(text) < len(block_text):
                block_text = text

        if not block_text:
            full_text = soup.get_text("\n", strip=True)
            three_year = _extract_compounded_growth(full_text, block_label, "3 Years")
            five_year = _extract_compounded_growth(full_text, block_label, "5 Years")
            if three_year:
                metrics[f"{output_prefix} 3Y"] = three_year
            if five_year:
                metrics[f"{output_prefix} 5Y"] = five_year
            continue

        for period in ("3 Years", "5 Years"):
            match = re.search(
                rf"{re.escape(period)}\s*:?\s*([+-]?\d+(?:\.\d+)?%)",
                block_text,
                flags=re.IGNORECASE,
            )
            if match:
                metrics[f"{output_prefix} {'3Y' if period == '3 Years' else '5Y'}"] = match.group(1)

    return metrics


def _extract_latest_metric_from_tables(soup: BeautifulSoup, labels: tuple[str, ...]) -> str:
    """Find the latest value for a metric row from visible tables."""
    normalized_labels = tuple(label.lower().replace(" ", "") for label in labels)

    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        row_label = _clean(cells[0].get_text(" ", strip=True)).lower().replace(" ", "")
        if not any(lbl in row_label for lbl in normalized_labels):
            continue

        values: list[str] = []
        for cell in cells[1:]:
            value = _clean(cell.get_text(" ", strip=True))
            if not value or value == "-":
                continue
            values.append(value)
        if values:
            return values[-1]
    return ""


def _extract_quarterly_trend(soup: BeautifulSoup, max_periods: int = 8) -> dict:
    """Sales/EPS/operating-margin mini-trend from Screener's Quarterly Results
    table (section#quarters) — the same company page `get_fundamentals`
    already fetches, so this is free (no extra network round trip).
    Oldest-first, capped to the most recent `max_periods` quarters, matching
    PriceHistory's convention. Returns {} rather than a partial/misaligned
    series if the Sales or EPS row is missing or any of the last
    `max_periods` cells doesn't parse as a number (e.g. "-" for a pre-IPO
    period) — never guess at a gap. Operating margin (OPM %) is a genuinely
    optional third line on top of that: Screener reports it as its own
    percentage row (never derived/computed here), but several sectors
    (banks, NBFCs) routinely omit that row entirely — absent, not guessed,
    when it isn't cleanly present for the same window as revenue/eps."""
    section = soup.find("section", {"id": "quarters"})
    if not section:
        return {}
    table = section.find("table")
    if not table:
        return {}

    header_cells = table.select("thead tr th")
    periods = [_clean(th.get_text(" ", strip=True)) for th in header_cells[1:]]

    def _row_values(row_labels: tuple[str, ...]) -> list[float | None] | None:
        normalized = tuple(label.lower().replace(" ", "") for label in row_labels)
        for row in table.select("tbody tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            row_label = _clean(cells[0].get_text(" ", strip=True)).lower().replace(" ", "")
            if not any(lbl in row_label for lbl in normalized):
                continue
            values: list[float | None] = []
            for cell in cells[1:]:
                raw = _clean(cell.get_text(" ", strip=True)).replace("%", "")
                try:
                    values.append(float(raw))
                except ValueError:
                    values.append(None)
            return values
        return None

    revenue = _row_values(("Sales", "Revenue"))
    eps = _row_values(("EPS in Rs", "EPS"))
    if not periods or revenue is None or eps is None:
        return {}

    n = min(len(periods), len(revenue), len(eps), max_periods)
    periods_n, revenue_n, eps_n = periods[-n:], revenue[-n:], eps[-n:]
    if n < 2 or any(v is None for v in revenue_n) or any(v is None for v in eps_n):
        return {}

    result = {"quarters": periods_n, "revenue": revenue_n, "eps": eps_n}

    opm = _row_values(("OPM %", "OPM"))
    if opm is not None and len(opm) >= n:
        opm_n = opm[-n:]
        if not any(v is None for v in opm_n):
            result["operating_margin"] = opm_n

    return result


def _extract_valuation_band(soup: BeautifulSoup, max_years: int = 5) -> dict:
    """Yearly historical Price to Earning band from Screener's own Ratios table
    (section#ratios, on the same company page get_fundamentals/get_peer_comparison
    already fetch — no extra network round trip), the real published number
    Screener computes per year, not a value derived/estimated here. Oldest-first,
    capped to the most recent `max_years`, same "never guess a gap" alignment
    convention as `_extract_quarterly_trend`: returns {} if the row is missing,
    fewer than 3 years are available (too thin a sample for a meaningful band),
    or any cell in the last-n window doesn't parse as a number.

    **Disclosed limitation**: whether Screener.in actually renders a yearly
    "Price to Earning" row inside section#ratios (vs. only exposing historical
    P/E through its separate interactive chart, which this scraper does not
    call) was not verified against a live response in this sandbox (no
    outbound internet — same disclosure as the FII/DII/macro scrapers and the
    sector-taxonomy assumption in signals/engine.py). If the row isn't there
    under this id/label, this simply returns {} (same as "Screener doesn't
    have this data" elsewhere in this module) rather than raising — worth
    spot-checking against a live company page before relying on this in
    production."""
    section = soup.find("section", {"id": "ratios"})
    if not section:
        return {}
    table = section.find("table")
    if not table:
        return {}

    header_cells = table.select("thead tr th")
    years = [_clean(th.get_text(" ", strip=True)) for th in header_cells[1:]]
    if not years:
        return {}

    pe_values: list[float | None] | None = None
    for row in table.select("tbody tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        row_label = _clean(cells[0].get_text(" ", strip=True)).lower().replace(" ", "")
        if "pricetoearning" not in row_label and row_label not in ("p/e", "pe"):
            continue
        values: list[float | None] = []
        for cell in cells[1:]:
            raw = _clean(cell.get_text(" ", strip=True)).replace("%", "")
            try:
                values.append(float(raw))
            except ValueError:
                values.append(None)
        pe_values = values
        break

    if pe_values is None:
        return {}
    if len(pe_values) != len(years):
        # Row and header cell counts disagree (e.g. a trailing "TTM" column
        # only one of the two carries) — [-n:] truncation on each independently
        # would silently pair a P/E value with the wrong year rather than fail
        # loudly, so bail out instead of guessing an alignment.
        return {}

    n = min(len(years), len(pe_values), max_years)
    years_n, pe_n = years[-n:], pe_values[-n:]
    if n < 3 or any(v is None for v in pe_n):
        return {}

    return {"years": years_n, "pe": pe_n}


def _extract_yearly_statement(soup: BeautifulSoup, section_id: str, max_years: int = 10) -> dict:
    """Generic yearly-table extractor for Screener's Profit & Loss
    (section#profit-loss), Balance Sheet (section#balance-sheet), and Cash
    Flow (section#cash-flow) tables — the three multi-year statement views
    this codebase didn't have at all before (only a short quarterly
    Sales/EPS/OPM window via _extract_quarterly_trend, and a P/E-only yearly
    band via _extract_valuation_band). Deliberately generic rather than a
    hardcoded row schema, same "whatever Screener renders is what gets
    returned" instinct as _parse_peer_table — each statement's row set
    genuinely differs (a bank's balance sheet looks nothing like an FMCG
    company's), so hardcoding row labels here would silently drop real data
    for entire sectors.

    Unlike _extract_quarterly_trend's strict "every cell must parse or the
    whole row is dropped" alignment, a row here keeps a `None` for any year
    it doesn't have a clean number for (e.g. a business segment or line item
    that didn't exist yet at IPO) — across up to a decade of history that's
    an expected, real gap in the data, not a misalignment to guard against
    the way a handful of recent quarters would be.

    **Disclosed limitation**: Screener.in's exact section ids/row labels for
    these three tables were not verified against a live response in this
    sandbox (no outbound internet — same disclosure pattern as every other
    Screener/NSE scraper in this codebase). If a section id doesn't match a
    live page's actual markup, this returns {} (never guessed/fabricated
    figures) exactly like "Screener doesn't have this data" elsewhere in
    this module — worth spot-checking against a live company page before
    relying on this in production.
    """
    section = soup.find("section", {"id": section_id})
    if not section:
        return {}
    table = section.find("table")
    if not table:
        return {}

    header_cells = table.select("thead tr th")
    years = [_clean(th.get_text(" ", strip=True)) for th in header_cells[1:]]
    if not years:
        return {}
    n = min(len(years), max_years)
    years_n = years[-n:]

    rows = []
    for row in table.select("tbody tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True).rstrip("+").strip()
        if not label:
            continue
        raw_values = [_clean(c.get_text(" ", strip=True)).replace("%", "") for c in cells[1:]]
        if len(raw_values) != len(years):
            # Row/header cell counts disagree (e.g. a trailing summary column
            # only one of the two carries) — skip rather than misalign a
            # value to the wrong year, same rule _extract_valuation_band uses.
            continue
        values: list[float | None] = []
        for raw in raw_values[-n:]:
            try:
                values.append(float(raw))
            except ValueError:
                values.append(None)
        if any(v is not None for v in values):
            rows.append({"label": label, "values": values})

    if not rows:
        return {}
    return {"years": years_n, "rows": rows}


@tool("Get Screener.in Multi-Year Financial Statements")
def get_financial_statements(symbol: str) -> str:
    """Scrape Screener.in's multi-year Profit & Loss, Balance Sheet, and Cash
    Flow statement tables (up to the last 10 years each) for an Indian stock —
    the fuller financial-history view get_fundamentals' current-ratios-only
    payload and get_peer_comparison's P/E-only valuation_band don't cover.
    Each of profit_loss/balance_sheet/cash_flow is independently optional and
    absent (not guessed) when Screener doesn't have that table for this
    company. Input: NSE stock symbol, e.g. RELIANCE, TCS, INFY."""
    try:
        soup = _fetch_soup(symbol)
        payload: dict = {"symbol": symbol.upper()}

        profit_loss = _extract_yearly_statement(soup, "profit-loss")
        if profit_loss:
            payload["profit_loss"] = profit_loss

        balance_sheet = _extract_yearly_statement(soup, "balance-sheet")
        if balance_sheet:
            payload["balance_sheet"] = balance_sheet

        cash_flow = _extract_yearly_statement(soup, "cash-flow")
        if cash_flow:
            payload["cash_flow"] = cash_flow

        return json.dumps(payload)
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


@tool("Get Screener.in Fundamentals")
def get_fundamentals(symbol: str) -> str:
    """Scrape key financial ratios and fundamentals from Screener.in for an Indian stock.
    Returns Market Cap, Current Price, P/E, Book Value, Dividend Yield, ROCE, ROE,
    Sales, Net Profit, company description, and a quarterly Sales/EPS mini-trend.
    Input: NSE stock symbol, e.g. RELIANCE, TCS, INFY."""
    try:
        soup = _fetch_soup(symbol)
        ratios = {}

        for li in soup.select("#top-ratios li"):
            name_el = li.select_one(".name")
            val_el = li.select_one(".number")
            if name_el and val_el:
                key = name_el.get_text(strip=True)
                val = _clean(val_el.get_text(" ", strip=True))
                if key:
                    ratios[key] = val

        growth_metrics = _extract_growth_metrics(soup)
        ebitda_margin = _extract_latest_metric_from_tables(
            soup, ("EBITDA Margin", "EBITDA %", "Operating Profit Margin", "OPM %")
        )

        for metric_key, metric_value in growth_metrics.items():
            ratios[metric_key] = metric_value
        if ebitda_margin:
            ratios["EBITDA margin"] = ebitda_margin

        about_el = (
            soup.select_one(".company-profile p")
            or soup.select_one("#about p")
        )
        about = about_el.get_text(strip=True)[:600] if about_el else ""

        payload = {"symbol": symbol.upper(), "ratios": ratios, "about": about}
        quarterly_trend = _extract_quarterly_trend(soup)
        if quarterly_trend:
            payload["quarterly_trend"] = quarterly_trend

        # Best-effort NSE fallback — only when Screener's own ratios table
        # came back completely empty (e.g. a recent IPO Screener hasn't
        # indexed a ratios table for yet, but NSE already has a results
        # filing). A lazy import, not a module-level one: nse_tools.py
        # doesn't import this module, so there's no circularity, but this
        # keeps the dependency scoped to the one code path that needs it,
        # matching this codebase's existing lazy-import convention for
        # cross-tool composition (e.g. market_picks_pipeline.py's
        # _fetch_valuation_percentile). Never lets a fallback failure
        # affect the primary Screener payload above.
        if not ratios:
            try:
                from tools.nse_tools import get_nse_basic_ratios

                fallback = get_nse_basic_ratios(symbol)
                if fallback:
                    payload["nse_fallback_ratios"] = fallback
            except Exception:
                pass

        return json.dumps(payload)
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


def _parse_peer_table(soup: BeautifulSoup) -> dict:
    """Parse Screener's Peer comparison table (section#peers). The column set
    varies by sector (a bank's peer table looks nothing like an IT company's),
    so this is driven entirely by the table's own headers rather than a
    hardcoded schema — whatever Screener renders is what gets returned."""
    section = soup.find("section", {"id": "peers"})
    if not section:
        return {"columns": [], "rows": []}
    table = section.find("table")
    if not table:
        return {"columns": [], "rows": []}

    header_cells = table.select("thead th")
    columns = [_clean(th.get_text(" ", strip=True)) for th in header_cells]
    if not columns:
        return {"columns": [], "rows": []}

    rows = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) != len(columns):
            continue  # malformed/summary row we can't align to headers — skip rather than guess

        link = tr.find("a", href=True)
        slug = ""
        if link and link.get("href"):
            parts = link["href"].strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "company":
                slug = parts[1]

        values: dict[str, str] = {}
        for col, cell in zip(columns, cells):
            text = _clean(cell.get_text(" ", strip=True))
            if text and text != "-":
                values[col] = text

        name = values.get("Name") or (link.get_text(" ", strip=True) if link else "")
        if name:
            rows.append({"name": name, "slug": slug, "values": values})

    return {"columns": columns, "rows": rows}


@tool("Get Screener.in Peer Comparison")
def get_peer_comparison(symbol: str) -> str:
    """Scrape the Peer comparison table from Screener.in for an Indian stock: the
    company's own row, its sector peers, and Screener's own sector-median row
    when present, with whatever ratio columns Screener renders for that sector
    (typically P/E, Market Cap, Div Yield, ROCE %, and quarterly growth %).
    Also carries an optional `valuation_band`: the company's own last 3-5 years
    of Screener's yearly Price to Earning ratio, when that row is present —
    see _extract_valuation_band for its "never guess a gap" rules.
    Input: NSE stock symbol, e.g. RELIANCE, TCS, INFY."""
    try:
        slug = _resolve_screener_slug(symbol)
        soup = _fetch_soup(symbol, slug=slug)
        table = _parse_peer_table(soup)

        self_row = None
        median_row = None
        peer_rows = []
        for row in table["rows"]:
            if row["name"].strip().lower() == "median":
                median_row = row
            elif row["slug"] and row["slug"].upper() == slug.upper():
                self_row = row
            else:
                peer_rows.append(row)

        # Screener orders peers by market cap; cap at 5 per the product spec (3-5 peers).
        peer_rows = peer_rows[:5]

        payload = {
            "symbol": symbol.upper(),
            "self": self_row,
            "peers": peer_rows,
            "sector_median": median_row,
        }
        valuation_band = _extract_valuation_band(soup)
        if valuation_band:
            payload["valuation_band"] = valuation_band

        return json.dumps(payload)
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


@tool("Get Shareholding Pattern and Mutual Fund Holdings")
def get_holdings(symbol: str) -> str:
    """Scrape shareholding pattern (Promoters %, FIIs %, DIIs %, Public %), promoter
    pledge %, and top mutual fund holdings from Screener.in for an Indian stock.
    Returns latest quarter data.
    Input: NSE stock symbol, e.g. RELIANCE, TCS, INFY."""
    try:
        soup = _fetch_soup(symbol)
        result = {
            "symbol": symbol.upper(),
            "shareholding_pattern": {},
        }

        # Shareholding pattern table — latest quarter is the last column
        sh_section = soup.find("section", {"id": "shareholding"})
        if sh_section:
            table = sh_section.find("table")
            if table:
                # Don't blindly trust the last column position as "latest
                # quarter" — if Screener ever adds a trailing summary/delta
                # column (e.g. a "Q-o-Q change" column), cells[-1] would
                # silently be read as the latest shareholding % instead of
                # that delta value. Require the last header cell to not
                # look like an obvious non-period column before trusting
                # cells[-1] — same "never guess an alignment" instinct as
                # _extract_quarterly_trend/_extract_valuation_band in this
                # file, which explicitly validate header/row alignment.
                header_cells = table.select("thead tr th")
                last_header_text = (
                    _clean(header_cells[-1].get_text(" ", strip=True)).lower() if header_cells else ""
                )
                _NON_PERIOD_MARKERS = ("change", "trend", "yoy", "qoq", "delta", "diff", "%chg", "chg")
                trust_last_column = not header_cells or not any(
                    marker in last_header_text for marker in _NON_PERIOD_MARKERS
                )

                for row in table.select("tbody tr") if trust_last_column else []:
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        # Strip trailing "+" tooltip indicator and whitespace
                        category = cells[0].get_text(strip=True).rstrip("+").strip()
                        latest = cells[-1].get_text(strip=True).replace("%", "").strip()
                        if category and latest and category != "No. of Shareholders":
                            try:
                                value = float(latest)
                            except ValueError:
                                continue
                            # Screener surfaces promoter pledge as its own row
                            # (e.g. "Pledged percentage") within this same
                            # table rather than as a shareholder category —
                            # pull it into its own field, not the category dict.
                            if "pledge" in category.lower():
                                result["pledge_pct"] = value
                            else:
                                result["shareholding_pattern"][category] = value

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})
