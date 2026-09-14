"""Broker API sync — login/connect/sync endpoints for the Portfolio
Aggregator's connected-broker integrations (Zerodha Kite Connect, HDFC
Securities, Paytm Money). Split out of routes/portfolio_aggregator.py,
which had grown past its own CRUD half's line count — same "next
increment of an already-endorsed pattern" this codebase already applied
to watchlist/positions/portfolio_aggregator (see backend/CLAUDE.md's
"Route module extraction" section, which named this exact split as
future work).

Imports its shared primitives (`_get_db_engine`, `_bearer_token_from_request`,
`LOGGER`, `log_event`, `run_owned_db_call`, `OwnedRequest`) from
routes/_shared.py rather than reaching into `api` for them — see
routes/watchlist.py's own docstring for why (a real dependency, not the
ordering-coincidence `import api` every router used before `_shared.py`
existed).

Also imports `_owned_account_id`/`_owned_profile_id` from
routes.portfolio_aggregator rather than duplicating them — they're
Portfolio-Aggregator-schema-specific (profiles -> accounts -> assets
ownership checks), not generic across every router the way
routes/_shared.py's own primitives are, and portfolio_aggregator.py is
the file that already owns them. Same "second domain imports the first
domain's already-established primitive" shape as routes/positions.py
importing WatchlistOwner/owner_column/resolve_owner from
routes/watchlist.py.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from routes._shared import (
    LOGGER,
    OwnedRequest,
    _bearer_token_from_request,
    _get_db_engine,
    log_event,
    run_owned_db_call,
)
from routes.portfolio_aggregator import _owned_account_id, _owned_profile_id
from routes.watchlist import resolve_owner

router = APIRouter(prefix="/api/portfolio")


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


class BrokerLoginUrlIn(OwnedRequest):
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


class BrokerConnectIn(OwnedRequest):
    account_id: int
    request_token: str


class BrokerSyncIn(OwnedRequest):
    """client_id (inherited from OwnedRequest) is used only to mirror synced
    holdings into `positions` (see
    portfolio/positions_mirror.py's upsert_position_from_holding) — not an
    ownership check here; ownership resolves via account_id -> profile_id."""
    account_id: int


class HdfcLoginStartIn(OwnedRequest):
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


class HdfcVerifyOtpIn(OwnedRequest):
    account_id: int
    otp: str = Field(min_length=1, max_length=20)


def _require_supported_broker(broker: str) -> None:
    if broker not in _SUPPORTED_BROKERS:
        raise HTTPException(status_code=422, detail=f"broker must be one of: {sorted(_SUPPORTED_BROKERS)}")


def _validate_credential_pair(api_key: str | None, api_secret: str | None) -> None:
    """Both-or-neither: first-time/replace setup must supply both app
    credentials, a resume/retry call omits both to reuse what's already
    registered — either alone is a 422."""
    if bool(api_key) != bool(api_secret):
        raise HTTPException(status_code=422, detail="provide both api_key and api_secret, or neither to reuse saved credentials")


def _connection_reset_values(**extra) -> dict:
    """Column resets applied whenever a broker_connections row's app
    credentials are replaced — a prior access token was minted for
    credentials that no longer exist, and a prior sync's outcome recorded
    under them would misleadingly imply the new credentials already synced
    something. `extra` carries any additional columns a specific call site
    also needs cleared (e.g. hdfc_securities' pending_token_id)."""
    return {
        "access_token_enc": None,
        "token_obtained_at": None,
        "sync_status": "idle",
        "last_sync_summary": None,
        "last_sync_error": None,
        **extra,
    }


def _decrypt_or_422(ciphertext: str, error_detail: str) -> str:
    """Decrypts `ciphertext`, letting EncryptionNotConfigured surface as a
    503 (unchanged) and translating any other decrypt failure into a 422
    with `error_detail` — the message differs per call site (a corrupted
    app secret vs. access token point the caller at different fixes), so
    it's a parameter rather than a single hardcoded string."""
    from core.crypto import EncryptionNotConfigured, decrypt

    try:
        return decrypt(ciphertext)
    except EncryptionNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=422, detail=error_detail)


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
    _validate_credential_pair(body.api_key, body.api_secret)

    def _sync() -> dict:
        from core.crypto import EncryptionNotConfigured, encrypt
        from db.models import accounts, broker_connections
        from sqlalchemy import insert, select, update

        mod = _broker_sync_module(broker)
        owner = resolve_owner(_bearer_token_from_request(request), body.client_id)

        with _get_db_engine().begin() as conn:
            _owned_account_id(conn, owner, body.account_id)
            account = conn.execute(
                select(accounts.c.id, accounts.c.profile_id, accounts.c.type)
                .where(accounts.c.id == body.account_id)
            ).first()
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
                    .values(api_key=body.api_key, api_secret_enc=api_secret_enc, **_connection_reset_values())
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

        from core.crypto import EncryptionNotConfigured, encrypt
        from db.models import broker_connections
        from sqlalchemy import select, update

        mod = _broker_sync_module(broker)
        owner = resolve_owner(_bearer_token_from_request(request), body.client_id)

        with _get_db_engine().begin() as conn:
            _owned_account_id(conn, owner, body.account_id)
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

            api_secret = _decrypt_or_422(
                conn_row.api_secret_enc,
                "stored app secret could not be decrypted — re-register this broker's API key/secret",
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
                # See the identical comment on the hdfc_securities verify-otp
                # handler above — a fresh token invalidates whatever the
                # previous token's last sync attempt recorded.
                .values(access_token_enc=token_enc, token_obtained_at=now, sync_status="idle", last_sync_error=None)
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
    supplied · `409` a login attempt for this account is already in
    progress · `422` exactly one of api_key/api_secret supplied, or HDFC
    rejected the login/credentials step · `503` PORTFOLIO_ENCRYPTION_KEY
    unset (first-time/replace mode only)."""
    _validate_credential_pair(body.api_key, body.api_secret)

    def _sync() -> dict:
        from core import rate_limiter

        owner = resolve_owner(_bearer_token_from_request(request), body.client_id)
        with _get_db_engine().connect() as conn:
            _owned_account_id(conn, owner, body.account_id)

        # Held from here through hdfc_verify_otp()'s own release (success or
        # failure) — spans two separate HTTP requests, same as
        # pending_token_id itself already does, since HDFC's real login is a
        # two-request handshake with no way to collapse it into one. Without
        # this, two concurrent login-start calls for the same account (two
        # tabs, a retried timeout) could each independently reach HDFC, and
        # whichever's pending_token_id write landed last would silently
        # invalidate the other's already-dispatched OTP — same
        # `try_acquire_lock`/`release_lock` pattern broker_sync() already
        # uses for the identical "don't let two attempts interleave"
        # problem, just spanning a login handshake instead of a sync job.
        # TTL-bounded so an abandoned login-start (user never submits the
        # OTP) self-heals rather than locking this account's HDFC connect
        # flow out forever.
        lock_name = f"broker_hdfc_login:{body.account_id}"
        if not rate_limiter.try_acquire_lock(lock_name, _BROKER_SYNC_LOCK_TTL_SECONDS):
            raise HTTPException(status_code=409, detail="a hdfc_securities login attempt for this account is already in progress")

        try:
            return _do_login_start()
        except Exception:
            rate_limiter.release_lock(lock_name)
            raise

    def _do_login_start() -> dict:
        from core.crypto import EncryptionNotConfigured, encrypt
        from db.models import accounts, broker_connections
        from portfolio import hdfc_sync
        from sqlalchemy import insert, select, update

        # Credential registration is its own committed transaction, separate
        # from the two blocking HDFC network calls below — same reasoning as
        # hdfc_verify_otp()'s own pending_token_id-cleared-first restructure
        # (see that handler's comment): holding a transaction open across an
        # outbound HTTP call to a third party ties up a pooled DB connection
        # and a row lock on this account's broker_connections row for as
        # long as HDFC takes to respond (up to hdfc_sync._TIMEOUT per call),
        # for no benefit — nothing here needs the credential write and the
        # HDFC calls to commit atomically together. Safe now that the lock
        # above serializes concurrent attempts for this account — the old
        # single-transaction version's row lock was incidentally doing part
        # of that job before this restructure existed.
        with _get_db_engine().begin() as conn:
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
                            **_connection_reset_values(pending_token_id=None),
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

        with _get_db_engine().begin() as conn:
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

        from core import rate_limiter
        from core.crypto import EncryptionNotConfigured, encrypt
        from db.models import broker_connections
        from portfolio import hdfc_sync
        from sqlalchemy import select, update

        engine = _get_db_engine()
        owner = resolve_owner(_bearer_token_from_request(request), body.client_id)

        with engine.connect() as conn:
            _owned_account_id(conn, owner, body.account_id)
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
        #
        # Compare-and-swap on the value just read, not a bare id match —
        # defense in depth alongside the broker_hdfc_login:{account_id} lock
        # login-start now holds across this whole handshake: the lock is
        # what actually prevents a second concurrent attempt from ever
        # reaching here while this one is in flight, but a CAS clear means
        # even a lock-acquisition bug or a future caller that forgets to
        # hold it can't silently wipe a DIFFERENT, newer login attempt's
        # still-unconsumed token — this UPDATE only ever clears the exact
        # value this request itself read.
        with engine.begin() as conn:
            clear_result = conn.execute(
                update(broker_connections)
                .where(
                    broker_connections.c.id == conn_row.id,
                    broker_connections.c.pending_token_id == conn_row.pending_token_id,
                )
                .values(pending_token_id=None)
            )
        if clear_result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="no hdfc_securities login in progress for this account — call login-start first",
            )

        # Acquired by login-start, released here — the one place this
        # login attempt's lock can end, whether the rest of this handshake
        # succeeds or fails below.
        lock_name = f"broker_hdfc_login:{body.account_id}"
        try:
            otp_result = hdfc_sync.submit_otp(conn_row.api_key, conn_row.pending_token_id, body.otp)
            if "error" in otp_result:
                raise HTTPException(status_code=422, detail=otp_result["error"])
            request_token = otp_result["request_token"]

            authorise_result = hdfc_sync.authorise(conn_row.api_key, conn_row.pending_token_id, request_token)
            if "error" in authorise_result:
                raise HTTPException(status_code=422, detail=authorise_result["error"])

            api_secret = _decrypt_or_422(
                conn_row.api_secret_enc,
                "stored app secret could not be decrypted — re-register this broker's API key/secret",
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
                    # A fresh token invalidates whatever the *previous* token's
                    # last sync attempt recorded — without clearing these, the
                    # frontend's poll immediately re-displays the stale
                    # last_sync_error right after showing "Connected.", making a
                    # successful reconnect look like it failed again. `idle`
                    # matches what a connection has before its first-ever sync
                    # (see BrokerConnection.sync_status's own comment).
                    .values(
                        access_token_enc=token_enc, token_obtained_at=now, pending_token_id=None,
                        sync_status="idle", last_sync_error=None,
                    )
                )
            return {"connected": True, "account_id": body.account_id, "broker": "hdfc_securities"}
        finally:
            rate_limiter.release_lock(lock_name)

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
        from core import rate_limiter
        from db.models import broker_connections
        from sqlalchemy import select, update

        # Resolved here, inside the executor thread _prepare() already runs
        # on via run_owned_db_call() below — resolve_owner() does a real DB
        # query when a bearer token is present (routes/watchlist.py's own
        # docstring: "runs inside the same executor thread as the caller's
        # other DB work"), so calling it directly in this async handler
        # would block the event loop for every other concurrent request on
        # this worker. Required, not best-effort, now that every account has
        # a real owner (_owned_account_id below) — the same owner also mirrors
        # a synced holding into `positions` under the SAME column GET
        # /api/positions actually reads for that caller (a signed-in session
        # always wins over client_id, per resolve_owner's own docstring, so
        # this lands visibly on that caller's own /portfolio page either way).
        #
        # Checked in its own connection, before either guard below — the
        # rate limit and lock are keyed only by account_id/broker, not by
        # caller identity, so checking ownership after them would let a
        # caller who doesn't even own this account burn through the real
        # owner's rate-limit budget and acquire/release their sync lock
        # before ever hitting a 404. Every other broker endpoint in this
        # module already checks ownership before touching any shared
        # guard state; this one now matches.
        engine = _get_db_engine()
        owner = resolve_owner(_bearer_token_from_request(request), body.client_id)
        with engine.connect() as conn:
            _owned_account_id(conn, owner, account_id)

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
            with engine.begin() as conn:
                conn_row = conn.execute(
                    select(broker_connections.c.api_key, broker_connections.c.access_token_enc).where(
                        broker_connections.c.account_id == account_id,
                        broker_connections.c.broker == broker,
                    )
                ).first()
                if conn_row is None or not conn_row.access_token_enc:
                    raise HTTPException(status_code=404, detail=f"no connected {broker} account for this account_id")

                access_token = _decrypt_or_422(
                    conn_row.access_token_enc,
                    "stored credential could not be decrypted — reconnect this broker account",
                )

                conn.execute(
                    update(broker_connections)
                    .where(
                        broker_connections.c.account_id == account_id,
                        broker_connections.c.broker == broker,
                    )
                    .values(sync_status="syncing", last_sync_error=None)
                )
            return {"access_token": access_token, "api_key": conn_row.api_key, "owner": owner}
        except Exception:
            rate_limiter.release_lock(lock_name)
            raise

    prepared = await run_owned_db_call(request, "portfolio_agg_write", 60, _prepare, "portfolio_agg_write")

    def _run_and_release() -> None:
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
                _get_db_engine(), account_id, prepared["access_token"], api_key=prepared["api_key"],
                owner=prepared["owner"],
            )
            if "error" in result:
                error = result["error"]
            # Runs whenever sync_account() actually wrote something, even on
            # a partial failure (e.g. hdfc_sync.py's holdings-succeeded/
            # trades-failed case still commits real holdings) — result
            # carries an "error" key alongside real synced counts in that
            # case, not just on total failure, so gating this on "no error"
            # would skip revaluing data that did land.
            if result.get("holdings_synced") or result.get("trades_synced"):
                refresh_valuations(_get_db_engine())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # No HTTP request is left to surface this to — record it on the
            # connection row instead, same "never let a background failure
            # be silent" instinct as pipelines/watchlist_alerts.py.
            log_event(LOGGER, "broker_sync_background_failed", level="error",
                      account_id=account_id, broker=broker, error=str(exc))
            error = str(exc)

        try:
            with _get_db_engine().begin() as conn:
                if error is not None:
                    conn.execute(
                        update(broker_connections)
                        .where(
                            broker_connections.c.account_id == account_id,
                            broker_connections.c.broker == broker,
                        )
                        # `result` still carries real synced counts on a
                        # partial-fetch failure (see hdfc_sync.py's
                        # sync_account()) — stored alongside the error so the
                        # frontend can show "X holdings, Y trades synced" next
                        # to the failure, not just the bare error string.
                        # `None` on a total failure (the exception path
                        # above), same as before this fix.
                        .values(sync_status="error", last_sync_summary=result, last_sync_error=error)
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
            log_event(LOGGER, "broker_sync_status_write_failed", level="error",
                      account_id=account_id, broker=broker, error=str(exc))
        finally:
            rate_limiter.release_lock(lock_name)

    asyncio.get_running_loop().run_in_executor(_BROKER_SYNC_EXECUTOR, _run_and_release)
    return {"status": "syncing", "account_id": account_id, "broker": broker}


@router.get("/broker/connections")
async def list_broker_connections(request: Request, profile_id: int, client_id: str | None = None):
    def _sync() -> dict:
        from db.models import broker_connections
        from sqlalchemy import select

        owner = resolve_owner(_bearer_token_from_request(request), client_id)
        with _get_db_engine().connect() as conn:
            _owned_profile_id(conn, owner, profile_id)
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
