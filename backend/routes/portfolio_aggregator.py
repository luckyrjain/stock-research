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
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from routes._shared import run_owned_db_call

router = APIRouter(prefix="/api/portfolio")

_ACCOUNT_TYPES = {"bank", "broker", "amc", "epfo", "other"}
_ASSET_TYPES = {"mf", "stock", "fd", "epf", "ppf", "cash", "manual", "loan"}

# Broker-API integrations (docs/PRD-gmail-portfolio-intelligence.md's Phase 1) —
# a closed allowlist, not user-supplied text, since each broker needs its own
# sync module wired in below. See broker_connections' own comment in
# db/models.py for why the table itself is already broker-agnostic.
_SUPPORTED_BROKERS = {"zerodha", "hdfc_securities", "paytm_money"}

# Generous upper bound on one account's holdings+trades sync — same
# "TTL survives a crashed worker" reasoning as api.py's SME/screener/
# market-picks refresh locks, just scoped much smaller (one connection,
# not a whole market/pipeline run).
_BROKER_SYNC_LOCK_TTL_SECONDS = 300

# Caps how often a single (account, broker) pair may even *attempt* a sync,
# independent of the concurrency lock above — the lock only stops two syncs
# from overlapping, not a user (or a stuck frontend poll loop) repeatedly
# kicking off back-to-back syncs against the broker's own API. A generous
# ceiling for this tool's real scale (a handful of personal accounts, not a
# multi-tenant product) — well above any plausible legitimate use, low
# enough to matter as an actual backstop.
_BROKER_SYNC_RATE_LIMIT_MAX_CALLS = 12
_BROKER_SYNC_RATE_LIMIT_WINDOW_SECONDS = 3600.0

# A sync now runs on this dedicated background executor rather than inside
# the HTTP request/response cycle (see broker_sync() below) — a full
# holdings+trades round trip against a live broker API can take several
# seconds, long enough that holding a request thread open for it is poor
# UX and, at any real scale, a waste of a request-handling thread.
# ThreadPoolExecutor, not a job queue — see CLAUDE.md's Architectural
# Constraints; this repo's stated scale (tens of users) never needs
# anything heavier, and a handful of background sync jobs at a time is
# comfortably within a small fixed pool. Deliberately separate from
# api.py's own default executor (which every other route already runs on
# via run_owned_db_call) so a burst of broker syncs can never starve
# ordinary request handling of worker threads. Not wired into api.py's
# lifespan shutdown: an in-flight sync being killed on process exit is no
# different from today's crash-mid-sync case, which the lock's TTL above
# already exists to recover from.
_BROKER_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="broker-sync-bg")


def _broker_sync_module(broker: str):
    """Returns the portfolio.<broker>_sync module for a supported broker.
    A plain if/elif over three known names rather than importlib string
    assembly — the broker string reaching here already passed
    _require_supported_broker()'s allowlist check, but an explicit branch
    keeps `broker` from ever being interpolated into an import path."""
    if broker == "zerodha":
        import portfolio.kite_sync as mod
    elif broker == "hdfc_securities":
        import portfolio.hdfc_sync as mod
    elif broker == "paytm_money":
        import portfolio.paytm_sync as mod
    else:  # pragma: no cover — unreachable once _require_supported_broker() ran first
        raise HTTPException(status_code=422, detail=f"broker must be one of: {sorted(_SUPPORTED_BROKERS)}")
    return mod


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


class BrokerLoginUrlIn(BaseModel):
    account_id: int
    # This account's own app credentials, registered by whoever owns this
    # broker login (e.g. developers.kite.trade) — never a deployment-wide
    # env var, since a Kite Connect/HDFC/Paytm Money "app" is created under
    # one specific broker account, not issued once per AlphaPulse instance.
    # Both optional: omit both to reuse whatever was registered on a prior
    # call (resuming/retrying an OAuth handshake shouldn't force re-typing
    # a secret every time) — first-time setup for this (account, broker)
    # must supply both, and either alone is a 422.
    api_key: str | None = Field(default=None, min_length=1, max_length=255)
    api_secret: str | None = Field(default=None, min_length=1, max_length=255)


class BrokerConnectIn(BaseModel):
    account_id: int
    request_token: str


class BrokerSyncIn(BaseModel):
    account_id: int


class HdfcLoginStartIn(BaseModel):
    account_id: int
    # Same both-or-neither shape as BrokerLoginUrlIn — first-time setup
    # supplies the app credentials, a retry after a failed/expired login
    # attempt reuses what's already registered.
    api_key: str | None = Field(default=None, min_length=1, max_length=255)
    api_secret: str | None = Field(default=None, min_length=1, max_length=255)
    # HDFC's own broker login — never persisted (see hdfc_sync.py's module
    # docstring: passed straight through to HDFC's /login/validate and
    # discarded once this request returns).
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class HdfcVerifyOtpIn(BaseModel):
    account_id: int
    otp: str = Field(min_length=1, max_length=20)


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
        from portfolio.portfolio_valuation import refresh_valuations

        return refresh_valuations(api._get_db_engine())

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.get("/xirr")
async def get_xirr(request: Request, profile_id: int):
    def _sync() -> dict:
        import api
        from portfolio.portfolio_valuation import xirr_report

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
        from portfolio.cas_import import archive_parsed, import_cas, parse_cas
        from portfolio.portfolio_valuation import refresh_valuations

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
        from portfolio.csv_import import parse_broker_file, suggest_mapping

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
    from portfolio.csv_import import REQUIRED_FIELDS

    try:
        mapping_dict = json.loads(mapping)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=422, detail="mapping must be a JSON object")
    missing = [f for f in REQUIRED_FIELDS if not mapping_dict.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"mapping missing required field(s): {missing}")

    def _sync() -> dict:
        import api
        from portfolio.csv_import import import_rows, parse_broker_file
        from portfolio.portfolio_valuation import refresh_valuations

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


def _require_supported_broker(broker: str) -> None:
    if broker not in _SUPPORTED_BROKERS:
        raise HTTPException(status_code=422, detail=f"broker must be one of: {sorted(_SUPPORTED_BROKERS)}")


@router.post("/broker/{broker}/login-url")
async def broker_login_url(request: Request, broker: str, body: BrokerLoginUrlIn):
    """Returns the login URL to start (or resume) connecting `broker` for
    this account. A POST, not a GET, since a first-time call's api_secret
    must never ride along in a query string (server access logs, browser
    history).

    Two modes, both handled here since they share almost everything:
    - **First-time setup** — body carries both api_key and api_secret (see
      BrokerLoginUrlIn — never a shared env var, each account brings its
      own app credentials). Registers/replaces them on the broker_connections
      row and clears any previously-obtained access token, since it was
      minted for whatever credentials were on file before and can't
      possibly still be valid for a changed api_key.
    - **Resume/retry** — body omits both. Reuses whatever was already
      registered (a request_token expiring, or the user closing the tab
      mid-handshake, shouldn't force re-typing a secret every retry) —
      404 if nothing was ever registered for this (account, broker).

    **Not for hdfc_securities** — HDFC's real login has no browser redirect
    at all, so it doesn't fit this endpoint's contract; see
    POST /broker/hdfc_securities/login-start instead."""
    _require_supported_broker(broker)
    if broker == "hdfc_securities":
        raise HTTPException(
            status_code=422,
            detail="hdfc_securities has no redirect login — use POST /broker/hdfc_securities/login-start instead",
        )
    if bool(body.api_key) != bool(body.api_secret):
        raise HTTPException(status_code=422, detail="provide both api_key and api_secret, or neither to reuse saved credentials")

    def _sync() -> dict:
        import api
        from core.crypto import EncryptionNotConfigured, encrypt
        from db.models import accounts, broker_connections
        from sqlalchemy import insert, select, update

        mod = _broker_sync_module(broker)

        with api._get_db_engine().begin() as conn:
            account = conn.execute(
                select(accounts.c.id, accounts.c.profile_id, accounts.c.type)
                .where(accounts.c.id == body.account_id)
            ).first()
            if account is None:
                raise HTTPException(status_code=404, detail="account not found")
            if account.type != "broker":
                raise HTTPException(status_code=422, detail="account.type must be 'broker' to connect a broker API")

            existing = conn.execute(
                select(broker_connections.c.id, broker_connections.c.api_key).where(
                    broker_connections.c.account_id == body.account_id,
                    broker_connections.c.broker == broker,
                )
            ).first()

            if not body.api_key:
                if existing is None or not existing.api_key:
                    raise HTTPException(
                        status_code=404,
                        detail=f"no {broker} app credentials registered for this account yet — provide api_key/api_secret",
                    )
                return {"login_url": mod.get_login_url(existing.api_key)}

            try:
                api_secret_enc = encrypt(body.api_secret)
            except EncryptionNotConfigured as exc:
                raise HTTPException(status_code=503, detail=str(exc))

            if existing:
                conn.execute(
                    update(broker_connections)
                    .where(broker_connections.c.id == existing.id)
                    .values(
                        api_key=body.api_key, api_secret_enc=api_secret_enc,
                        access_token_enc=None, token_obtained_at=None,
                        # A prior sync's outcome was fetched under credentials
                        # that no longer exist — leaving "success" and its
                        # summary on display would misleadingly imply the new
                        # credentials have already synced something.
                        sync_status="idle", last_sync_summary=None, last_sync_error=None,
                    )
                )
            else:
                conn.execute(
                    insert(broker_connections).values(
                        profile_id=account.profile_id,
                        account_id=body.account_id,
                        broker=broker,
                        api_key=body.api_key,
                        api_secret_enc=api_secret_enc,
                    )
                )
            return {"login_url": mod.get_login_url(body.api_key)}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.post("/broker/{broker}/connect")
async def broker_connect(request: Request, broker: str, body: BrokerConnectIn):
    """**Not for hdfc_securities** — see POST /broker/hdfc_securities/verify-otp instead."""
    _require_supported_broker(broker)
    if broker == "hdfc_securities":
        raise HTTPException(
            status_code=422,
            detail="hdfc_securities has no request_token exchange — use POST /broker/hdfc_securities/verify-otp instead",
        )

    def _sync() -> dict:
        import datetime as _dt

        import api
        from core.crypto import EncryptionNotConfigured, decrypt, encrypt
        from db.models import broker_connections
        from sqlalchemy import select, update

        mod = _broker_sync_module(broker)

        with api._get_db_engine().begin() as conn:
            conn_row = conn.execute(
                select(broker_connections.c.id, broker_connections.c.api_key, broker_connections.c.api_secret_enc)
                .where(
                    broker_connections.c.account_id == body.account_id,
                    broker_connections.c.broker == broker,
                )
            ).first()
            if conn_row is None or not conn_row.api_key or not conn_row.api_secret_enc:
                raise HTTPException(
                    status_code=404,
                    detail=f"no {broker} app credentials registered for this account — call login-url first",
                )

            try:
                api_secret = decrypt(conn_row.api_secret_enc)
            except EncryptionNotConfigured as exc:
                raise HTTPException(status_code=503, detail=str(exc))
            except Exception:
                raise HTTPException(
                    status_code=422,
                    detail="stored app secret could not be decrypted — re-register this broker's API key/secret",
                )

            session = mod.exchange_request_token(conn_row.api_key, api_secret, body.request_token)
            if "error" in session or "access_token" not in session:
                raise HTTPException(status_code=422, detail=session.get("error", "broker login failed"))

            try:
                token_enc = encrypt(session["access_token"])
            except EncryptionNotConfigured as exc:
                raise HTTPException(status_code=503, detail=str(exc))

            now = _dt.datetime.now(_dt.timezone.utc)
            conn.execute(
                update(broker_connections)
                .where(broker_connections.c.id == conn_row.id)
                .values(access_token_enc=token_enc, token_obtained_at=now)
            )
        return {"connected": True, "account_id": body.account_id, "broker": broker}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.post("/broker/hdfc_securities/login-start")
async def hdfc_login_start(request: Request, body: HdfcLoginStartIn):
    """Steps 1-2 of HDFC's real login (see portfolio/hdfc_sync.py's module
    docstring for the full 5-step flow) — registers app credentials (same
    both-or-neither semantics as the generic login-url endpoint) if
    supplied, then calls HDFC's own /login (get a token_id) and
    /login/validate (username/password) in sequence. `username`/`password`
    are never written anywhere — passed straight through to HDFC's own
    endpoint and discarded once this request returns.

    `200 {"otp_required": true}` on success (the frontend's next step is
    always an OTP prompt) · `404` no credentials registered and none
    supplied · `422` exactly one of api_key/api_secret supplied, or HDFC
    rejected the login/credentials step · `503` PORTFOLIO_ENCRYPTION_KEY
    unset (first-time/replace mode only)."""
    if bool(body.api_key) != bool(body.api_secret):
        raise HTTPException(status_code=422, detail="provide both api_key and api_secret, or neither to reuse saved credentials")

    def _sync() -> dict:
        import api
        from core.crypto import EncryptionNotConfigured, encrypt
        from db.models import accounts, broker_connections
        from portfolio import hdfc_sync
        from sqlalchemy import insert, select, update

        with api._get_db_engine().begin() as conn:
            account = conn.execute(
                select(accounts.c.id, accounts.c.profile_id, accounts.c.type)
                .where(accounts.c.id == body.account_id)
            ).first()
            if account is None:
                raise HTTPException(status_code=404, detail="account not found")
            if account.type != "broker":
                raise HTTPException(status_code=422, detail="account.type must be 'broker' to connect a broker API")

            existing = conn.execute(
                select(broker_connections.c.id, broker_connections.c.api_key).where(
                    broker_connections.c.account_id == body.account_id,
                    broker_connections.c.broker == "hdfc_securities",
                )
            ).first()

            if body.api_key:
                try:
                    api_secret_enc = encrypt(body.api_secret)
                except EncryptionNotConfigured as exc:
                    raise HTTPException(status_code=503, detail=str(exc))
                api_key = body.api_key
                if existing:
                    conn.execute(
                        update(broker_connections)
                        .where(broker_connections.c.id == existing.id)
                        .values(
                            api_key=api_key, api_secret_enc=api_secret_enc,
                            access_token_enc=None, token_obtained_at=None, pending_token_id=None,
                            sync_status="idle", last_sync_summary=None, last_sync_error=None,
                        )
                    )
                else:
                    conn.execute(
                        insert(broker_connections).values(
                            profile_id=account.profile_id, account_id=body.account_id,
                            broker="hdfc_securities", api_key=api_key, api_secret_enc=api_secret_enc,
                        )
                    )
            else:
                if existing is None or not existing.api_key:
                    raise HTTPException(
                        status_code=404,
                        detail="no hdfc_securities app credentials registered for this account yet — provide api_key/api_secret",
                    )
                api_key = existing.api_key

            login = hdfc_sync.start_login(api_key)
            if "error" in login:
                raise HTTPException(status_code=422, detail=login["error"])
            token_id = login["token_id"]

            creds = hdfc_sync.submit_credentials(api_key, token_id, body.username, body.password)
            if "error" in creds:
                raise HTTPException(status_code=422, detail=creds["error"])

            conn.execute(
                update(broker_connections)
                .where(
                    broker_connections.c.account_id == body.account_id,
                    broker_connections.c.broker == "hdfc_securities",
                )
                .values(pending_token_id=token_id)
            )
        return {"otp_required": True}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.post("/broker/hdfc_securities/verify-otp")
async def hdfc_verify_otp(request: Request, body: HdfcVerifyOtpIn):
    """Steps 3-5 of HDFC's real login: OTP verification (returns
    request_token), consent, then the actual access-token exchange —
    chained together here since none of the last two need further user
    input once the OTP is in. Clears `pending_token_id` in every case
    (success or failure) — it's single-use, scoped to one login attempt.

    `200 {"connected": true, "account_id", "broker"}` · `404` no
    login-start call in progress for this account (call it first) · `422`
    HDFC rejected the OTP, consent, or token exchange, or the stored app
    secret couldn't be decrypted · `503` PORTFOLIO_ENCRYPTION_KEY unset."""
    def _sync() -> dict:
        import datetime as _dt

        import api
        from core.crypto import EncryptionNotConfigured, decrypt, encrypt
        from db.models import broker_connections
        from portfolio import hdfc_sync
        from sqlalchemy import select, update

        engine = api._get_db_engine()

        with engine.connect() as conn:
            conn_row = conn.execute(
                select(
                    broker_connections.c.id, broker_connections.c.api_key,
                    broker_connections.c.api_secret_enc, broker_connections.c.pending_token_id,
                ).where(
                    broker_connections.c.account_id == body.account_id,
                    broker_connections.c.broker == "hdfc_securities",
                )
            ).first()
        if conn_row is None or not conn_row.pending_token_id:
            raise HTTPException(
                status_code=404,
                detail="no hdfc_securities login in progress for this account — call login-start first",
            )

        # `pending_token_id` is cleared in its own committed transaction,
        # separate from the multi-step HDFC flow below — that flow raises
        # HTTPException on any failure, and raising inside `engine.begin()`
        # rolls back everything written in that same transaction, which
        # would silently undo the clear on every failure path (caught by
        # test_verify_otp_wrong_otp_clears_pending_token_not_credentials).
        # Single-use, scoped to one login attempt either way — clearing it
        # immediately, before HDFC has even answered the OTP, is correct
        # regardless of what happens next.
        with engine.begin() as conn:
            conn.execute(
                update(broker_connections)
                .where(broker_connections.c.id == conn_row.id)
                .values(pending_token_id=None)
            )

        otp_result = hdfc_sync.submit_otp(conn_row.api_key, conn_row.pending_token_id, body.otp)
        if "error" in otp_result:
            raise HTTPException(status_code=422, detail=otp_result["error"])
        request_token = otp_result["request_token"]

        authorise_result = hdfc_sync.authorise(conn_row.api_key, conn_row.pending_token_id, request_token)
        if "error" in authorise_result:
            raise HTTPException(status_code=422, detail=authorise_result["error"])

        try:
            api_secret = decrypt(conn_row.api_secret_enc)
        except EncryptionNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="stored app secret could not be decrypted — re-register this broker's API key/secret",
            )

        token_result = hdfc_sync.get_access_token(conn_row.api_key, api_secret, request_token)
        if "error" in token_result:
            raise HTTPException(status_code=422, detail=token_result["error"])

        try:
            token_enc = encrypt(token_result["access_token"])
        except EncryptionNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        now = _dt.datetime.now(_dt.timezone.utc)
        with engine.begin() as conn:
            conn.execute(
                update(broker_connections)
                .where(broker_connections.c.id == conn_row.id)
                .values(access_token_enc=token_enc, token_obtained_at=now, pending_token_id=None)
            )
        return {"connected": True, "account_id": body.account_id, "broker": "hdfc_securities"}

    return await run_owned_db_call(request, "portfolio_agg_write", 60, _sync, "portfolio_agg_write")


@router.post("/broker/{broker}/sync", status_code=202)
async def broker_sync(request: Request, broker: str, body: BrokerSyncIn):
    """Kicks off a background sync and returns immediately — see
    _BROKER_SYNC_EXECUTOR's own comment for why this doesn't run inline in
    the request/response cycle. `GET /broker/connections` is how the
    frontend learns the outcome: `sync_status` moves syncing -> success
    (with `last_sync_summary` populated) or syncing -> error (with
    `last_sync_error` populated), for the frontend to poll.

    Two independent guards run before anything is scheduled:
    - A sliding-window rate limit on this (account, broker) pair (429 if
      exceeded) — protects the broker's own API from a runaway poll loop
      or repeated clicks, independent of whether a sync is *currently*
      running.
    - The existing per-connection lock (409 if already held) — a
      double-click, two open tabs, or a retried request while a sync is
      still running could otherwise race two sync_account() calls for the
      same connection against each other. Both would pass
      broker_sync_common.py's own trade-id pre-check before either
      commits, producing duplicate trades were it not for `transactions`'s
      own `uq_transactions_asset_external_ref` DB constraint (belt) plus
      this lock (suspenders, and the one that actually prevents a
      concurrent attempt from starting at all — same `try_acquire_lock`/
      `release_lock` pattern api.py's SME/screener/market-picks refresh
      endpoints already use). The lock is now held for the background
      job's lifetime, not just the HTTP request's, and is always released
      by the background job itself (_run_and_release's `finally`), never
      by this handler."""
    _require_supported_broker(broker)
    account_id = body.account_id
    lock_name = f"broker_sync:{account_id}:{broker}"
    rate_key = f"broker_sync_rl:{account_id}:{broker}"

    def _prepare() -> dict:
        import api
        from core import rate_limiter
        from core.crypto import EncryptionNotConfigured, decrypt
        from db.models import broker_connections
        from sqlalchemy import select, update

        if not rate_limiter.is_allowed(
            rate_key, _BROKER_SYNC_RATE_LIMIT_MAX_CALLS, _BROKER_SYNC_RATE_LIMIT_WINDOW_SECONDS,
        ):
            raise HTTPException(
                status_code=429,
                detail=f"too many {broker} sync attempts for this account — wait before retrying",
            )
        if not rate_limiter.try_acquire_lock(lock_name, _BROKER_SYNC_LOCK_TTL_SECONDS):
            raise HTTPException(status_code=409, detail=f"a sync for this {broker} connection is already running")

        try:
            with api._get_db_engine().begin() as conn:
                conn_row = conn.execute(
                    select(broker_connections.c.api_key, broker_connections.c.access_token_enc).where(
                        broker_connections.c.account_id == account_id,
                        broker_connections.c.broker == broker,
                    )
                ).first()
                if conn_row is None or not conn_row.access_token_enc:
                    raise HTTPException(status_code=404, detail=f"no connected {broker} account for this account_id")

                try:
                    access_token = decrypt(conn_row.access_token_enc)
                except EncryptionNotConfigured as exc:
                    raise HTTPException(status_code=503, detail=str(exc))
                except Exception:
                    raise HTTPException(
                        status_code=422,
                        detail="stored credential could not be decrypted — reconnect this broker account",
                    )

                conn.execute(
                    update(broker_connections)
                    .where(
                        broker_connections.c.account_id == account_id,
                        broker_connections.c.broker == broker,
                    )
                    .values(sync_status="syncing", last_sync_error=None)
                )
            return {"access_token": access_token, "api_key": conn_row.api_key}
        except Exception:
            rate_limiter.release_lock(lock_name)
            raise

    prepared = await run_owned_db_call(request, "portfolio_agg_write", 60, _prepare, "portfolio_agg_write")

    def _run_and_release() -> None:
        import api
        from core import rate_limiter
        from db.models import broker_connections
        from portfolio.portfolio_valuation import refresh_valuations
        from sqlalchemy import update

        # Three independently-guarded steps, not one big try/except: a
        # failure recording the outcome (the second step) must never stop
        # the lock release (the third) from running, and refresh_valuations()
        # runs on its own connection/transaction rather than nested inside
        # the one used for the final status update — the original
        # synchronous version of this endpoint never held a transaction open
        # across that call either, and there is no reason for this one to.
        result, error = None, None
        try:
            mod = _broker_sync_module(broker)
            result = mod.sync_account(
                api._get_db_engine(), account_id, prepared["access_token"], api_key=prepared["api_key"],
            )
            if "error" in result:
                error = result["error"]
            else:
                refresh_valuations(api._get_db_engine())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # No HTTP request is left to surface this to — record it on the
            # connection row instead, same "never let a background failure
            # be silent" instinct as pipelines/watchlist_alerts.py.
            api.log_event(api.LOGGER, "broker_sync_background_failed", level="error",
                           account_id=account_id, broker=broker, error=str(exc))
            error = str(exc)

        try:
            with api._get_db_engine().begin() as conn:
                if error is not None:
                    conn.execute(
                        update(broker_connections)
                        .where(
                            broker_connections.c.account_id == account_id,
                            broker_connections.c.broker == broker,
                        )
                        .values(sync_status="error", last_sync_error=error)
                    )
                else:
                    conn.execute(
                        update(broker_connections)
                        .where(
                            broker_connections.c.account_id == account_id,
                            broker_connections.c.broker == broker,
                        )
                        .values(sync_status="success", last_sync_summary=result, last_sync_error=None)
                    )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # A failure writing the outcome itself (e.g. a transient DB
            # blip) must not prevent the lock release below — a stuck
            # sync_status="syncing" with the lock released is recoverable
            # (the next sync attempt just overwrites it); a stuck lock
            # would not be, short of its TTL expiring.
            api.log_event(api.LOGGER, "broker_sync_status_write_failed", level="error",
                           account_id=account_id, broker=broker, error=str(exc))
        finally:
            rate_limiter.release_lock(lock_name)

    asyncio.get_running_loop().run_in_executor(_BROKER_SYNC_EXECUTOR, _run_and_release)
    return {"status": "syncing", "account_id": account_id, "broker": broker}


@router.get("/broker/connections")
async def list_broker_connections(request: Request, profile_id: int):
    def _sync() -> dict:
        import api
        from db.models import broker_connections
        from sqlalchemy import select

        with api._get_db_engine().connect() as conn:
            rows = conn.execute(
                select(
                    broker_connections.c.id,
                    broker_connections.c.account_id,
                    broker_connections.c.broker,
                    broker_connections.c.token_obtained_at,
                    broker_connections.c.last_synced_at,
                    broker_connections.c.sync_status,
                    broker_connections.c.last_sync_summary,
                    broker_connections.c.last_sync_error,
                )
                .where(broker_connections.c.profile_id == profile_id)
                .order_by(broker_connections.c.id)
            ).mappings().fetchall()
        # Never returns api_key/api_secret_enc/access_token_enc — connection
        # status/metadata only. `connected` distinguishes "app credentials
        # registered, OAuth handshake not yet completed" (token_obtained_at
        # is null — login-url ran, connect hasn't) from a fully live
        # connection, since a row now exists after the credentials-only step.
        return {"connections": [{**dict(r), "connected": r["token_obtained_at"] is not None} for r in rows]}

    return await run_owned_db_call(request, "portfolio_agg_read", 120, _sync, "portfolio_agg_read")
