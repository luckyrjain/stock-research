import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from db.models import (
    accounts,
    assets,
    broker_connections,
    holdings,
    metadata,
    profiles,
    transactions,
    valuations,
)
from portfolio.hdfc_sync import (
    _resolve_hdfc_symbol,
    authorise,
    get_access_token,
    start_login,
    submit_credentials,
    submit_otp,
    sync_account,
)

_TABLES = [profiles, accounts, assets, holdings, valuations, transactions, broker_connections]


def _sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    metadata.create_all(engine, tables=_TABLES)
    return engine


def _mk_connected_account(engine) -> int:
    with engine.begin() as conn:
        pid = conn.execute(insert(profiles).values(name="p")).inserted_primary_key[0]
        aid = conn.execute(
            insert(accounts).values(profile_id=pid, name="HDFC Sec", type="broker")
        ).inserted_primary_key[0]
        conn.execute(
            insert(broker_connections).values(
                profile_id=pid, account_id=aid, broker="hdfc_securities",
                access_token_enc="fake-enc", token_obtained_at=datetime.now(timezone.utc),
            )
        )
        return aid


# Real, confirmed shape (see module docstring / user-supplied glossary):
# no trading-symbol field at all — only isin/security_id/company_name.
_HOLDING = {
    "security_id": "",
    "exchange": "NSE",
    "company_name": "Tata Consultancy Services",
    "isin": "INE467B01029",
    "quantity": 10,
    "average_price": 3500.0,
    "close_price": 3650.5,
}

_TRADE = {
    "trade_id": "T001",
    "order_id": "O001",
    "trading_symbol": "TCS",
    "exchange": "NSE",
    "transaction_type": "BUY",
    "quantity": 5,
    "price": 3480.0,
    "trade_time": "2024-01-15T10:30:00",
}


def _mock_response(json_body: dict, status_ok: bool = True) -> Mock:
    resp = Mock()
    resp.json.return_value = json_body
    if status_ok:
        resp.raise_for_status = Mock()
    else:
        resp.raise_for_status = Mock(side_effect=Exception("HTTP error"))
    return resp


class LoginFlowTest(unittest.TestCase):
    """The 5-step login HDFC's real API actually requires — see
    portfolio/hdfc_sync.py's own module docstring. Each step is a thin
    requests wrapper; these tests confirm request shape (which param goes
    in the query string vs. the JSON body) and that a missing expected
    field degrades to {"error": ...}, never a KeyError."""

    @patch("portfolio.hdfc_sync.requests.get")
    def test_start_login_success(self, mock_get):
        # Real, confirmed shape: flat, camelCase, no "data" wrapper.
        mock_get.return_value = _mock_response({"tokenId": "tok-1"})
        result = start_login("my-key")
        self.assertEqual(result, {"token_id": "tok-1"})
        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args.kwargs["params"], {"api_key": "my-key"})

    @patch("portfolio.hdfc_sync.requests.get")
    def test_start_login_missing_token_id_is_error(self, mock_get):
        mock_get.return_value = _mock_response({})
        result = start_login("my-key")
        self.assertIn("error", result)

    @patch("portfolio.hdfc_sync.requests.get")
    def test_start_login_http_failure_returns_error_not_raise(self, mock_get):
        mock_get.return_value = _mock_response({}, status_ok=False)
        result = start_login("my-key")
        self.assertIn("error", result)

    @patch("portfolio.hdfc_sync.requests.post")
    def test_submit_credentials_success(self, mock_post):
        mock_post.return_value = _mock_response({"status": "ok"})
        result = submit_credentials("my-key", "tok-1", "user", "pass")
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(mock_post.call_args.kwargs["params"], {"api_key": "my-key", "token_id": "tok-1"})
        self.assertEqual(mock_post.call_args.kwargs["json"], {"username": "user", "password": "pass"})

    @patch("portfolio.hdfc_sync.requests.post")
    def test_submit_otp_success(self, mock_post):
        mock_post.return_value = _mock_response({"requestToken": "req-1"})
        result = submit_otp("my-key", "tok-1", "654321")
        self.assertEqual(result, {"request_token": "req-1"})
        self.assertEqual(mock_post.call_args.kwargs["json"], {"answer": "654321"})

    @patch("portfolio.hdfc_sync.requests.post")
    def test_submit_otp_missing_request_token_is_error(self, mock_post):
        mock_post.return_value = _mock_response({})
        result = submit_otp("my-key", "tok-1", "000000")
        self.assertIn("error", result)

    @patch("portfolio.hdfc_sync.requests.get")
    def test_authorise_success(self, mock_get):
        mock_get.return_value = _mock_response({"status": "ok"})
        result = authorise("my-key", "tok-1", "req-1")
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(
            mock_get.call_args.kwargs["params"],
            {"api_key": "my-key", "token_id": "tok-1", "consent": "true", "request_token": "req-1"},
        )

    @patch("portfolio.hdfc_sync.requests.post")
    def test_get_access_token_success(self, mock_post):
        mock_post.return_value = _mock_response({"accessToken": "at-1"})
        result = get_access_token("my-key", "my-secret", "req-1")
        self.assertEqual(result, {"access_token": "at-1"})
        self.assertEqual(mock_post.call_args.kwargs["params"], {"api_key": "my-key", "request_token": "req-1"})
        self.assertEqual(mock_post.call_args.kwargs["json"], {"apiSecret": "my-secret"})

    @patch("portfolio.hdfc_sync.requests.post")
    def test_get_access_token_missing_is_error(self, mock_post):
        mock_post.return_value = _mock_response({})
        result = get_access_token("my-key", "my-secret", "req-1")
        self.assertIn("error", result)


class ResolveHdfcSymbolTest(unittest.TestCase):
    """HDFC's real holdings response has no trading-symbol field at all
    (see module docstring) — this is the one function standing between
    that and every holding silently vanishing (the actual bug a live
    account first surfaced: 0 holdings synced despite real data existing,
    because the original code only ever looked for trading_symbol/symbol)."""

    @patch("portfolio.hdfc_sync.resolve_symbol")
    def test_isin_match_returns_canonical_symbol(self, mock_resolve):
        mock_resolve.return_value = {"symbol": "TCS", "exchange": "NSE", "confidence": "isin", "candidate_name": "TCS Ltd"}
        result = _resolve_hdfc_symbol("engine", {"isin": "INE467B01029", "security_id": ""}, master=[])
        self.assertEqual(result, ("TCS", "NSE"))
        mock_resolve.assert_called_once_with("engine", "", company_name=None, isin="INE467B01029", master=[])

    @patch("portfolio.hdfc_sync.resolve_symbol")
    def test_unresolved_falls_back_to_isin_never_drops_the_record(self, mock_resolve):
        mock_resolve.return_value = {"symbol": None, "exchange": None, "confidence": "unresolved", "candidate_name": None}
        result = _resolve_hdfc_symbol("engine", {"isin": "INE999Z99999", "exchange": "BSE"}, master=[])
        self.assertEqual(result, ("INE999Z99999", "BSE"))

    @patch("portfolio.hdfc_sync.resolve_symbol")
    def test_unresolved_with_no_isin_falls_back_to_security_id(self, mock_resolve):
        mock_resolve.return_value = {"symbol": None, "exchange": None, "confidence": "unresolved", "candidate_name": None}
        result = _resolve_hdfc_symbol("engine", {"security_id": "HDFC123"}, master=[])
        self.assertEqual(result, ("HDFC123", None))

    def test_no_isin_and_no_security_id_returns_none(self):
        result = _resolve_hdfc_symbol("engine", {"exchange": "NSE", "quantity": 10}, master=[])
        self.assertIsNone(result)


class SyncAccountTest(unittest.TestCase):
    def setUp(self):
        self.engine = _sqlite_engine()
        self.account_id = _mk_connected_account(self.engine)

        # This test DB (see _TABLES above) has no `securities` table — real
        # ISIN resolution needs one, so every test here mocks it rather
        # than hitting a table that doesn't exist. Resolves _HOLDING's
        # fixture ISIN to TCS/NSE, matching a real securities-master hit
        # (confidence="isin") — see _resolve_hdfc_symbol()'s own docstring
        # for why HDFC holdings need this at all (no trading-symbol field).
        master_patcher = patch("portfolio.hdfc_sync.get_full_securities_master", return_value=[])
        self.addCleanup(master_patcher.stop)
        master_patcher.start()

        def _fake_resolve_symbol(engine, code, company_name=None, isin=None, master=None):
            if isin == "INE467B01029":
                return {"symbol": "TCS", "exchange": "NSE", "confidence": "isin", "candidate_name": "Tata Consultancy Services"}
            return {"symbol": None, "exchange": None, "confidence": "unresolved", "candidate_name": None}

        resolve_patcher = patch("portfolio.hdfc_sync.resolve_symbol", side_effect=_fake_resolve_symbol)
        self.addCleanup(resolve_patcher.stop)
        resolve_patcher.start()

    @patch("portfolio.hdfc_sync._fetch_tradebook")
    @patch("portfolio.hdfc_sync._fetch_holdings")
    def test_syncs_holdings_and_trades(self, mock_holdings, mock_trades):
        mock_holdings.return_value = [_HOLDING]
        mock_trades.return_value = [_TRADE]

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["holdings_synced"], 1)
        self.assertEqual(result["holdings_skipped"], 0)
        self.assertEqual(result["trades_synced"], 1)
        self.assertEqual(result["trades_duplicate"], 0)

        with self.engine.connect() as conn:
            asset = conn.execute(select(assets).where(assets.c.symbol == "TCS")).mappings().first()
            self.assertIsNotNone(asset)
            self.assertEqual(asset["meta"]["source"], "hdfc_securities_api")

            holding = conn.execute(
                select(holdings).where(holdings.c.asset_id == asset["id"])
            ).mappings().first()
            self.assertEqual(float(holding["units"]), 10.0)
            self.assertEqual(float(holding["avg_cost"]), 3500.0)

            val = conn.execute(
                select(valuations).where(valuations.c.asset_id == asset["id"])
            ).mappings().first()
            self.assertEqual(float(val["value"]), 10 * 3650.5)

            txn = conn.execute(
                select(transactions).where(transactions.c.asset_id == asset["id"])
            ).mappings().first()
            self.assertEqual(txn["type"], "buy")
            self.assertEqual(float(txn["units"]), 5.0)
            self.assertEqual(txn["meta"]["trade_id"], "T001")
            self.assertEqual(txn["date"], date(2024, 1, 15))

            conn_row = conn.execute(
                select(broker_connections.c.last_synced_at).where(
                    broker_connections.c.account_id == self.account_id
                )
            ).first()
            self.assertIsNotNone(conn_row.last_synced_at)

    @patch("portfolio.hdfc_sync._fetch_tradebook")
    @patch("portfolio.hdfc_sync._fetch_holdings")
    def test_second_sync_does_not_duplicate_trade_or_holding(self, mock_holdings, mock_trades):
        mock_holdings.return_value = [_HOLDING]
        mock_trades.return_value = [_TRADE]
        sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        updated_holding = {**_HOLDING, "quantity": 10, "close_price": 3700.0}
        mock_holdings.return_value = [updated_holding]

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["trades_duplicate"], 1)
        self.assertEqual(result["trades_synced"], 0)

        with self.engine.connect() as conn:
            asset = conn.execute(select(assets).where(assets.c.symbol == "TCS")).mappings().first()
            txns = conn.execute(
                select(transactions).where(transactions.c.asset_id == asset["id"])
            ).mappings().fetchall()
            self.assertEqual(len(txns), 1)

            val = conn.execute(
                select(valuations).where(valuations.c.asset_id == asset["id"])
            ).mappings().first()
            self.assertEqual(float(val["value"]), 10 * 3700.0)

    @patch("portfolio.hdfc_sync._fetch_tradebook")
    @patch("portfolio.hdfc_sync._fetch_holdings")
    def test_malformed_holding_is_skipped_not_fatal(self, mock_holdings, mock_trades):
        mock_holdings.return_value = [{"exchange": "NSE"}]  # no isin/security_id to resolve, no quantity
        mock_trades.return_value = []

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["holdings_synced"], 0)
        self.assertEqual(result["holdings_skipped"], 1)

    @patch("portfolio.hdfc_sync._fetch_tradebook")
    @patch("portfolio.hdfc_sync._fetch_holdings")
    @patch("portfolio.broker_sync_common.time.sleep")  # opaque exception → call_with_backoff retries; skip the real sleeps
    def test_both_fetches_failing_returns_error_dict_not_raise(self, _mock_sleep, mock_holdings, mock_trades):
        mock_holdings.side_effect = Exception("network blip")
        mock_trades.side_effect = Exception("network blip")

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertIn("error", result)

    @patch("portfolio.hdfc_sync._fetch_tradebook")
    @patch("portfolio.hdfc_sync._fetch_holdings")
    @patch("portfolio.broker_sync_common.time.sleep")
    def test_holdings_failure_does_not_prevent_trades_from_syncing(self, _mock_sleep, mock_holdings, mock_trades):
        # Regression test: a bad holdings-endpoint guess (or a real broker-side
        # hiccup) must not silently discard an otherwise-working trades fetch —
        # this is exactly what happened when the original /portfolio/tradebook
        # guess 404'd while /portfolio/holdings worked fine, back when both
        # fetches shared one try/except and either failure killed the whole sync.
        mock_holdings.side_effect = Exception("network blip")
        mock_trades.return_value = [_TRADE]

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertNotIn("error", result)
        self.assertEqual(result["trades_synced"], 1)
        self.assertEqual(result.get("holdings_synced", 0), 0)

    @patch("portfolio.hdfc_sync._fetch_tradebook")
    @patch("portfolio.hdfc_sync._fetch_holdings")
    @patch("portfolio.broker_sync_common.time.sleep")
    def test_tradebook_failure_does_not_prevent_holdings_from_syncing(self, _mock_sleep, mock_holdings, mock_trades):
        mock_holdings.return_value = [_HOLDING]
        mock_trades.side_effect = Exception("network blip")

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertNotIn("error", result)
        self.assertEqual(result["holdings_synced"], 1)
        self.assertEqual(result.get("trades_synced", 0), 0)


if __name__ == "__main__":
    unittest.main()
