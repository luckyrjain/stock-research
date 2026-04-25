import json
import requests
import yfinance as yf
from lxml import etree
from crewai.tools import tool

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com",
}

_NSE_BASE = "https://www.nseindia.com"


def _nse_session() -> requests.Session:
    session = requests.Session()
    session.get(_NSE_BASE, headers=_NSE_HEADERS, timeout=10)
    return session


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
        "dividend_yield_pct": round(info.get("dividendYield", 0) * 100, 2) if info.get("dividendYield") else 0,
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


@tool("Get NSE Mutual Fund Holdings")
def get_mf_holdings(symbol: str) -> str:
    """Fetch the top mutual funds holding an NSE-listed Indian stock from NSE's shareholding XBRL data.
    Returns fund names, number of shares, and percentage of total shares held.
    Input: NSE stock symbol, e.g. RELIANCE, TCS, INFY."""
    try:
        session = _nse_session()
        master_url = f"{_NSE_BASE}/api/corporate-share-holdings-master?index=equities&symbol={symbol.upper()}"
        master_resp = session.get(master_url, headers=_NSE_HEADERS, timeout=15)
        master_resp.raise_for_status()
        records = master_resp.json()

        if not records:
            return json.dumps({"error": "No shareholding records found", "symbol": symbol})

        # Pick the most recent record (last by date)
        latest = sorted(records, key=lambda r: r.get("date", ""), reverse=True)[0]
        xbrl_url = latest.get("xbrl", "")
        if not xbrl_url:
            return json.dumps({"error": "No XBRL URL in shareholding record", "symbol": symbol})

        xbrl_resp = requests.get(xbrl_url, headers={"User-Agent": _NSE_HEADERS["User-Agent"]}, timeout=20)
        xbrl_resp.raise_for_status()
        root = etree.fromstring(xbrl_resp.content)

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
                # ctx_ref without "D_" prefix is the numeric context
                ctx_pct[ctx_ref] = float(elem.text)

        funds = []
        for ctx_id, name in ctx_name.items():
            pct = ctx_pct.get(ctx_id)
            if pct is not None:
                funds.append({
                    "fund": name,
                    "holding_pct": round(pct * 100, 4) if pct < 1 else round(pct, 4),
                })

        funds.sort(key=lambda x: x["holding_pct"], reverse=True)

        return json.dumps({
            "symbol": symbol.upper(),
            "as_of_date": latest.get("date", ""),
            "mutual_funds": funds[:15],
        })
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})
