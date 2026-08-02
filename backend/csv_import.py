"""
Broker CSV/XLSX Import
======================
Generic column-mapping importer for broker tradebooks (stock buy/sell
transactions). Zerodha's tradebook is auto-detected; any other export is
mapped by the user in the UI (mapping remembered client-side). Rows are
appended with content-key dedupe — imports never delete.

New assets are resolved against tools.securities_master.resolve_symbol()
before creation: an ISIN/exact-code match substitutes the verified NSE/BSE
symbol; a fuzzy/unresolved match keeps the broker's raw code as-is (never a
silent guess) and adds a warning naming the unverified symbol (plus the
closest fuzzy-match guess, if any) — once per distinct new symbol in the
file, not once per row, since resolution only runs the first time a given
symbol is seen (subsequent rows for the same symbol reuse the cached
asset id).
"""

import csv
import io

from dotenv import load_dotenv

from observability import get_logger, log_event
from tools.securities_master import get_full_securities_master, resolve_symbol

load_dotenv()
LOGGER = get_logger("csv_import")

REQUIRED_FIELDS = ("date", "symbol", "side", "quantity", "price")

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y")
_BUY_ALIASES = {"buy", "b", "bought", "purchase"}
_SELL_ALIASES = {"sell", "s", "sold"}


def parse_broker_file(file_bytes: bytes, filename: str) -> dict:
    """Parse a broker export into headers + string rows. Never raises."""
    if filename.lower().endswith(".xlsx"):
        try:
            import pandas as pd
            df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {"error": f"Unreadable spreadsheet: {exc}"}
        headers = [str(c).strip() for c in df.columns]
        rows = [["" if pd.isna(v) else str(v).strip() for v in row]
                for row in df.itertuples(index=False)]
    else:
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        table = [r for r in csv.reader(io.StringIO(text), dialect)
                 if any(c.strip() for c in r)]
        if not table:
            return {"error": "Empty file."}
        headers = [c.strip() for c in table[0]]
        rows = [[c.strip() for c in r] for r in table[1:]]
    if not headers or not rows:
        return {"error": "No data rows found."}
    return {"headers": headers, "rows": rows}


def suggest_mapping(headers: list[str]) -> dict:
    """Suggest field -> header mapping; detect the Zerodha tradebook."""
    low = [h.lower().strip() for h in headers]
    lowset = set(low)

    def _find(*exact: str, contains: tuple[str, ...] = ()) -> str | None:
        for cand in exact:
            if cand in lowset:
                return headers[low.index(cand)]
        for i, h in enumerate(low):
            if any(key in h for key in contains):
                return headers[i]
        return None

    if ({"isin", "trade_date", "trade_type", "quantity", "price"} <= lowset
            and ("symbol" in lowset or "tradingsymbol" in lowset)):
        return {"mapping": {
            "date": headers[low.index("trade_date")],
            "symbol": _find("tradingsymbol", "symbol"),
            "side": headers[low.index("trade_type")],
            "quantity": headers[low.index("quantity")],
            "price": headers[low.index("price")],
            "amount": None,
            "isin": headers[low.index("isin")],
        }, "detected": "zerodha"}

    return {"mapping": {
        "date": _find("date", contains=("date",)),
        "symbol": _find("symbol", "tradingsymbol", contains=("symbol", "scrip", "stock")),
        "side": _find("side", contains=("buy/sell", "side", "action", "type")),
        "quantity": _find("qty", "quantity", contains=("qty", "quantity")),
        "price": _find("price", "rate", contains=("price", "rate")),
        "amount": _find("net amount", contains=("net amount", "amount", "value")),
        "isin": _find("isin", contains=("isin",)),
    }, "detected": None}


def _parse_date(value: str):
    from datetime import datetime
    for candidate in (value, value.split(" ")[0], value.split("T")[0]):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _parse_num(value: str) -> float | None:
    cleaned = value.replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_rows(rows: list[list[str]], headers: list[str],
                    mapping: dict) -> tuple[list[dict], int, list[str]]:
    """Map + validate raw rows. Returns (txns, skipped_count, warnings).
    Row numbers in warnings are 1-based counting the header as row 1."""
    idx = {field: headers.index(col) for field, col in mapping.items()
           if col in headers}

    def _cell(row: list[str], field: str) -> str:
        i = idx.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    txns: list[dict] = []
    warnings: list[str] = []
    skipped = 0
    for n, row in enumerate(rows, start=2):
        symbol = _cell(row, "symbol").upper()
        d = _parse_date(_cell(row, "date"))
        side_raw = _cell(row, "side").lower()
        side = ("buy" if side_raw in _BUY_ALIASES
                else "sell" if side_raw in _SELL_ALIASES else None)
        units = _parse_num(_cell(row, "quantity"))
        price = _parse_num(_cell(row, "price"))
        amount = _parse_num(_cell(row, "amount"))
        problem = (None if symbol else "missing symbol") \
            or (None if d else "unparseable date") \
            or (None if side else f"unrecognized side '{side_raw}'") \
            or (None if units else "bad quantity") \
            or (None if (amount is not None or price is not None) else "missing price/amount")
        if problem:
            skipped += 1
            warnings.append(f"row {n}: {problem} — skipped")
            continue
        if amount is None:
            amount = units * price
        txns.append({"symbol": symbol, "isin": _cell(row, "isin") or None,
                     "date": d, "type": side, "units": abs(units),
                     "amount": abs(amount)})
    return txns, skipped, warnings


def import_rows(engine, rows: list[list[str]], headers: list[str],
                mapping: dict, account_id: int, broker: str) -> dict:
    """Append normalized transactions with dedupe; derive holdings units.
    All writes in one transaction. Never raises."""
    from sqlalchemy import insert as _insert, select, text as _text
    from db.models import (
        accounts as accounts_t, assets as assets_t,
        transactions as transactions_t,
    )

    txns, skipped, warnings = _normalize_rows(rows, headers, mapping)
    summary = {"rows": len(rows), "imported": 0, "duplicates": 0,
               "skipped": skipped, "assets_created": 0, "assets_matched": 0,
               "warnings": warnings}

    def _key(d, typ, units, amount):
        return (str(d), typ, round(float(units or 0), 4), round(float(amount or 0), 2))

    # Disclosed limitation, not an oversight: this key has no row-sequence
    # component, so two genuinely distinct same-day trades that happen to
    # share identical (date, type, units, amount) — e.g. one order that
    # fills as two separate same-price/same-quantity legs — collide and the
    # second is dropped as a "duplicate" even on a first-ever import. The
    # design deliberately chose content-key dedupe over a sequence number to
    # make re-imports of a date-ranged partial tradebook idempotent without
    # needing the file's own row order to stay stable across re-exports.

    with engine.begin() as conn:
        if not conn.execute(select(accounts_t.c.id)
                            .where(accounts_t.c.id == account_id)).first():
            return {"error": "account not found"}

        existing = conn.execute(
            select(assets_t.c.id, assets_t.c.symbol, assets_t.c.meta)
            .where(assets_t.c.type == "stock")
        ).mappings().fetchall()
        by_symbol = {r["symbol"]: r["id"] for r in existing if r["symbol"]}
        by_isin = {(r["meta"] or {}).get("isin"): r["id"]
                   for r in existing if (r["meta"] or {}).get("isin")}

        asset_ids: dict[str, int] = {}
        seen_keys: dict[int, set] = {}
        securities_master: list[dict] | None = None
        for t in txns:
            aid = asset_ids.get(t["symbol"])
            if aid is None:
                aid = by_symbol.get(t["symbol"]) \
                    or (by_isin.get(t["isin"]) if t["isin"] else None)
                if aid:
                    summary["assets_matched"] += 1
                else:
                    if securities_master is None:
                        securities_master = get_full_securities_master(engine)
                    resolved = resolve_symbol(engine, t["symbol"], isin=t["isin"],
                                               master=securities_master)
                    if resolved["confidence"] in ("isin", "exact"):
                        final_symbol = resolved["symbol"]
                        meta = {"isin": t["isin"], "broker": broker,
                                "resolved_exchange": resolved["exchange"]}
                    else:
                        final_symbol = t["symbol"]
                        meta = {"isin": t["isin"], "broker": broker}
                        note = f"symbol '{t['symbol']}' unverified"
                        if resolved["candidate_name"]:
                            note += f" (closest guess: {resolved['candidate_name']})"
                        summary["warnings"].append(note)
                    aid = by_symbol.get(final_symbol)
                    if aid:
                        summary["assets_matched"] += 1
                    else:
                        aid = conn.execute(_insert(assets_t).values(
                            account_id=account_id, type="stock",
                            name=final_symbol, symbol=final_symbol,
                            meta=meta,
                        ).returning(assets_t.c.id)).scalar()
                        by_symbol[final_symbol] = aid
                        summary["assets_created"] += 1
                asset_ids[t["symbol"]] = aid
                seen_keys[aid] = {
                    _key(r[0], r[1], r[2], r[3]) for r in conn.execute(
                        select(transactions_t.c.date, transactions_t.c.type,
                               transactions_t.c.units, transactions_t.c.amount)
                        .where(transactions_t.c.asset_id == aid,
                               transactions_t.c.meta["source"].as_string() == "csv"))
                }

            key = _key(t["date"], t["type"], t["units"], t["amount"])
            if key in seen_keys[aid]:
                summary["duplicates"] += 1
                continue
            conn.execute(_insert(transactions_t).values(
                asset_id=aid, date=t["date"], type=t["type"],
                amount=t["amount"], units=t["units"],
                meta={"source": "csv", "broker": broker},
            ))
            seen_keys[aid].add(key)
            summary["imported"] += 1

        for symbol, aid in asset_ids.items():
            row = conn.execute(_text(
                "SELECT COALESCE(SUM(CASE WHEN type='buy' THEN units END), 0) "
                "     - COALESCE(SUM(CASE WHEN type='sell' THEN units END), 0) "
                "FROM transactions WHERE asset_id = :aid"
            ), {"aid": aid}).scalar()
            units = float(row or 0)
            if units < 0:
                summary["warnings"].append(
                    f"{symbol}: derived units negative ({units}) — floored to 0; "
                    "tradebook likely incomplete")
                units = 0.0
            conn.execute(_text(
                "INSERT INTO holdings (asset_id, units) VALUES (:aid, :u) "
                "ON CONFLICT (asset_id) DO UPDATE SET units = EXCLUDED.units"
            ), {"aid": aid, "u": units})
        if asset_ids:
            summary["warnings"].append(
                "derived units exclude bonus/split shares — verify against your broker")

    log_event(LOGGER, "csv_imported", account_id=account_id, broker=broker,
              **{k: v for k, v in summary.items() if k != "warnings"},
              warnings=len(summary["warnings"]))
    return summary
