"""
Portfolio Valuation Engine
==========================
Auto-values mf/stock holdings from the EOD price store (prices_daily /
mf_nav_daily) into the existing `valuations` table (asset_id + as_of=today
upsert), and computes XIRR from `transactions` where they exist.

Runs nightly as the final isolated step of eod_prices_pipeline.run(), on
demand via POST /api/portfolio/refresh-valuations, or standalone:

    python portfolio/portfolio_valuation.py

XIRR returns null for an asset with no rows in `transactions` — expected
for manually-entered assets, or before a CAS PDF (portfolio/cas_import.py) or broker
CSV (portfolio/csv_import.py) import has run for it. Both of those write real
transaction rows; this function reads whatever they've written so far.
"""

from datetime import date

from dotenv import load_dotenv
from sqlalchemy import select, text

from core.observability import get_logger, log_event

load_dotenv()
LOGGER = get_logger("portfolio_valuation")

# transactions.type → cashflow sign; other types carry no cashflow for XIRR
_FLOW_SIGNS = {"buy": -1.0, "sell": 1.0, "dividend": 1.0}

_XIRR_LO, _XIRR_HI = -0.99, 10.0


# ── XIRR ──────────────────────────────────────────────────────────────────────

def xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """Annualized internal rate of return for dated cashflows.

    Newton's method with bisection fallback, rate bounded [-0.99, 10.0].
    Returns None for fewer than 2 flows, all-same-sign flows, or
    non-convergence.
    """
    flows = [(d, float(a)) for d, a in cashflows]
    if len(flows) < 2:
        return None
    if not (any(a > 0 for _, a in flows) and any(a < 0 for _, a in flows)):
        return None

    t0 = min(d for d, _ in flows)
    times = [((d - t0).days / 365.0, a) for d, a in flows]

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** t for t, a in times)

    def npv_prime(rate: float) -> float:
        return sum(-t * a / (1.0 + rate) ** (t + 1.0) for t, a in times)

    # Newton from a neutral starting guess
    rate = 0.1
    for _ in range(50):
        f = npv(rate)
        if abs(f) < 1e-8:
            return rate if _XIRR_LO <= rate <= _XIRR_HI else None
        fp = npv_prime(rate)
        if fp == 0.0:
            break
        nxt = rate - f / fp
        if not (_XIRR_LO < nxt < _XIRR_HI):
            break
        if abs(nxt - rate) < 1e-10:
            return nxt
        rate = nxt

    # Bisection fallback over the bounded interval
    lo, hi = _XIRR_LO, _XIRR_HI
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0.0:
        return lo
    if f_hi == 0.0:
        return hi
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-8 or (hi - lo) < 1e-10:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return None


# ── Price lookups ─────────────────────────────────────────────────────────────

def _latest_close(engine, symbol: str) -> tuple[float, str] | None:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT close, trade_date FROM prices_daily "
            "WHERE symbol = :s AND close IS NOT NULL "
            "ORDER BY trade_date DESC LIMIT 1"
        ), {"s": symbol}).first()
    return (float(row[0]), str(row[1])) if row else None


def _latest_nav(engine, scheme_code: str) -> tuple[float, str] | None:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT nav, nav_date FROM mf_nav_daily "
            "WHERE scheme_code = :c ORDER BY nav_date DESC LIMIT 1"
        ), {"c": scheme_code}).first()
    return (float(row[0]), str(row[1])) if row else None


def _yfinance_price(symbol: str) -> float | None:
    """Live-quote fallback when a stock is missing from prices_daily."""
    try:
        import yfinance as yf  # local import: keeps tests free of yfinance
    except ImportError:
        return None
    for suffix in (".NS", ".BO"):
        try:
            hist = yf.Ticker(f"{symbol}{suffix}").history(period="5d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return None


# ── Valuation refresh ─────────────────────────────────────────────────────────

def refresh_valuations(engine) -> dict:
    """Value every non-archived mf/stock asset that has a holdings row.

    value = units × latest price, upserted into valuations
    (asset_id, as_of=today). Per-asset failures skip and count — the prior
    valuation stands. Never raises.
    """
    with engine.connect() as conn:
        candidates = conn.execute(text(
            "SELECT a.id, a.type, a.name, a.symbol, h.units "
            "FROM assets a JOIN holdings h ON h.asset_id = a.id "
            "WHERE a.type IN ('mf', 'stock') AND NOT a.archived "
            "ORDER BY a.id"
        )).mappings().fetchall()

    today = date.today()
    details: list[dict] = []
    for row in candidates:
        detail = {"asset_id": row["id"], "name": row["name"],
                  "type": row["type"], "symbol": row["symbol"]}
        symbol = (row["symbol"] or "").strip()
        if not symbol:
            detail.update(status="skipped", reason="no symbol")
            details.append(detail)
            continue

        if row["type"] == "stock":
            found = _latest_close(engine, symbol)
            if found is None:
                live = _yfinance_price(symbol)
                found = (live, "live") if live is not None else None
            reason = "no price in prices_daily or yfinance"
        else:
            found = _latest_nav(engine, symbol)
            reason = "no NAV for scheme code"

        if found is None:
            detail.update(status="skipped", reason=reason)
            details.append(detail)
            continue

        price, price_date = found
        value = round(float(row["units"]) * price, 2)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO valuations (asset_id, as_of, value) "
                "VALUES (:aid, :as_of, :value) "
                "ON CONFLICT (asset_id, as_of) DO UPDATE SET value = EXCLUDED.value"
            ), {"aid": row["id"], "as_of": today, "value": value})
        detail.update(status="valued", price=price, price_date=price_date, value=value)
        details.append(detail)

    valued = sum(1 for d in details if d["status"] == "valued")
    skipped = len(details) - valued
    for d in details:
        if d["status"] == "skipped":
            log_event(LOGGER, "valuation_skipped", level="warning", **d)
    log_event(LOGGER, "valuations_refreshed", valued=valued, skipped=skipped)
    return {"valued": valued, "skipped": skipped, "details": details}


# ── XIRR report ───────────────────────────────────────────────────────────────

def xirr_report(engine, profile_id: int) -> dict:
    """Per-asset and portfolio XIRR for a profile.

    Cashflows come from `transactions` (buy → −amount, sell/dividend →
    +amount); the terminal flow is the asset's latest valuation. Assets
    without transactions get null and are excluded from the portfolio pool.
    """
    from db.models import accounts, assets, transactions, valuations

    with engine.connect() as conn:
        asset_rows = conn.execute(
            select(assets.c.id, assets.c.name)
            .select_from(assets.join(accounts, accounts.c.id == assets.c.account_id))
            .where(accounts.c.profile_id == profile_id, ~assets.c.archived)
            .order_by(assets.c.id)
        ).fetchall()
        asset_ids = [r[0] for r in asset_rows]

        flows_by_asset: dict[int, list[tuple[date, float]]] = {}
        latest_val: dict[int, tuple[date, float]] = {}
        if asset_ids:
            for aid, d, typ, amount in conn.execute(
                select(transactions.c.asset_id, transactions.c.date,
                       transactions.c.type, transactions.c.amount)
                .where(transactions.c.asset_id.in_(asset_ids))
            ):
                sign = _FLOW_SIGNS.get(typ)
                if sign is None or amount is None:
                    continue
                flows_by_asset.setdefault(aid, []).append((d, sign * float(amount)))
            for aid, as_of, value in conn.execute(
                select(valuations.c.asset_id, valuations.c.as_of, valuations.c.value)
                .where(valuations.c.asset_id.in_(asset_ids))
                .order_by(valuations.c.asset_id, valuations.c.as_of)
            ):
                latest_val[aid] = (as_of, float(value))   # last as_of wins

    pooled: list[tuple[date, float]] = []
    per_asset = []
    for aid, name in asset_rows:
        flows = flows_by_asset.get(aid)
        rate = None
        if flows and aid in latest_val:
            as_of, value = latest_val[aid]
            rate = xirr(flows + [(as_of, value)])
            pooled.extend(flows)
            pooled.append((as_of, value))
        per_asset.append({"asset_id": aid, "name": name, "xirr": rate})

    return {"portfolio_xirr": xirr(pooled), "assets": per_asset}


if __name__ == "__main__":
    from db.models import get_engine
    summary = refresh_valuations(get_engine())
    print(f"valued={summary['valued']} skipped={summary['skipped']}")
