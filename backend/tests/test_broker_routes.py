"""Endpoint tests for the broker-API routes in routes/portfolio_aggregator.py
(docs/PRD-gmail-portfolio-intelligence.md Phase 1) — same SQLite-in-memory
approach as test_portfolio_aggregator.py's own reference design.

Credentials are per-connection (account_id, broker), never a deployment-wide
env var — see db/models.py's broker_connections comment and
routes/portfolio_aggregator.py's BrokerLoginUrlIn for why: a Kite Connect/
HDFC Securities/Paytm Money "app" is always registered under one specific
broker login, so a single global env var would only ever work for one
person's one broker account, not "whoever connects an account." Every test
below registers its own api_key/api_secret via login-url before connecting.
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

        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        api._DB_ENGINE = self._old_engine
        for var, old in [
            ("DATABASE_URL", self._old_db_url),
            ("PORTFOLIO_ENCRYPTION_KEY", self._old_enc_key),
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
            "profile_id": profile_id, "name": "My Broker", "type": type_,
        })
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["id"]

    def _register_credentials(self, broker: str, account_id: int, api_key: str, api_secret: str):
        return client.post(f"/api/portfolio/broker/{broker}/login-url", json={
            "account_id": account_id, "api_key": api_key, "api_secret": api_secret,
        })

    # ── login-url (register app credentials + return login URL) ─────────────

    def test_login_url_unsupported_broker_422(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/broker/robinhood/login-url", json={
            "account_id": acc, "api_key": "k", "api_secret": "s",
        })
        self.assertEqual(resp.status_code, 422)

    def test_login_url_missing_account_404(self) -> None:
        resp = client.post("/api/portfolio/broker/zerodha/login-url", json={
            "account_id": 999999, "api_key": "k", "api_secret": "s",
        })
        self.assertEqual(resp.status_code, 404)

    def test_login_url_non_broker_account_type_422(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid, type_="bank")
        resp = client.post("/api/portfolio/broker/zerodha/login-url", json={
            "account_id": acc, "api_key": "k", "api_secret": "s",
        })
        self.assertEqual(resp.status_code, 422)

    def test_login_url_missing_encryption_key_503(self) -> None:
        os.environ.pop("PORTFOLIO_ENCRYPTION_KEY", None)
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = self._register_credentials("zerodha", acc, "k", "s")
        self.assertEqual(resp.status_code, 503)

    @patch("portfolio.kite_sync.get_login_url")
    def test_login_url_success_stores_key_plaintext_and_secret_encrypted(self, mock_login_url) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?api_key=my-zerodha-key&v=3"
        pid = self._mk_profile()
        acc = self._mk_account(pid)

        resp = self._register_credentials("zerodha", acc, "my-zerodha-key", "my-zerodha-secret")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("kite.trade", resp.json()["login_url"])
        mock_login_url.assert_called_once_with("my-zerodha-key")

        with self.engine.connect() as conn:
            row = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().first()
            self.assertEqual(row["api_key"], "my-zerodha-key")
            self.assertIsNotNone(row["api_secret_enc"])
            self.assertNotIn("my-zerodha-secret", row["api_secret_enc"])
            self.assertIsNone(row["access_token_enc"])  # OAuth handshake hasn't happened yet
            self.assertEqual(row["profile_id"], pid)

    @patch("portfolio.kite_sync.get_login_url")
    def test_login_url_called_twice_upserts_credentials_not_duplicates(self, mock_login_url) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "key-1", "secret-1")
        self._register_credentials("zerodha", acc, "key-2", "secret-2")

        with self.engine.connect() as conn:
            rows = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["api_key"], "key-2")

    def test_login_url_only_api_key_without_secret_422(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/broker/zerodha/login-url", json={
            "account_id": acc, "api_key": "k",
        })
        self.assertEqual(resp.status_code, 422)

    def test_login_url_resume_without_credentials_but_none_registered_404(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/broker/zerodha/login-url", json={"account_id": acc})
        self.assertEqual(resp.status_code, 404)

    @patch("portfolio.kite_sync.get_login_url")
    def test_login_url_resume_reuses_registered_credentials_without_resupplying_secret(
        self, mock_login_url,
    ) -> None:
        mock_login_url.side_effect = lambda api_key: f"https://kite.trade/connect/login?api_key={api_key}"
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "already-registered-key", "already-registered-secret")

        # Resume: no api_key/api_secret in the body at all.
        resp = client.post("/api/portfolio/broker/zerodha/login-url", json={"account_id": acc})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("already-registered-key", resp.json()["login_url"])

        with self.engine.connect() as conn:
            row = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().first()
            # Resuming must not disturb the stored secret or reset a token
            # that (in this scenario) was never obtained yet in the first place.
            self.assertEqual(row["api_key"], "already-registered-key")
            self.assertIsNotNone(row["api_secret_enc"])

    # ── connect (exchange request_token for an access_token) ────────────────

    @patch("portfolio.kite_sync.exchange_request_token")
    @patch("portfolio.kite_sync.get_login_url")
    def test_reregistering_credentials_clears_stale_access_token(self, mock_login_url, mock_exchange) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        mock_exchange.return_value = {"access_token": "token-for-old-key"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "old-key", "old-secret")
        client.post("/api/portfolio/broker/zerodha/connect", json={"account_id": acc, "request_token": "rt"})

        # Re-registering with a different key must invalidate the token
        # minted under the old one — it can't possibly still be valid for
        # a different app's credentials.
        self._register_credentials("zerodha", acc, "new-key", "new-secret")

        with self.engine.connect() as conn:
            row = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().first()
            self.assertEqual(row["api_key"], "new-key")
            self.assertIsNone(row["access_token_enc"])
            self.assertIsNone(row["token_obtained_at"])

        resp = client.get(f"/api/portfolio/broker/connections?profile_id={pid}")
        self.assertFalse(resp.json()["connections"][0]["connected"])

    def test_connect_no_registered_credentials_404(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/broker/zerodha/connect", json={
            "account_id": acc, "request_token": "rt",
        })
        self.assertEqual(resp.status_code, 404)

    @patch("portfolio.kite_sync.exchange_request_token")
    @patch("portfolio.kite_sync.get_login_url")
    def test_connect_kite_error_422(self, mock_login_url, mock_exchange) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        mock_exchange.return_value = {"error": "bad request_token"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "k", "s")

        resp = client.post("/api/portfolio/broker/zerodha/connect", json={
            "account_id": acc, "request_token": "bad",
        })
        self.assertEqual(resp.status_code, 422)

    @patch("portfolio.kite_sync.exchange_request_token")
    @patch("portfolio.kite_sync.get_login_url")
    def test_connect_success_stores_encrypted_token_and_reuses_registered_credentials(
        self, mock_login_url, mock_exchange,
    ) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        mock_exchange.return_value = {"access_token": "real-kite-access-token", "user_id": "AB1234"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "my-key", "my-secret")

        resp = client.post("/api/portfolio/broker/zerodha/connect", json={
            "account_id": acc, "request_token": "good-token",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        mock_exchange.assert_called_once_with("my-key", "my-secret", "good-token")

        with self.engine.connect() as conn:
            row = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().first()
            self.assertIsNotNone(row["access_token_enc"])
            self.assertNotIn("real-kite-access-token", row["access_token_enc"])

    @patch("portfolio.kite_sync.exchange_request_token")
    @patch("portfolio.kite_sync.get_login_url")
    def test_reconnect_upserts_not_duplicates(self, mock_login_url, mock_exchange) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        mock_exchange.return_value = {"access_token": "token-1"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "k", "s")
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
    @patch("portfolio.kite_sync.get_login_url")
    def test_sync_success_returns_summary_and_reuses_registered_api_key(
        self, mock_login_url, mock_exchange, mock_sync, mock_refresh,
    ) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        mock_exchange.return_value = {"access_token": "token-1"}
        mock_sync.return_value = {"holdings_synced": 3, "trades_synced": 1}
        mock_refresh.return_value = {"valued": 3, "skipped": 0}

        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "my-key", "my-secret")
        client.post("/api/portfolio/broker/zerodha/connect",
                     json={"account_id": acc, "request_token": "rt"})

        resp = client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"holdings_synced": 3, "trades_synced": 1})
        mock_sync.assert_called_once()
        self.assertEqual(mock_sync.call_args.kwargs.get("api_key"), "my-key")

    @patch("portfolio.portfolio_valuation.refresh_valuations")
    @patch("portfolio.kite_sync.sync_account")
    @patch("portfolio.kite_sync.exchange_request_token")
    @patch("portfolio.kite_sync.get_login_url")
    def test_concurrent_sync_for_same_connection_returns_409(
        self, mock_login_url, mock_exchange, mock_sync, _mock_refresh,
    ) -> None:
        """A double-click, a retried request, or two open tabs must not let
        two sync_account() calls for the same connection race each other —
        simulates "already running" the same way a real concurrent request
        would leave the lock held."""
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        mock_exchange.return_value = {"access_token": "token-1"}
        mock_sync.return_value = {"holdings_synced": 0, "trades_synced": 0}

        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "k", "s")
        client.post("/api/portfolio/broker/zerodha/connect", json={"account_id": acc, "request_token": "rt"})

        lock_name = f"broker_sync:{acc}:zerodha"
        self.assertTrue(rate_limiter.try_acquire_lock(lock_name, 300))
        try:
            resp = client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc})
            self.assertEqual(resp.status_code, 409)
            mock_sync.assert_not_called()
        finally:
            rate_limiter.release_lock(lock_name)

        # Lock released — a subsequent sync must succeed normally.
        resp = client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc})
        self.assertEqual(resp.status_code, 200, resp.text)
        mock_sync.assert_called_once()

    @patch("portfolio.kite_sync.sync_account")
    @patch("portfolio.kite_sync.exchange_request_token")
    @patch("portfolio.kite_sync.get_login_url")
    def test_sync_propagates_kite_error_as_422(self, mock_login_url, mock_exchange, mock_sync) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        mock_exchange.return_value = {"access_token": "token-1"}
        mock_sync.return_value = {"error": "session expired"}

        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "k", "s")
        client.post("/api/portfolio/broker/zerodha/connect",
                     json={"account_id": acc, "request_token": "rt"})

        resp = client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc})
        self.assertEqual(resp.status_code, 422)

    # ── the actual bug this redesign fixes: credentials are per-account,
    # not a shared global — two accounts connecting the same broker must
    # never cross-contaminate each other's api_key/api_secret/access_token.

    @patch("portfolio.kite_sync.sync_account")
    @patch("portfolio.kite_sync.exchange_request_token")
    @patch("portfolio.kite_sync.get_login_url")
    def test_two_accounts_use_independent_credentials_for_the_same_broker(
        self, mock_login_url, mock_exchange, mock_sync,
    ) -> None:
        mock_login_url.side_effect = lambda api_key: f"https://kite.trade/connect/login?api_key={api_key}"
        mock_exchange.side_effect = lambda api_key, api_secret, request_token: {
            "access_token": f"access-token-for-{api_key}",
        }
        mock_sync.return_value = {"holdings_synced": 0, "trades_synced": 0}

        pid = self._mk_profile()
        acc_a = self._mk_account(pid)
        acc_b = self._mk_account(pid)

        url_a = self._register_credentials("zerodha", acc_a, "key-A", "secret-A")
        url_b = self._register_credentials("zerodha", acc_b, "key-B", "secret-B")
        self.assertIn("key-A", url_a.json()["login_url"])
        self.assertIn("key-B", url_b.json()["login_url"])

        client.post("/api/portfolio/broker/zerodha/connect", json={"account_id": acc_a, "request_token": "rt-a"})
        client.post("/api/portfolio/broker/zerodha/connect", json={"account_id": acc_b, "request_token": "rt-b"})
        self.assertEqual(mock_exchange.call_args_list[0].args[0], "key-A")
        self.assertEqual(mock_exchange.call_args_list[1].args[0], "key-B")

        with self.engine.connect() as conn:
            rows = {
                r["account_id"]: r for r in conn.execute(select(broker_connections)).mappings().fetchall()
            }
        # Each account's decrypted access token really is the one minted
        # for its own api_key, not the other account's.
        from core.crypto import decrypt
        self.assertEqual(decrypt(rows[acc_a]["access_token_enc"]), "access-token-for-key-A")
        self.assertEqual(decrypt(rows[acc_b]["access_token_enc"]), "access-token-for-key-B")

        client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc_a})
        client.post("/api/portfolio/broker/zerodha/sync", json={"account_id": acc_b})
        self.assertEqual(mock_sync.call_args_list[0].kwargs.get("api_key"), "key-A")
        self.assertEqual(mock_sync.call_args_list[1].kwargs.get("api_key"), "key-B")

    # ── multi-broker dispatch (HDFC Securities, Paytm Money) ────────────────
    # Not a full repeat of every zerodha case above — just enough per broker
    # to prove _broker_sync_module() actually dispatches to the right sync
    # module with the credentials registered for that specific connection.

    @patch("portfolio.hdfc_sync.get_login_url")
    def test_login_url_success_hdfc_securities(self, mock_login_url) -> None:
        mock_login_url.return_value = "https://developer.hdfcsec.com/login?api_key=my-hdfc-key"
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = self._register_credentials("hdfc_securities", acc, "my-hdfc-key", "my-hdfc-secret")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hdfcsec.com", resp.json()["login_url"])
        mock_login_url.assert_called_once_with("my-hdfc-key")

    @patch("portfolio.hdfc_sync.exchange_request_token")
    @patch("portfolio.hdfc_sync.get_login_url")
    def test_connect_success_hdfc_securities(self, mock_login_url, mock_exchange) -> None:
        mock_login_url.return_value = "https://developer.hdfcsec.com/login?v=3"
        mock_exchange.return_value = {"access_token": "hdfc-access-token"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("hdfc_securities", acc, "my-hdfc-key", "my-hdfc-secret")

        resp = client.post("/api/portfolio/broker/hdfc_securities/connect", json={
            "account_id": acc, "request_token": "rt",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        mock_exchange.assert_called_once_with("my-hdfc-key", "my-hdfc-secret", "rt")

        with self.engine.connect() as conn:
            row = conn.execute(
                select(broker_connections).where(broker_connections.c.account_id == acc)
            ).mappings().first()
            self.assertEqual(row["broker"], "hdfc_securities")
            self.assertNotIn("hdfc-access-token", row["access_token_enc"])

    @patch("portfolio.paytm_sync.get_login_url")
    def test_login_url_success_paytm_money(self, mock_login_url) -> None:
        mock_login_url.return_value = "https://login.paytmmoney.com/merchant-login?apiKey=my-paytm-key"
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = self._register_credentials("paytm_money", acc, "my-paytm-key", "my-paytm-secret")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("paytmmoney.com", resp.json()["login_url"])
        mock_login_url.assert_called_once_with("my-paytm-key")

    @patch("portfolio.paytm_sync.exchange_request_token")
    @patch("portfolio.paytm_sync.get_login_url")
    def test_connect_success_paytm_money(self, mock_login_url, mock_exchange) -> None:
        mock_login_url.return_value = "https://login.paytmmoney.com/merchant-login?v=3"
        mock_exchange.return_value = {"access_token": "paytm-access-token"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("paytm_money", acc, "my-paytm-key", "my-paytm-secret")

        resp = client.post("/api/portfolio/broker/paytm_money/connect", json={
            "account_id": acc, "request_token": "rt",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        mock_exchange.assert_called_once_with("my-paytm-key", "my-paytm-secret", "rt")

    @patch("portfolio.portfolio_valuation.refresh_valuations")
    @patch("portfolio.paytm_sync.sync_account")
    @patch("portfolio.paytm_sync.exchange_request_token")
    @patch("portfolio.paytm_sync.get_login_url")
    def test_sync_success_paytm_money_dispatches_to_paytm_module(
        self, mock_login_url, mock_exchange, mock_sync, _mock_refresh,
    ) -> None:
        mock_login_url.return_value = "https://login.paytmmoney.com/merchant-login?v=3"
        mock_exchange.return_value = {"access_token": "token-1"}
        mock_sync.return_value = {"holdings_synced": 2, "trades_synced": 0}

        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("paytm_money", acc, "my-paytm-key", "my-paytm-secret")
        client.post("/api/portfolio/broker/paytm_money/connect",
                     json={"account_id": acc, "request_token": "rt"})

        resp = client.post("/api/portfolio/broker/paytm_money/sync", json={"account_id": acc})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"holdings_synced": 2, "trades_synced": 0})
        mock_sync.assert_called_once()
        self.assertEqual(mock_sync.call_args.kwargs.get("api_key"), "my-paytm-key")

    # ── connections list ─────────────────────────────────────────────────────

    @patch("portfolio.kite_sync.get_login_url")
    def test_list_connections_shows_registered_but_not_yet_connected(self, mock_login_url) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "k", "s")

        resp = client.get(f"/api/portfolio/broker/connections?profile_id={pid}")
        self.assertEqual(resp.status_code, 200)
        conns = resp.json()["connections"]
        self.assertEqual(len(conns), 1)
        self.assertFalse(conns[0]["connected"])

    @patch("portfolio.kite_sync.exchange_request_token")
    @patch("portfolio.kite_sync.get_login_url")
    def test_list_connections_never_exposes_credentials(self, mock_login_url, mock_exchange) -> None:
        mock_login_url.return_value = "https://kite.trade/connect/login?v=3"
        mock_exchange.return_value = {"access_token": "secret-token"}
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._register_credentials("zerodha", acc, "my-api-key", "my-api-secret")
        client.post("/api/portfolio/broker/zerodha/connect",
                     json={"account_id": acc, "request_token": "rt"})

        resp = client.get(f"/api/portfolio/broker/connections?profile_id={pid}")
        self.assertEqual(resp.status_code, 200)
        conns = resp.json()["connections"]
        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0]["broker"], "zerodha")
        self.assertTrue(conns[0]["connected"])
        self.assertNotIn("access_token_enc", conns[0])
        self.assertNotIn("api_key", conns[0])
        self.assertNotIn("api_secret_enc", conns[0])
        body_str = str(resp.json())
        self.assertNotIn("secret-token", body_str)
        self.assertNotIn("my-api-secret", body_str)


if __name__ == "__main__":
    unittest.main()
