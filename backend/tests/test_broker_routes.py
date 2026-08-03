"""Endpoint tests for the broker-API routes in routes/portfolio_aggregator.py
(docs/PRD-gmail-portfolio-intelligence.md Phase 1) — same SQLite-in-memory
approach as test_portfolio_aggregator.py's own reference design.
"""
import os
import unittest
import warnings
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

import api
from core import rate_limiter
from db.models import accounts, broker_connections, metadata, profiles

client = TestClient(api.app)


def _silence_sqlite_date_adapter_warning() -> None:
    warnings.filterwarnings(
        "ignore", message="The default date adapter is deprecated", category=DeprecationWarning,
    )


class BrokerRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        _silence_sqlite_date_adapter_warning()
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
        )
        metadata.create_all(self.engine, tables=[profiles, accounts, broker_connections])
        self._old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "sqlite://"
        self._old_engine = api._DB_ENGINE
        api._DB_ENGINE = self.engine

        self._old_enc_key = os.environ.get("PORTFOLIO_ENCRYPTION_KEY")
        os.environ["PORTFOLIO_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        self._old_kite_key = os.environ.get("KITE_API_KEY")
        self._old_kite_secret = os.environ.get("KITE_API_SECRET")
        os.environ["KITE_API_KEY"] = "test-api-key"
        os.environ["KITE_API_SECRET"] = "test-api-secret"
        self._old_hdfc_key = os.environ.get("HDFC_SEC_API_KEY")
        self._old_hdfc_secret = os.environ.get("HDFC_SEC_API_SECRET")
        os.environ["HDFC_SEC_API_KEY"] = "test-hdfc-key"
        os.environ["HDFC_SEC_API_SECRET"] = "test-hdfc-secret"
        self._old_paytm_key = os.environ.get("PAYTM_MONEY_API_KEY")
        self._old_paytm_secret = os.environ.get("PAYTM_MONEY_API_SECRET")
        os.environ["PAYTM_MONEY_API_KEY"] = "test-paytm-key"
        os.environ["PAYTM_MONEY_API_SECRET"] = "test-paytm-secret"

        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        api._DB_ENGINE = self._old_engine
        for var, old in [
            ("DATABASE_URL", self._old_db_url),
            ("PORTFOLIO_ENCRYPTION_KEY", self._old_enc_key),
            ("KITE_API_KEY", self._old_kite_key),
            ("KITE_API_SECRET", self._old_kite_secret),
            ("HDFC_SEC_API_KEY", self._old_hdfc_key),
            ("HDFC_SEC_API_SECRET", self._old_hdfc_secret),
            ("PAYTM_MONEY_API_KEY", self._old_paytm_key),
            ("PAYTM_MONEY_API_SECRET", self._old_paytm_secret),
        ]:
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old
        rate_limiter._memory_calls.clear()

    def _mk_profile(self) -> int:
        res = client.post("/api/portfolio/profiles", json={"name": "me"})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["id"]

    def _mk_account(self, profile_id: int, type_: str = "broker") -> int:
        res = client.post("/api/portfolio/accounts", json={
            "profile_id": profile_id, "name": "Zerodha", "type": type_,
        })
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["id"]

    # ── login-url ────────────────────────────────────────────────────────────

    def test_login_url_unsupported_broker_422(self) -> None:
        resp = client.get("/api/portfolio/broker/robinhood/login-url")
        self.assertEqual(resp.status_code, 422)

    def test_login_url_missing_api_key_503(self) -> None:
        os.environ.pop("KITE_API_KEY", None)
        resp = client.get("/api/portfolio/broker/zerodha/login-url")
        self.assertEqual(resp.status_code, 503)

    @patch("portfolio.kite_sync.get_login_url")
    def test_login_url_success(self, mock_login_url) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?api_key=test-api-key&v=3"
        resp = client.get("/api/portfolio/broker/zerodha/login-url")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("kite.trade", resp.json()["login_url"])

    # ── connect ──────────────────────────────────────────────────────────────

    def test_connect_missing_account_404(self) -> None:
        resp = client.post("/api/portfolio/broker/zerodha/connect", json={
            "account_id": 999999, "request_token": "rt",
        })
        self.assertEqual(resp.status_code, 404)

    def test_connect_non_broker_account_type_422(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid, type_="bank")
        resp = client.post("/api/portfolio/broker/zerodha/connect", json={
            "account_id": acc, "request_token": "rt",
        })
        self.assertEqual(resp.status_code, 422)

    @patch("portfolio.kite_sync.exchange_request_token")
    def test_connect_kite_error_422(self, mock_exchange) -> None:
        mock_exchange.return_value = {"error": "bad request_token"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/broker/zerodha/connect", json={
            "account_id": acc, "request_token": "bad",
        })
        self.assertEqual(resp.status_code, 422)

    @patch("portfolio.kite_sync.exchange_request_token")
    def test_connect_success_stores_encrypted_token(self, mock_exchange) -> None:
        mock_exchange.return_value = {"access_token": "real-kite-access-token", "user_id": "AB1234"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)

        resp = client.post("/api/portfolio/broker/zerodha/connect", json={
            "account_id": acc, "request_token": "good-token",
        })
        self.assertEqual(resp.status_code, 200, resp.text)

        with self.engine.connect() as conn:
            row = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().first()
            self.assertIsNotNone(row)
            self.assertIsNotNone(row["access_token_enc"])
            self.assertNotIn("real-kite-access-token", row["access_token_enc"])
            self.assertEqual(row["profile_id"], pid)

    @patch("portfolio.kite_sync.exchange_request_token")
    def test_reconnect_upserts_not_duplicates(self, mock_exchange) -> None:
        mock_exchange.return_value = {"access_token": "token-1"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        client.post("/api/portfolio/broker/zerodha/connect",
                     json={"account_id": acc, "request_token": "rt1"})

        mock_exchange.return_value = {"access_token": "token-2"}
        resp = client.post("/api/portfolio/broker/zerodha/connect",
                            json={"account_id": acc, "request_token": "rt2"})
        self.assertEqual(resp.status_code, 200)

        with self.engine.connect() as conn:
            rows = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().fetchall()
            self.assertEqual(len(rows), 1)

    # ── sync ─────────────────────────────────────────────────────────────────

    def test_sync_no_connection_404(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc})
        self.assertEqual(resp.status_code, 404)

    @patch("portfolio.portfolio_valuation.refresh_valuations")
    @patch("portfolio.kite_sync.sync_account")
    @patch("portfolio.kite_sync.exchange_request_token")
    def test_sync_success_returns_summary(self, mock_exchange, mock_sync, mock_refresh) -> None:
        mock_exchange.return_value = {"access_token": "token-1"}
        mock_sync.return_value = {"holdings_synced": 3, "trades_synced": 1}
        mock_refresh.return_value = {"valued": 3, "skipped": 0}

        pid = self._mk_profile()
        acc = self._mk_account(pid)
        client.post("/api/portfolio/broker/zerodha/connect",
                     json={"account_id": acc, "request_token": "rt"})

        resp = client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"holdings_synced": 3, "trades_synced": 1})
        mock_sync.assert_called_once()

    @patch("portfolio.kite_sync.sync_account")
    @patch("portfolio.kite_sync.exchange_request_token")
    def test_sync_propagates_kite_error_as_422(self, mock_exchange, mock_sync) -> None:
        mock_exchange.return_value = {"access_token": "token-1"}
        mock_sync.return_value = {"error": "session expired"}

        pid = self._mk_profile()
        acc = self._mk_account(pid)
        client.post("/api/portfolio/broker/zerodha/connect",
                     json={"account_id": acc, "request_token": "rt"})

        resp = client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc})
        self.assertEqual(resp.status_code, 422)

    # ── multi-broker dispatch (HDFC Securities, Paytm Money) ────────────────
    # Not a full repeat of every zerodha case above — just enough per broker
    # to prove _broker_sync_module()/_BROKER_ENV_KEYS actually dispatch to
    # the right sync module and env vars, since that's the only thing that
    # changed by adding a second/third broker.

    @patch("portfolio.hdfc_sync.get_login_url")
    def test_login_url_success_hdfc_securities(self, mock_login_url) -> None:
        mock_login_url.return_value = "https://developer.hdfcsec.com/login?api_key=test-hdfc-key"
        resp = client.get("/api/portfolio/broker/hdfc_securities/login-url")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hdfcsec.com", resp.json()["login_url"])
        mock_login_url.assert_called_once_with("test-hdfc-key")

    @patch("portfolio.hdfc_sync.exchange_request_token")
    def test_connect_success_hdfc_securities(self, mock_exchange) -> None:
        mock_exchange.return_value = {"access_token": "hdfc-access-token"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/broker/hdfc_securities/connect", json={
            "account_id": acc, "request_token": "rt",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        mock_exchange.assert_called_once_with("test-hdfc-key", "test-hdfc-secret", "rt")

        with self.engine.connect() as conn:
            row = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().first()
            self.assertEqual(row["broker"], "hdfc_securities")
            self.assertNotIn("hdfc-access-token", row["access_token_enc"])

    @patch("portfolio.paytm_sync.get_login_url")
    def test_login_url_success_paytm_money(self, mock_login_url) -> None:
        mock_login_url.return_value = "https://login.paytmmoney.com/merchant-login?apiKey=test-paytm-key"
        resp = client.get("/api/portfolio/broker/paytm_money/login-url")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("paytmmoney.com", resp.json()["login_url"])
        mock_login_url.assert_called_once_with("test-paytm-key")

    @patch("portfolio.paytm_sync.exchange_request_token")
    def test_connect_success_paytm_money(self, mock_exchange) -> None:
        mock_exchange.return_value = {"access_token": "paytm-access-token"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/broker/paytm_money/connect", json={
            "account_id": acc, "request_token": "rt",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        mock_exchange.assert_called_once_with("test-paytm-key", "test-paytm-secret", "rt")

    @patch("portfolio.portfolio_valuation.refresh_valuations")
    @patch("portfolio.paytm_sync.sync_account")
    @patch("portfolio.paytm_sync.exchange_request_token")
    def test_sync_success_paytm_money_dispatches_to_paytm_module(
        self, mock_exchange, mock_sync, _mock_refresh,
    ) -> None:
        mock_exchange.return_value = {"access_token": "token-1"}
        mock_sync.return_value = {"holdings_synced": 2, "trades_synced": 0}

        pid = self._mk_profile()
        acc = self._mk_account(pid)
        client.post("/api/portfolio/broker/paytm_money/connect",
                     json={"account_id": acc, "request_token": "rt"})

        resp = client.post("/api/portfolio/broker/paytm_money/sync", json={"account_id": acc})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"holdings_synced": 2, "trades_synced": 0})
        mock_sync.assert_called_once()
        # Confirms the sync call used Paytm Money's own api_key env var, not Kite's.
        self.assertEqual(mock_sync.call_args.kwargs.get("api_key"), "test-paytm-key")

    # ── connections list ─────────────────────────────────────────────────────

    @patch("portfolio.kite_sync.exchange_request_token")
    def test_list_connections_never_exposes_token(self, mock_exchange) -> None:
        mock_exchange.return_value = {"access_token": "secret-token"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        client.post("/api/portfolio/broker/zerodha/connect",
                     json={"account_id": acc, "request_token": "rt"})

        resp = client.get(f"/api/portfolio/broker/connections?profile_id={pid}")
        self.assertEqual(resp.status_code, 200)
        conns = resp.json()["connections"]
        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0]["broker"], "zerodha")
        self.assertNotIn("access_token_enc", conns[0])
        self.assertNotIn("secret-token", str(resp.json()))


if __name__ == "__main__":
    unittest.main()
