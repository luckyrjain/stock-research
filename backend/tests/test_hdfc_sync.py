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
from portfolio.hdfc_sync import sync_account

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


_HOLDING = {
    "trading_symbol": "TCS",
    "exchange": "NSE",
    "quantity": 10,
    "average_price": 3500.0,
    "last_price": 3650.5,
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


class SyncAccountTest(unittest.TestCase):
    def setUp(self):
        self.engine = _sqlite_engine()
        self.account_id = _mk_connected_account(self.engine)

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

        updated_holding = {**_HOLDING, "quantity": 10, "last_price": 3700.0}
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
        mock_holdings.return_value = [{"exchange": "NSE"}]  # missing trading_symbol/quantity
        mock_trades.return_value = []

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["holdings_synced"], 0)
        self.assertEqual(result["holdings_skipped"], 1)

    @patch("portfolio.hdfc_sync._fetch_holdings")
    def test_fetch_failure_returns_error_dict_not_raise(self, mock_holdings):
        mock_holdings.side_effect = Exception("network blip")

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
