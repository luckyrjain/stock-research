import json
import requests
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


def _fetch_soup(symbol: str) -> BeautifulSoup:
    url = f"https://www.screener.in/company/{symbol.upper()}/"
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def _clean(text: str) -> str:
    return text.strip().replace("₹", "").replace(",", "").strip()


@tool("Get Screener.in Fundamentals")
def get_fundamentals(symbol: str) -> str:
    """Scrape key financial ratios and fundamentals from Screener.in for an Indian stock.
    Returns Market Cap, Current Price, P/E, Book Value, Dividend Yield, ROCE, ROE,
    Sales, Net Profit, and company description.
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

        about_el = (
            soup.select_one(".company-profile p")
            or soup.select_one("#about p")
        )
        about = about_el.get_text(strip=True)[:600] if about_el else ""

        return json.dumps({"symbol": symbol.upper(), "ratios": ratios, "about": about})
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


@tool("Get Shareholding Pattern and Mutual Fund Holdings")
def get_holdings(symbol: str) -> str:
    """Scrape shareholding pattern (Promoters %, FIIs %, DIIs %, Public %) and top mutual fund
    holdings from Screener.in for an Indian stock. Returns latest quarter data.
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
                for row in table.select("tbody tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        # Strip trailing "+" tooltip indicator and whitespace
                        category = cells[0].get_text(strip=True).rstrip("+").strip()
                        latest = cells[-1].get_text(strip=True).replace("%", "").strip()
                        if category and latest and category != "No. of Shareholders":
                            try:
                                result["shareholding_pattern"][category] = float(latest)
                            except ValueError:
                                pass

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})
