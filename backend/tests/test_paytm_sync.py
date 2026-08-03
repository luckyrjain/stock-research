import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

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
from portfolio.paytm_sync import sync_account

_TABLES = [profiles, accounts, assets, holdings, valuations, transactions, broker_connections]


def _sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    metadata.create_all(engine, tables=_TABLES)
    return engine


def _mk_connected_account(engine) -> int:
    with engine.begin() as conn:
        pid = conn.execute(insert(profiles).values(name="p")).inserted_primary_key[0]
        aid = conn.execute(
            insert(accounts).values(profile_id=pid, name="Paytm Money", type="broker")
        ).inserted_primary_key[0]
        conn.execute(
            insert(broker_connections).values(
                profile_id=pid, account_id=aid, broker="paytm_money",
                access_token_enc="fake-enc", token_obtained_at=datetime.now(timezone.utc),
            )
        )
        return aid


_HOLDING = {
    "tradingSymbol": "TCS",
    "exchange": "NSE",
    "quantity": 10,
    "averagePrice": 3500.0,
    "lastTradedPrice": 3650.5,
}

_FILLED_ORDER = {
    "orderNo": "O001",
    "tradingSymbol": "TCS",
    "exchange": "NSE",
    "transactionType": "BUY",
    "status": "EXECUTED",
    "quantity": 5,
    "avgTradedPrice": 3480.0,
    "orderTimestamp": "2024-01-15T10:30:00",
}

_PENDING_ORDER = {**_FILLED_ORDER, "orderNo": "O002", "status": "PENDING"}


class SyncAccountTest(unittest.TestCase):
    def setUp(self):
        self.engine = _sqlite_engine()
        self.account_id = _mk_connected_account(self.engine)

    @patch("portfolio.paytm_sync._fetch_orders")
    @patch("portfolio.paytm_sync._fetch_holdings")
    def test_syncs_holdings_and_filled_orders(self, mock_holdings, mock_orders):
        mock_holdings.return_value = [_HOLDING]
        mock_orders.return_value = [_FILLED_ORDER]

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["holdings_synced"], 1)
        self.assertEqual(result["holdings_skipped"], 0)
        self.assertEqual(result["trades_synced"], 1)
        self.assertEqual(result["trades_duplicate"], 0)

        with self.engine.connect() as conn:
            asset = conn.execute(select(assets).where(assets.c.symbol == "TCS")).mappings().first()
            self.assertIsNotNone(asset)
            self.assertEqual(asset["meta"]["source"], "paytm_money_api")

            holding = conn.execute(
                select(holdings).where(holdings.c.asset_id == asset["id"])
            ).mappings().first()
            self.assertEqual(float(holding["units"]), 10.0)

            txn = conn.execute(
                select(transactions).where(transactions.c.asset_id == asset["id"])
            ).mappings().first()
            self.assertEqual(txn["type"], "buy")
            self.assertEqual(txn["meta"]["trade_id"], "O001")
            self.assertEqual(txn["date"], date(2024, 1, 15))

    @patch("portfolio.paytm_sync._fetch_orders")
    @patch("portfolio.paytm_sync._fetch_holdings")
    def test_unfilled_order_is_excluded_not_counted_skipped(self, mock_holdings, mock_orders):
        mock_holdings.return_value = []
        mock_orders.return_value = [_PENDING_ORDER]

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        # A pending order is neither a synced trade nor a malformed/skipped
        # one — it's silently excluded, the expected common case for most
        # rows in an order book.
        self.assertEqual(result["trades_synced"], 0)
        self.assertEqual(result["trades_skipped"], 0)

    @patch("portfolio.paytm_sync._fetch_orders")
    @patch("portfolio.paytm_sync._fetch_holdings")
    def test_malformed_holding_is_skipped_not_fatal(self, mock_holdings, mock_orders):
        mock_holdings.return_value = [{"exchange": "NSE"}]  # missing tradingSymbol/quantity
        mock_orders.return_value = []

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["holdings_synced"], 0)
        self.assertEqual(result["holdings_skipped"], 1)

    @patch("portfolio.paytm_sync._fetch_holdings")
    @patch("portfolio.broker_sync_common.time.sleep")  # opaque exception → call_with_backoff retries; skip the real sleeps
    def test_fetch_failure_returns_error_dict_not_raise(self, _mock_sleep, mock_holdings):
        mock_holdings.side_effect = Exception("network blip")

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
