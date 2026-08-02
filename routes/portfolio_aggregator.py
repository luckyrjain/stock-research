"""Personal Portfolio Aggregator — profiles/accounts/assets/valuations/net
worth. See CLAUDE.md's "Portfolio aggregator" section for the full design
and, importantly, why this is a *different* feature from the existing
`/portfolio` page (which aggregates "I bought this" market-picks positions).

No auth, no client_id/user_id ownership — deliberate scope call inherited
from the original design: a personal, localhost/Tailscale-only tool for a
handful of users, not a multi-tenant product. `profiles` is a bare picker
(no credentials), which keeps the door open for real auth later without
forcing it into this increment.
"""
import json
from datetime import date as _date

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from routes._shared import run_owned_db_call

router = APIRouter(prefix="/api/portfolio")

_ACCOUNT_TYPES = {"bank", "broker", "amc", "epfo", "other"}
_ASSET_TYPES = {"mf", "stock", "fd", "epf", "ppf", "cash", "manual", "loan"}


def compute_networth(rows: list[dict]) -> dict:
    """Aggregates latest-valuation rows into a net worth summary. Pure
    function (rows in, summary out) so it's unit-testable without a live
    DB — loans are stored positive in `valuations` and signed negative
    here, the only place the sign flip happens."""
    total = 0.0
    by_type: dict[str, float] = {}
    by_account: dict[int, dict] = {}
    for r in rows:
        signed = -float(r["value"]) if r["type"] == "loan" else float(r["value"])
        total += signed
        by_type[r["type"]] = by_type.get(r["type"], 0.0) + signed
        acc = by_account.setdefault(
            r["account_id"],
            {"account_id": r["account_id"], "account_name": r["account_name"], "value": 0.0},
        )
        acc["value"] += signed
    for k in by_type:
        by_type[k] = round(by_type[k], 2)
    for acc in by_account.values():
        acc["value"] = round(acc["value"], 2)
    return {"total": round(total, 2), "by_type": by_type, "by_account": list(by_account.values())}


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class AccountIn(BaseModel):
    profile_id: int
    name: str = Field(min_length=1, max_length=120)
    institution: str | None = None
    type: str


class AccountPatch(BaseModel):
    name: str | None = None
    institution: str | None = None
    type: str | None = None


class AssetIn(BaseModel):
    account_id: int
    type: str
    name: str = Field(min_length=1, max_length=200)
    symbol: str | None = None
    meta: dict = Field(default_factory=dict)
    value: float = Field(ge=0)          # initial valuation, as of today
    units: float | None = None          # mf/stock only
    avg_cost: float | None = None


class AssetPatch(BaseModel):
    name: str | None = None
    symbol: str | None = None
    meta: dict | None = None
    archived: bool | None = None
    units: float | None = None
    avg_cost: float | None = None


class ValuationIn(BaseModel):
    value: float = Field(ge=0)
    as_of: _date | None = None


@router.get("/profiles")
async def list_profiles(request: Request):
    def _sync() -> dict:
        import api
        from sqlalchemy import select
        from db.models import profiles

        with api._get_db_engine().connect() as conn:
            rows = conn.execute(select(profiles).order_by(profiles.c.id)).mappings().fetchall()
        return {"profiles": [{"id": r["id"], "name": r["name"]} for r in rows]}

    return await run_owned_db_call(request, "portfolio_agg_read", 120, _sync, "portfolio_agg_read")


@router.post("/profiles", status_code=201)
async def create_profile(request: Request, body: ProfileIn):
    def _sync() -> dict:
        import api
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError
        from db.models import profiles

        try:
            with api._get_db_engine().begin() as conn:
                new_id = conn.execute(
                    insert(profiles).values(name=body.name).returning(profiles.c.id)
                ).scalar()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="profile name already exists")
        return {"id": new_id, "name": body.name}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.get("/accounts")
async def list_accounts(request: Request, profile_id: int):
    def _sync() -> dict:
        import api
        from sqlalchemy import select
        from db.models import accounts

        with api._get_db_engine().connect() as conn:
            rows = conn.execute(
                select(accounts).where(accounts.c.profile_id == profile_id).order_by(accounts.c.id)
            ).mappings().fetchall()
        return {"accounts": [dict(r) for r in rows]}

    return await run_owned_db_call(request, "portfolio_agg_read", 120, _sync, "portfolio_agg_read")


@router.post("/accounts", status_code=201)
async def create_account(request: Request, body: AccountIn):
    if body.type not in _ACCOUNT_TYPES:
        raise HTTPException(status_code=422, detail=f"type must be one of: {sorted(_ACCOUNT_TYPES)}")

    def _sync() -> dict:
        import api
        from sqlalchemy import insert, select
        from db.models import accounts, profiles

        with api._get_db_engine().begin() as conn:
            if not conn.execute(select(profiles.c.id).where(profiles.c.id == body.profile_id)).first():
                raise HTTPException(status_code=404, detail="profile not found")
            new_id = conn.execute(
                insert(accounts).values(
                    profile_id=body.profile_id, name=body.name,
                    institution=body.institution, type=body.type,
                ).returning(accounts.c.id)
            ).scalar()
        return {"id": new_id}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.patch("/accounts/{account_id}")
async def patch_account(request: Request, account_id: int, body: AccountPatch):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    if "type" in updates and updates["type"] not in _ACCOUNT_TYPES:
        raise HTTPException(status_code=422, detail=f"type must be one of: {sorted(_ACCOUNT_TYPES)}")

    def _sync() -> dict:
        import api
        from sqlalchemy import update
        from db.models import accounts

        with api._get_db_engine().begin() as conn:
            res = conn.execute(update(accounts).where(accounts.c.id == account_id).values(**updates))
            if res.rowcount == 0:
                raise HTTPException(status_code=404, detail="account not found")
        return {"ok": True}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.delete("/accounts/{account_id}")
async def delete_account(request: Request, account_id: int):
    def _sync() -> dict:
        import api
        from sqlalchemy import delete, func, select
        from db.models import accounts, assets

        with api._get_db_engine().begin() as conn:
            n_assets = conn.execute(
                select(func.count()).where(assets.c.account_id == account_id)
            ).scalar() or 0
            if n_assets:
                raise HTTPException(status_code=422, detail="account still has assets; delete them first")
            res = conn.execute(delete(accounts).where(accounts.c.id == account_id))
            if res.rowcount == 0:
                raise HTTPException(status_code=404, detail="account not found")
        return {"ok": True}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.get("/assets")
async def list_assets(request: Request, account_id: int):
    def _sync() -> dict:
        import api
        from sqlalchemy import text as _text

        with api._get_db_engine().connect() as conn:
            # Correlated scalar subqueries, not a LATERAL join — LATERAL
            # isn't supported by SQLite, which this codebase's tests run
            # these tables against (house rule: no live DB in tests). At
            # this table's real scale (a personal, single-household tool,
            # per the design's own "tens of users" scope) a second
            # correlated lookup per asset is not a real cost.
            rows = conn.execute(_text("""
                SELECT a.id, a.account_id, a.type, a.name, a.symbol, a.meta, a.archived,
                       CAST(h.units AS FLOAT) AS units, CAST(h.avg_cost AS FLOAT) AS avg_cost,
                       CAST((SELECT value FROM valuations
                             WHERE asset_id = a.id ORDER BY as_of DESC LIMIT 1) AS FLOAT) AS value,
                       CAST((SELECT as_of FROM valuations
                             WHERE asset_id = a.id ORDER BY as_of DESC LIMIT 1) AS TEXT) AS valued_on
                FROM assets a
                LEFT JOIN holdings h ON h.asset_id = a.id
                WHERE a.account_id = :acc
                ORDER BY a.id
            """), {"acc": account_id}).mappings().fetchall()
        return {"assets": [dict(r) for r in rows]}

    return await run_owned_db_call(request, "portfolio_agg_read", 120, _sync, "portfolio_agg_read")


@router.post("/assets", status_code=201)
async def create_asset(request: Request, body: AssetIn):
    if body.type not in _ASSET_TYPES:
        raise HTTPException(status_code=422, detail=f"type must be one of: {sorted(_ASSET_TYPES)}")
    if body.type not in ("mf", "stock") and (body.units is not None or body.avg_cost is not None):
        raise HTTPException(status_code=422, detail="units/avg_cost only apply to mf and stock assets")
    if body.avg_cost is not None and body.units is None:
        raise HTTPException(status_code=422, detail="cannot set avg_cost without units")

    def _sync() -> dict:
        import api
        from sqlalchemy import insert, select
        from db.models import accounts, assets, holdings, valuations

        with api._get_db_engine().begin() as conn:
            if not conn.execute(select(accounts.c.id).where(accounts.c.id == body.account_id)).first():
                raise HTTPException(status_code=404, detail="account not found")
            asset_id = conn.execute(
                insert(assets).values(
                    account_id=body.account_id, type=body.type, name=body.name,
                    symbol=body.symbol, meta=body.meta,
                ).returning(assets.c.id)
            ).scalar()
            conn.execute(insert(valuations).values(
                asset_id=asset_id, as_of=_date.today(), value=body.value,
            ))
            if body.type in ("mf", "stock") and body.units is not None:
                conn.execute(insert(holdings).values(
                    asset_id=asset_id, units=body.units, avg_cost=body.avg_cost,
                ))
        return {"id": asset_id}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.patch("/assets/{asset_id}")
async def patch_asset(request: Request, asset_id: int, body: AssetPatch):
    fields = body.model_dump()
    asset_updates = {k: v for k, v in fields.items()
                     if k in ("name", "symbol", "meta", "archived") and v is not None}
    holding_updates = {k: v for k, v in fields.items()
                       if k in ("units", "avg_cost") and v is not None}
    if not asset_updates and not holding_updates:
        raise HTTPException(status_code=422, detail="no fields to update")

    def _sync() -> dict:
        import api
        from sqlalchemy import insert, select, update
        from db.models import assets, holdings

        with api._get_db_engine().begin() as conn:
            asset_row = conn.execute(
                select(assets.c.id, assets.c.type).where(assets.c.id == asset_id)
            ).mappings().first()
            if not asset_row:
                raise HTTPException(status_code=404, detail="asset not found")
            if holding_updates and asset_row["type"] not in ("mf", "stock"):
                raise HTTPException(status_code=422, detail="units/avg_cost only apply to mf and stock assets")
            if asset_updates:
                conn.execute(update(assets).where(assets.c.id == asset_id).values(**asset_updates))
            if holding_updates:
                existing = conn.execute(
                    select(holdings.c.id).where(holdings.c.asset_id == asset_id)
                ).first()
                if existing:
                    conn.execute(update(holdings).where(holdings.c.asset_id == asset_id).values(**holding_updates))
                elif "units" in holding_updates:
                    conn.execute(insert(holdings).values(asset_id=asset_id, **holding_updates))
                else:
                    raise HTTPException(status_code=422, detail="cannot set avg_cost without units")
        return {"ok": True}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.delete("/assets/{asset_id}")
async def delete_asset(request: Request, asset_id: int):
    def _sync() -> dict:
        import api
        from sqlalchemy import delete
        from db.models import assets, holdings, transactions, valuations

        with api._get_db_engine().begin() as conn:
            conn.execute(delete(valuations).where(valuations.c.asset_id == asset_id))
            conn.execute(delete(holdings).where(holdings.c.asset_id == asset_id))
            conn.execute(delete(transactions).where(transactions.c.asset_id == asset_id))
            res = conn.execute(delete(assets).where(assets.c.id == asset_id))
            if res.rowcount == 0:
                raise HTTPException(status_code=404, detail="asset not found")
        return {"ok": True}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.post("/assets/{asset_id}/valuations", status_code=201)
async def upsert_valuation(request: Request, asset_id: int, body: ValuationIn):
    if body.as_of and body.as_of > _date.today():
        raise HTTPException(status_code=422, detail="as_of cannot be in the future")

    def _sync() -> dict:
        import api
        from sqlalchemy import select, text as _text
        from db.models import assets

        with api._get_db_engine().begin() as conn:
            if not conn.execute(select(assets.c.id).where(assets.c.id == asset_id)).first():
                raise HTTPException(status_code=404, detail="asset not found")
            conn.execute(_text("""
                INSERT INTO valuations (asset_id, as_of, value)
                VALUES (:aid, :as_of, :value)
                ON CONFLICT (asset_id, as_of)
                DO UPDATE SET value = EXCLUDED.value
            """), {"aid": asset_id, "as_of": body.as_of or _date.today(), "value": body.value})
        return {"ok": True}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.get("/networth")
async def get_networth(request: Request, profile_id: int):
    def _sync() -> dict:
        import api
        from sqlalchemy import text as _text

        with api._get_db_engine().connect() as conn:
            # Correlated scalar subquery, not a LATERAL join — see the same
            # note in list_assets() above. The EXISTS filters out an asset
            # with no valuation row at all, matching the original inner-join
            # semantics (an asset never valued contributes nothing, rather
            # than a NULL/0 entry).
            rows = conn.execute(_text("""
                SELECT a.id AS asset_id, a.name AS asset_name, a.type,
                       ac.id AS account_id, ac.name AS account_name,
                       CAST((SELECT value FROM valuations
                             WHERE asset_id = a.id ORDER BY as_of DESC LIMIT 1) AS FLOAT) AS value
                FROM assets a
                JOIN accounts ac ON ac.id = a.account_id
                WHERE ac.profile_id = :pid AND NOT a.archived
                  AND EXISTS (SELECT 1 FROM valuations WHERE asset_id = a.id)
            """), {"pid": profile_id}).mappings().fetchall()
        return compute_networth([dict(r) for r in rows])

    return await run_owned_db_call(request, "portfolio_agg_read", 120, _sync, "portfolio_agg_read")


@router.post("/refresh-valuations")
async def refresh_valuations_endpoint(request: Request):
    def _sync() -> dict:
        import api
        from portfolio_valuation import refresh_valuations

        return refresh_valuations(api._get_db_engine())

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.get("/xirr")
async def get_xirr(request: Request, profile_id: int):
    def _sync() -> dict:
        import api
        from portfolio_valuation import xirr_report

        return xirr_report(api._get_db_engine(), profile_id)

    return await run_owned_db_call(request, "portfolio_agg_read", 120, _sync, "portfolio_agg_read")


@router.post("/import-cas")
async def import_cas_endpoint(
    request: Request,
    file: UploadFile = File(...),
    password: str = Form(...),
    account_id: int = Form(...),
):
    pdf_bytes = await file.read()

    def _sync() -> dict:
        import api
        from cas_import import archive_parsed, import_cas, parse_cas
        from portfolio_valuation import refresh_valuations

        parsed = parse_cas(pdf_bytes, password)
        if "error" in parsed:
            raise HTTPException(status_code=422, detail=parsed["error"])
        result = import_cas(api._get_db_engine(), parsed, account_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        archive_parsed(parsed)
        refresh_valuations(api._get_db_engine())
        return result

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.post("/import-csv/preview")
async def import_csv_preview(request: Request, file: UploadFile = File(...)):
    file_bytes = await file.read()
    filename = file.filename or ""

    def _sync() -> dict:
        from csv_import import parse_broker_file, suggest_mapping

        parsed = parse_broker_file(file_bytes, filename)
        if "error" in parsed:
            raise HTTPException(status_code=422, detail=parsed["error"])
        suggestion = suggest_mapping(parsed["headers"])
        return {
            "headers": parsed["headers"],
            "sample_rows": parsed["rows"][:5],
            "suggested_mapping": suggestion["mapping"],
            "detected": suggestion["detected"],
        }

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.post("/import-csv")
async def import_csv_endpoint(
    request: Request,
    file: UploadFile = File(...),
    mapping: str = Form(...),
    account_id: int = Form(...),
    broker: str = Form(...),
):
    file_bytes = await file.read()
    filename = file.filename or ""
    try:
        mapping_dict = json.loads(mapping)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=422, detail="mapping must be a JSON object")
    missing = [f for f in ("date", "symbol", "side", "quantity", "price")
               if not mapping_dict.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"mapping missing required field(s): {missing}")

    def _sync() -> dict:
        import api
        from csv_import import import_rows, parse_broker_file
        from portfolio_valuation import refresh_valuations

        parsed = parse_broker_file(file_bytes, filename)
        if "error" in parsed:
            raise HTTPException(status_code=422, detail=parsed["error"])
        result = import_rows(api._get_db_engine(), parsed["rows"], parsed["headers"],
                             mapping_dict, account_id, broker)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        refresh_valuations(api._get_db_engine())
        return result

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")
