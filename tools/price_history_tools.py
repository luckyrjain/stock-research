"""Daily-close OHLCV series fetch for one symbol, shared by the sparkline
endpoint (`GET /api/prices/history/{symbol}` in api.py) and the technical
signal (`signals/technical.py`) — both need the same yfinance .NS/.BO
fallback and the same `price_history` cache (6 h TTL,
output/<SYMBOL>/price_history.json), so this is the one place that talks to
yfinance for it. Extracted out of api.py rather than duplicated, matching
this repo's shared-helper convention (e.g. api._fetch_live_price_sync).
"""
import cache

# api.py's GET /api/prices/history/{symbol} accepts days in [7, 365] — the
# cache always fetches/stores this maximum window and every caller-specific
# `days` value trims from that one shared entry, rather than each `days`
# value getting its own cache key. Without this, a short-range request
# (e.g. days=30) would populate the shared "price_history" cache key with
# too few rows, and a later, longer-range request within the same 6 h TTL
# window (e.g. signals/technical.py's default days=180) would wrongly reuse
# that too-short series — silently degrading the technical signal to
# UNKNOWN for up to 6 h. Matches api.py's own Query(180, ge=7, le=365) upper
# bound, so this cache can serve every valid `days` value a caller could ask.
_MAX_DAYS = 365


def _trim(series: dict, days: int) -> dict:
    """Return only the most recent `days` rows — the cached series always
    covers the maximum window (see _MAX_DAYS), so a caller asking for fewer
    days than what's cached gets a real, freshly trimmed slice rather than
    a separate, possibly-stale fetch. Rows are trading days, not calendar
    days, so this is a close-enough approximation of "the last N calendar
    days" rather than an exact one — acceptable for a sparkline/technical-
    signal input, and no worse than yfinance's own "period=Nd" semantics
    already were for a fresh fetch."""
    dates = series.get("dates", [])
    closes = series.get("closes", [])
    return {
        **{k: v for k, v in series.items() if k not in ("dates", "closes", "_meta")},
        "dates": dates[-days:] if days < len(dates) else dates,
        "closes": closes[-days:] if days < len(closes) else closes,
    }


def get_price_series(symbol: str, days: int = 180) -> dict:
    """Cached daily-close series, trimmed to the caller's requested `days`.
    Returns {"symbol", "exchange", "dates", "closes"} — "dates"/"closes" are
    [] (never an error dict) if neither the NSE nor BSE suffix returned
    usable data; callers already degrade gracefully on an empty series, the
    same convention as the rest of this codebase's "never invent" data."""
    sym = symbol.upper().strip()
    cached = cache.load(sym, "price_history")
    if cached and len(cached.get("closes", [])) >= 5:
        return _trim({k: v for k, v in cached.items() if k != "_meta"}, days)

    import yfinance as yf
    for suffix, exch in ((".NS", "NSE"), (".BO", "BSE")):
        try:
            df = yf.Ticker(sym + suffix).history(period=f"{_MAX_DAYS}d", interval="1d", auto_adjust=True)
            if df.empty:
                continue
            result = {
                "symbol":   sym,
                "exchange": exch,
                "dates":    [d.strftime("%Y-%m-%d") for d in df.index],
                "closes":   [round(float(c), 2) for c in df["Close"].tolist()],
            }
            cache.save(sym, "price_history", result)
            return _trim(result, days)
        except Exception:
            continue
    return {"symbol": sym, "exchange": None, "dates": [], "closes": []}
