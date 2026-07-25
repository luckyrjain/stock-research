"""Daily-close OHLCV series fetch for one symbol, shared by the sparkline
endpoint (`GET /api/prices/history/{symbol}` in api.py) and the technical
signal (`signals/technical.py`) — both need the same yfinance .NS/.BO
fallback and the same `price_history` cache (6 h TTL,
output/<SYMBOL>/price_history.json), so this is the one place that talks to
yfinance for it. Extracted out of api.py rather than duplicated, matching
this repo's shared-helper convention (e.g. api._fetch_live_price_sync).
"""
import cache


def get_price_series(symbol: str, days: int = 180) -> dict:
    """Cached daily-close series. Returns {"symbol", "exchange", "dates",
    "closes"} — "dates"/"closes" are [] (never an error dict) if neither the
    NSE nor BSE suffix returned usable data; callers already degrade
    gracefully on an empty series, the same convention as the rest of this
    codebase's "never invent" data."""
    sym = symbol.upper().strip()
    cached = cache.load(sym, "price_history")
    if cached and len(cached.get("closes", [])) >= 5:
        return {k: v for k, v in cached.items() if k != "_meta"}

    import yfinance as yf
    for suffix, exch in ((".NS", "NSE"), (".BO", "BSE")):
        try:
            df = yf.Ticker(sym + suffix).history(period=f"{days}d", interval="1d", auto_adjust=True)
            if df.empty:
                continue
            result = {
                "symbol":   sym,
                "exchange": exch,
                "dates":    [d.strftime("%Y-%m-%d") for d in df.index],
                "closes":   [round(float(c), 2) for c in df["Close"].tolist()],
            }
            cache.save(sym, "price_history", result)
            return result
        except Exception:
            continue
    return {"symbol": sym, "exchange": None, "dates": [], "closes": []}
