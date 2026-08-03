import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import IntegrityError
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
from portfolio.kite_sync import sync_account

_TABLES = [profiles, accounts, assets, holdings, valuations, transactions, broker_connections]


def _sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    metadata.create_all(engine, tables=_TABLES)
    return engine


def _mk_connected_account(engine) -> int:
    with engine.begin() as conn:
        pid = conn.execute(insert(profiles).values(name="p")).inserted_primary_key[0]
        aid = conn.execute(
            insert(accounts).values(profile_id=pid, name="Zerodha", type="broker")
        ).inserted_primary_key[0]
        conn.execute(
            insert(broker_connections).values(
                profile_id=pid, account_id=aid, broker="zerodha",
                access_token_enc="fake-enc", token_obtained_at=datetime.now(timezone.utc),
            )
        )
        return aid


_HOLDING = {
    "tradingsymbol": "TCS",
    "exchange": "NSE",
    "quantity": 10,
    "average_price": 3500.0,
    "last_price": 3650.5,
}

_HOLDING_INFY = {
    "tradingsymbol": "INFY",
    "exchange": "NSE",
    "quantity": 20,
    "average_price": 1400.0,
    "last_price": 1450.0,
}

_TRADE = {
    "trade_id": "T001",
    "order_id": "O001",
    "tradingsymbol": "TCS",
    "exchange": "NSE",
    "transaction_type": "BUY",
    "quantity": 5,
    "average_price": 3480.0,
    "order_timestamp": "2024-01-15T10:30:00",
}


class SyncAccountTest(unittest.TestCase):
    def setUp(self):
        self.engine = _sqlite_engine()
        self.account_id = _mk_connected_account(self.engine)

    def _mock_kite(self, holdings_data=None, trades_data=None):
        kite = MagicMock()
        kite.holdings.return_value = holdings_data if holdings_data is not None else [_HOLDING]
        kite.trades.return_value = trades_data if trades_data is not None else [_TRADE]
        return kite

    @patch("portfolio.kite_sync._get_kite_client")
    def test_syncs_holdings_and_trades(self, mock_get_client):
        mock_get_client.return_value = self._mock_kite()

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["holdings_synced"], 1)
        self.assertEqual(result["holdings_skipped"], 0)
        self.assertEqual(result["trades_synced"], 1)
        self.assertEqual(result["trades_duplicate"], 0)

        with self.engine.connect() as conn:
            asset = conn.execute(select(assets).where(assets.c.symbol == "TCS")).mappings().first()
            self.assertIsNotNone(asset)
            self.assertEqual(asset["meta"]["source"], "zerodha_api")

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

    @patch("portfolio.kite_sync._get_kite_client")
    def test_second_sync_does_not_duplicate_trade_or_holding(self, mock_get_client):
        mock_get_client.return_value = self._mock_kite()
        sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        # Same holding at a new price, same trade re-delivered by the API.
        updated_holding = {**_HOLDING, "quantity": 10, "last_price": 3700.0}
        mock_get_client.return_value = self._mock_kite(holdings_data=[updated_holding])

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["trades_duplicate"], 1)
        self.assertEqual(result["trades_synced"], 0)

        with self.engine.connect() as conn:
            asset = conn.execute(select(assets).where(assets.c.symbol == "TCS")).mappings().first()
            txns = conn.execute(
                select(transactions).where(transactions.c.asset_id == asset["id"])
            ).mappings().fetchall()
            self.assertEqual(len(txns), 1)  # not duplicated

            val = conn.execute(
                select(valuations).where(valuations.c.asset_id == asset["id"])
            ).mappings().first()
            self.assertEqual(float(val["value"]), 10 * 3700.0)  # same-day upsert took the new price

    @patch("portfolio.kite_sync._get_kite_client")
    def test_malformed_holding_is_skipped_not_fatal(self, mock_get_client):
        mock_get_client.return_value = self._mock_kite(
            holdings_data=[{"exchange": "NSE"}],  # missing tradingsymbol/quantity
            trades_data=[],
        )

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertEqual(result["holdings_synced"], 0)
        self.assertEqual(result["holdings_skipped"], 1)

    @patch("portfolio.kite_sync._get_kite_client")
    def test_fetch_failure_returns_error_dict_not_raise(self, mock_get_client):
        kite = MagicMock()
        kite.holdings.side_effect = Exception("network blip")
        mock_get_client.return_value = kite

        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        self.assertIn("error", result)

    # ── holdings reconciliation (archive on sell-out, un-archive on rebuy) ──

    @patch("portfolio.kite_sync._get_kite_client")
    def test_holding_sold_completely_is_archived_not_deleted(self, mock_get_client):
        mock_get_client.return_value = self._mock_kite(holdings_data=[_HOLDING, _HOLDING_INFY], trades_data=[])
        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")
        self.assertEqual(result["holdings_archived"], 0)

        # Next sync: TCS sold out entirely, INFY still held — a real,
        # non-empty holdings response missing just one symbol, distinct
        # from the "broker returned nothing at all" case below.
        mock_get_client.return_value = self._mock_kite(holdings_data=[_HOLDING_INFY], trades_data=[])
        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")
        self.assertEqual(result["holdings_archived"], 1)

        with self.engine.connect() as conn:
            tcs = conn.execute(select(assets).where(assets.c.symbol == "TCS")).mappings().first()
            self.assertIsNotNone(tcs, "asset must still exist — archived, never deleted")
            self.assertTrue(tcs["archived"])
            # Transaction history (XIRR input) must survive the archive.
            txns = conn.execute(
                select(transactions).where(transactions.c.asset_id == tcs["id"])
            ).mappings().fetchall()
            self.assertEqual(len(txns), 0)  # no trades were ever synced for this asset in this test

            infy = conn.execute(select(assets).where(assets.c.symbol == "INFY")).mappings().first()
            self.assertFalse(infy["archived"])  # still held — must be untouched

    @patch("portfolio.kite_sync._get_kite_client")
    def test_archived_holding_is_unarchived_when_bought_back(self, mock_get_client):
        mock_get_client.return_value = self._mock_kite(holdings_data=[_HOLDING, _HOLDING_INFY], trades_data=[])
        sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")
        mock_get_client.return_value = self._mock_kite(holdings_data=[_HOLDING_INFY], trades_data=[])
        sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")  # archives TCS

        # TCS reappears in a later sync (bought back).
        mock_get_client.return_value = self._mock_kite(holdings_data=[_HOLDING, _HOLDING_INFY], trades_data=[])
        sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        with self.engine.connect() as conn:
            asset = conn.execute(select(assets).where(assets.c.symbol == "TCS")).mappings().first()
            self.assertFalse(asset["archived"])

    @patch("portfolio.kite_sync._get_kite_client")
    def test_genuinely_empty_holdings_response_does_not_archive_existing_holdings(self, mock_get_client):
        """A broker API hiccup that legitimately returns an empty list for a
        reason that isn't "the user sold everything" must not silently
        archive the whole portfolio — worse than the stale-holding problem
        reconciliation exists to fix. An entirely empty response is
        deliberately indistinguishable from "sold literally everything," so
        this codebase's own "never invent/never guess" instinct picks the
        safe interpretation and skips reconciliation outright, unlike the
        "sold one of several, response still has content" test above."""
        mock_get_client.return_value = self._mock_kite(holdings_data=[_HOLDING], trades_data=[])
        sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        mock_get_client.return_value = self._mock_kite(holdings_data=[], trades_data=[])
        result = sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")
        self.assertEqual(result["holdings_archived"], 0)

        with self.engine.connect() as conn:
            asset = conn.execute(select(assets).where(assets.c.symbol == "TCS")).mappings().first()
            self.assertFalse(asset["archived"])

    @patch("portfolio.kite_sync._get_kite_client")
    def test_reconciliation_never_touches_a_different_brokers_asset(self, mock_get_client):
        """Two different broker connections on the same account must never
        archive each other's holdings just because they happen to share a
        symbol — reconciliation is scoped to meta.source."""
        with self.engine.begin() as conn:
            conn.execute(
                insert(assets).values(
                    account_id=self.account_id, type="stock", name="TCS", symbol="TCS",
                    meta={"source": "csv"}, archived=False,
                )
            )
        mock_get_client.return_value = self._mock_kite(holdings_data=[], trades_data=[])
        sync_account(self.engine, self.account_id, "fake-token", api_key="fake-key")

        with self.engine.connect() as conn:
            csv_asset = conn.execute(
                select(assets).where(assets.c.symbol == "TCS", assets.c.meta["source"].as_string() == "csv")
            ).mappings().first()
            self.assertFalse(csv_asset["archived"])

    # ── DB-level trade dedup guarantee (belt, not just the app-level pre-check) ──

    def test_duplicate_external_ref_rejected_by_db_constraint(self):
        """Proves transactions.uq_transactions_asset_external_ref actually
        enforces uniqueness at the database level — the real backstop under
        a race two concurrent syncs could otherwise slip past
        broker_sync_common.py's own Python-side pre-check."""
        with self.engine.begin() as conn:
            asset_id = conn.execute(
                insert(assets).values(
                    account_id=self.account_id, type="stock", name="TCS", symbol="TCS",
                    meta={"source": "zerodha_api"},
                )
            ).inserted_primary_key[0]
            conn.execute(
                insert(transactions).values(
                    asset_id=asset_id, date=date(2024, 1, 15), type="buy", amount=17400, units=5,
                    external_ref="zerodha_api:T001", meta={"source": "zerodha_api", "trade_id": "T001"},
                )
            )

        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.execute(
                    insert(transactions).values(
                        asset_id=asset_id, date=date(2024, 1, 15), type="buy", amount=17400, units=5,
                        external_ref="zerodha_api:T001", meta={"source": "zerodha_api", "trade_id": "T001"},
                    )
                )


if __name__ == "__main__":
    unittest.main()
