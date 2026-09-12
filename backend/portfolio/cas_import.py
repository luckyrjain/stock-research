"""
CAS PDF Import
==============
Imports a CAMS/KFintech *detailed* CAS PDF into the portfolio tables:
transactions (meta.source='cas'), holdings (units = closing balance), and
missing mf assets. Parsed JSON (scrubbed of PII) is archived under the
`cas_archive` namespace in core/state_store.py for replay:

    python portfolio/cas_import.py --replay <key> --account-id N
"""

import copy
import io
import json

from dotenv import load_dotenv

from core import state_store
from core.observability import get_logger, log_event

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


def upsert_holdings_units(conn, asset_id: int, units) -> None:
    """Upsert one asset's total units into holdings.

    Shared by import_cas() (below) and csv_import.import_rows() — both
    compute an asset's total unit count their own way, then persist it via
    this identical statement. Lives here since CAS import was, per
    backend/CLAUDE.md's own framing, "the first real writer into
    transactions"; csv_import imports it rather than redefining it."""
    from sqlalchemy import text as _text
    conn.execute(_text(
        "INSERT INTO holdings (asset_id, units) VALUES (:aid, :u) "
        "ON CONFLICT (asset_id) DO UPDATE SET units = EXCLUDED.units"
    ), {"aid": asset_id, "u": units})


def import_cas(engine, parsed: dict, account_id: int) -> dict:
    """Write one parsed CAS into the portfolio tables. All writes in a single
    transaction. Returns a summary dict; {"error": ...} on bad account."""
    from sqlalchemy import insert as _insert, select, update as _update
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
        # `_write_transactions()` deletes an asset's existing CAS rows before
        # inserting the statement's own — correct once per asset per import
        # (a full re-import restates that asset's history), but the same
        # scheme can now be matched from more than one folio within this
        # same statement (see the by_amfi/by_isin backfill above) — without
        # this guard, the second folio's write would delete the first
        # folio's just-inserted rows for the same asset before this
        # transaction commits.
        cas_rows_cleared: set[int] = set()
        # Same reasoning applies to holdings.units and the archived flag: a
        # scheme genuinely held via two folios (two SIP folios, a
        # post-merger split) now resolves to one asset, but each folio still
        # reports its OWN closing balance -- the true total is their sum,
        # not either one alone (and not whichever folio the row happened to
        # be created/last-updated from). Accumulated here across EVERY
        # folio touching this asset -- including a zero/negative one, which
        # matters for the archived reconciliation below: a dict that only
        # ever held positive contributions could never represent "this
        # asset's folios now net out to fully redeemed," making that
        # reconciliation able to un-archive but never re-archive.
        close_by_asset: dict[int, float] = {}

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
                    # Record the new asset in this run's own lookup dicts —
                    # not just the DB — so the same scheme appearing under a
                    # second folio later in this same statement (a scheme
                    # held via two SIP folios, or a post-merger folio split)
                    # matches this asset instead of silently creating a
                    # duplicate one. `existing` above was snapshotted once
                    # before this loop started, so without this a later
                    # iteration's lookup would still see it as unmatched.
                    new_row = {"id": asset_id, "symbol": amfi,
                               "meta": {"isin": isin}, "name": scheme.get("scheme")}
                    if amfi:
                        by_amfi[amfi] = new_row
                    if isin:
                        by_isin[isin] = new_row

                close_by_asset[asset_id] = close_by_asset.get(asset_id, 0.0) + close

                summary["transactions"] += _write_transactions(
                    conn, asset_id, folio.get("folio"), txns, summary,
                    clear_existing=asset_id not in cas_rows_cleared)
                cas_rows_cleared.add(asset_id)

        for asset_id, total_close in close_by_asset.items():
            if total_close > 0:
                upsert_holdings_units(conn, asset_id, total_close)
            # A scheme matched across one or more folios (see the
            # by_amfi/by_isin backfill above -- this also covers an asset
            # matched to a SINGLE folio in THIS statement whose own close
            # now differs from its prior archived state, not just the
            # multi-folio backfill case) may have been created
            # `archived=True`/`False` off a stale assumption -- reconcile
            # against the TRUE combined close now that every folio's
            # contribution is in, rather than trusting whichever folio
            # happened to be seen first at asset-creation time.
            conn.execute(_update(assets_t)
                         .where(assets_t.c.id == asset_id)
                         .values(archived=total_close <= 0))

    log_event(LOGGER, "cas_imported", account_id=account_id,
              **{k: v for k, v in summary.items() if k != "warnings"},
              warnings=len(summary["warnings"]))
    return summary


def _write_transactions(conn, asset_id: int, folio: str | None,
                        txns: list[dict], summary: dict,
                        clear_existing: bool = True) -> int:
    """Replace this asset's CAS-sourced rows with the statement's rows.

    `clear_existing=False` skips the delete — used when this asset was
    already cleared earlier in the same `import_cas()` call (a scheme
    spanning two folios in one statement), so a later folio's write doesn't
    wipe out an earlier folio's just-inserted rows for the same asset."""
    from datetime import date as _date
    from sqlalchemy import delete as _delete, insert as _insert
    from db.models import transactions as transactions_t

    if clear_existing:
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


ARCHIVE_NAMESPACE = "cas_archive"
# One full scrubbed parse per import — a debug/replay convenience, not
# durable product history (unlike e.g. verdict_history), so it's pruned the
# same way telemetry/source_quality.py's own per-run namespace is (see
# CLAUDE.md's "A namespace with unbounded per-run growth ... should call
# delete_older_than() after each write"). 90 days comfortably covers
# realistic "re-run this import for debugging" use without keeping every
# statement's full transaction history around indefinitely.
_ARCHIVE_RETENTION_DAYS = 90


def archive_parsed(parsed: dict) -> str:
    """Store the scrubbed parse result for later replay. Returns its key.

    The PDF bytes themselves are never persisted, and `_scrub()` has already
    stripped PAN/KYC/investor identity — this is the parse output only, kept
    so an import can be re-run for debugging without re-uploading the
    statement."""
    from datetime import datetime, timezone
    key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    state_store.save(ARCHIVE_NAMESPACE, key, _scrub(parsed))
    state_store.delete_older_than(ARCHIVE_NAMESPACE, days=_ARCHIVE_RETENTION_DAYS)
    return key


if __name__ == "__main__":
    import argparse
    from db.models import get_engine

    cli = argparse.ArgumentParser(description="CAS import replay")
    cli.add_argument("--replay", required=True, metavar="KEY",
                     help="archived parse key (as returned by archive_parsed)")
    cli.add_argument("--account-id", required=True, type=int)
    args = cli.parse_args()
    archived = state_store.load(ARCHIVE_NAMESPACE, args.replay)
    if archived is None:
        raise SystemExit(f"No archived CAS parse found for key {args.replay!r}")
    print(json.dumps(import_cas(get_engine(), archived, args.account_id), indent=1))
