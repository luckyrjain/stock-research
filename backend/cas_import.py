"""
CAS PDF Import
==============
Imports a CAMS/KFintech *detailed* CAS PDF into the portfolio tables:
transactions (meta.source='cas'), holdings (units = closing balance), and
missing mf assets. Parsed JSON (scrubbed of PII) is archived to
output/_cas/ for replay:

    python cas_import.py --replay output/_cas/<file>.json --account-id N
"""

import copy
import io
import json

from dotenv import load_dotenv

from observability import get_logger, log_event

load_dotenv()
LOGGER = get_logger("cas_import")

# casparser transaction type -> transactions.type
_TXN_TYPE_MAP = {
    "PURCHASE": "buy", "PURCHASE_SIP": "buy",
    "SWITCH_IN": "buy", "SWITCH_IN_MERGER": "buy",
    "REDEMPTION": "sell", "SWITCH_OUT": "sell", "SWITCH_OUT_MERGER": "sell",
    "DIVIDEND_PAYOUT": "dividend",
    "DIVIDEND_REINVEST": "dividend_reinvest",   # no external cashflow; XIRR ignores
}
# informational rows: never cashflows for XIRR, not stored
_TXN_SKIP = {"STT_TAX", "STAMP_DUTY_TAX", "TDS_TAX", "MISC"}


def parse_cas(pdf_bytes: bytes, password: str) -> dict:
    """Parse a CAS PDF. Returns the parsed dict, or {"error": ...}. Never raises."""
    import casparser
    from casparser.exceptions import IncorrectPasswordError, ParserException
    try:
        raw = casparser.read_cas_pdf(io.BytesIO(pdf_bytes), password, output="json")
    except IncorrectPasswordError:
        return {"error": "Incorrect PDF password."}
    except ParserException as exc:
        return {"error": f"Could not parse CAS PDF: {exc}"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"error": f"Unreadable PDF: {exc}"}
    parsed = json.loads(raw)
    if parsed.get("cas_type") != "DETAILED":
        return {"error": "Not a detailed CAS statement — request the detailed "
                         "(transaction-wise) CAS from CAMS/KFintech."}
    return parsed


def _scrub(parsed: dict) -> dict:
    """Deep copy with PII removed (PAN, KYC flags, investor identity)."""
    clean = copy.deepcopy(parsed)
    clean["investor_info"] = {}
    for folio in clean.get("folios", []):
        for key in ("PAN", "KYC", "PANKYC"):
            folio.pop(key, None)
    return clean


def import_cas(engine, parsed: dict, account_id: int) -> dict:
    """Write one parsed CAS into the portfolio tables. All writes in a single
    transaction. Returns a summary dict; {"error": ...} on bad account."""
    from sqlalchemy import insert as _insert, select, text as _text, update as _update
    from db.models import accounts as accounts_t, assets as assets_t

    summary = {"schemes": 0, "assets_created": 0, "assets_matched": 0,
               "transactions": 0, "skipped_rows": 0, "warnings": []}

    with engine.begin() as conn:
        if not conn.execute(select(accounts_t.c.id)
                            .where(accounts_t.c.id == account_id)).first():
            return {"error": "account not found"}

        existing = conn.execute(
            select(assets_t.c.id, assets_t.c.symbol, assets_t.c.meta, assets_t.c.name)
            .where(assets_t.c.type == "mf")
        ).mappings().fetchall()
        by_amfi = {r["symbol"]: r for r in existing if r["symbol"]}
        by_isin = {(r["meta"] or {}).get("isin"): r
                   for r in existing if (r["meta"] or {}).get("isin")}

        for folio in parsed.get("folios", []):
            for scheme in folio.get("schemes", []):
                summary["schemes"] += 1
                amfi = (scheme.get("amfi") or "").strip() or None
                isin = (scheme.get("isin") or "").strip() or None
                if not amfi and not isin:
                    summary["warnings"].append(
                        f"scheme without AMFI code or ISIN skipped: {scheme.get('scheme')}")
                    continue
                close = float(scheme.get("close") or 0)
                txns = scheme.get("transactions", [])

                row = (amfi and by_amfi.get(amfi)) or (isin and by_isin.get(isin))
                if row:
                    asset_id = row["id"]
                    summary["assets_matched"] += 1
                    updates = {}
                    if not row["symbol"] and amfi:
                        updates["symbol"] = amfi
                    meta = dict(row["meta"] or {})
                    if isin and not meta.get("isin"):
                        meta["isin"] = isin
                        updates["meta"] = meta
                    if updates:
                        conn.execute(_update(assets_t)
                                     .where(assets_t.c.id == asset_id).values(**updates))
                else:
                    if close <= 0 and not txns:
                        summary["warnings"].append(
                            f"closed scheme without transactions skipped: {scheme.get('scheme')}")
                        continue
                    asset_id = conn.execute(_insert(assets_t).values(
                        account_id=account_id, type="mf",
                        name=scheme.get("scheme") or f"CAS scheme {amfi or isin}",
                        symbol=amfi,
                        meta={"isin": isin, "folio": folio.get("folio"),
                              "rta": scheme.get("rta")},
                        archived=close <= 0,
                    ).returning(assets_t.c.id)).scalar()
                    summary["assets_created"] += 1

                if close > 0:
                    conn.execute(_text(
                        "INSERT INTO holdings (asset_id, units) VALUES (:aid, :u) "
                        "ON CONFLICT (asset_id) DO UPDATE SET units = EXCLUDED.units"
                    ), {"aid": asset_id, "u": close})

                summary["transactions"] += _write_transactions(
                    conn, asset_id, folio.get("folio"), txns, summary)

    log_event(LOGGER, "cas_imported", account_id=account_id,
              **{k: v for k, v in summary.items() if k != "warnings"},
              warnings=len(summary["warnings"]))
    return summary


def _write_transactions(conn, asset_id: int, folio: str | None,
                        txns: list[dict], summary: dict) -> int:
    """Replace this asset's CAS-sourced rows with the statement's rows."""
    from datetime import date as _date
    from sqlalchemy import delete as _delete, insert as _insert
    from db.models import transactions as transactions_t

    conn.execute(_delete(transactions_t).where(
        transactions_t.c.asset_id == asset_id,
        transactions_t.c.meta["source"].as_string() == "cas",
    ))

    written = 0
    unmapped: set[str] = set()
    for t in txns:
        cas_type = t.get("type") or ""
        if cas_type in _TXN_SKIP:
            summary["skipped_rows"] += 1
            continue
        our_type = _TXN_TYPE_MAP.get(cas_type)
        if our_type is None:
            summary["skipped_rows"] += 1
            unmapped.add(cas_type)
            continue
        amount = t.get("amount")
        if amount is None:
            # transactions.amount is NOT NULL — a CAS row with no parseable
            # amount can't be inserted at all, and there's no real value to
            # invent in its place (never guess). Skip it like every other
            # unwritable row here, rather than letting the insert below
            # raise IntegrityError mid-transaction.
            summary["skipped_rows"] += 1
            continue
        conn.execute(_insert(transactions_t).values(
            asset_id=asset_id,
            date=_date.fromisoformat(str(t["date"])),
            type=our_type,
            amount=abs(float(amount)),
            units=float(t["units"]) if t.get("units") is not None else None,
            meta={"source": "cas", "folio": folio},
        ))
        written += 1
    for cas_type in sorted(unmapped):
        summary["warnings"].append(f"unmapped transaction type skipped: {cas_type}")
    return written


def archive_parsed(parsed: dict, out_dir: str = "output/_cas") -> str:
    """Write the scrubbed parse result for later replay. Returns the path."""
    import os
    from datetime import datetime
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_scrub(parsed), fh, ensure_ascii=False, indent=1)
    return path


if __name__ == "__main__":
    import argparse
    from db.models import get_engine

    cli = argparse.ArgumentParser(description="CAS import replay")
    cli.add_argument("--replay", required=True, metavar="JSON",
                     help="archived parse JSON from output/_cas/")
    cli.add_argument("--account-id", required=True, type=int)
    args = cli.parse_args()
    with open(args.replay, encoding="utf-8") as fh:
        result = import_cas(get_engine(), json.load(fh), args.account_id)
    print(json.dumps(result, indent=1))
