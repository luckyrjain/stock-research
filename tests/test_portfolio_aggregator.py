"""Endpoint tests for routes/portfolio_aggregator.py — same SQLite-in-memory
approach as the module's own reference design (StaticPool shares one
in-memory DB across the executor threads run_owned_db_call's endpoints run
their queries in), through the real `api.app` so rate limiting and the
shared run_owned_db_call wrapper are exercised exactly as in production.
"""
import json
import os
import unittest
import warnings
from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api
import rate_limiter
from db.models import (
    accounts, assets, holdings, metadata, mf_nav_daily, prices_daily, profiles,
    transactions, valuations,
)
from routes.portfolio_aggregator import compute_networth

client = TestClient(api.app)


def _silence_sqlite_date_adapter_warning() -> None:
    warnings.filterwarnings(
        "ignore", message="The default date adapter is deprecated", category=DeprecationWarning,
    )


class ComputeNetworthTest(unittest.TestCase):
    """Pure function — no DB needed."""

    def test_empty_rows_is_zero(self) -> None:
        self.assertEqual(compute_networth([]), {"total": 0.0, "by_type": {}, "by_account": []})

    def test_sums_across_types_and_accounts(self) -> None:
        rows = [
            {"asset_id": 1, "asset_name": "SBI Savings", "type": "cash", "account_id": 1, "account_name": "SBI", "value": 50000.0},
            {"asset_id": 2, "asset_name": "HDFC FD", "type": "fd", "account_id": 2, "account_name": "HDFC", "value": 100000.0},
        ]
        summary = compute_networth(rows)
        self.assertEqual(summary["total"], 150000.0)
        self.assertEqual(summary["by_type"], {"cash": 50000.0, "fd": 100000.0})
        self.assertEqual(len(summary["by_account"]), 2)

    def test_loan_is_subtracted_not_added(self) -> None:
        rows = [
            {"asset_id": 1, "asset_name": "Cash", "type": "cash", "account_id": 1, "account_name": "SBI", "value": 100000.0},
            {"asset_id": 2, "asset_name": "Home Loan", "type": "loan", "account_id": 1, "account_name": "SBI", "value": 40000.0},
        ]
        summary = compute_networth(rows)
        self.assertEqual(summary["total"], 60000.0)
        self.assertEqual(summary["by_type"]["loan"], -40000.0)

    def test_same_account_multiple_assets_aggregate_into_one_entry(self) -> None:
        rows = [
            {"asset_id": 1, "asset_name": "A", "type": "cash", "account_id": 1, "account_name": "SBI", "value": 100.0},
            {"asset_id": 2, "asset_name": "B", "type": "cash", "account_id": 1, "account_name": "SBI", "value": 200.0},
        ]
        summary = compute_networth(rows)
        self.assertEqual(len(summary["by_account"]), 1)
        self.assertEqual(summary["by_account"][0]["value"], 300.0)


class PortfolioAggregatorEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        _silence_sqlite_date_adapter_warning()
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
        )
        metadata.create_all(self.engine, tables=[
            profiles, accounts, assets, holdings, valuations, transactions,
            prices_daily, mf_nav_daily,
        ])
        self._old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "sqlite://"
        self._old_engine = api._DB_ENGINE
        api._DB_ENGINE = self.engine
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        api._DB_ENGINE = self._old_engine
        if self._old_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._old_db_url
        rate_limiter._memory_calls.clear()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _mk_profile(self, name: str = "me") -> int:
        res = client.post("/api/portfolio/profiles", json={"name": name})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["id"]

    def _mk_account(self, profile_id: int) -> int:
        res = client.post("/api/portfolio/accounts", json={
            "profile_id": profile_id, "name": "broker", "type": "broker",
        })
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["id"]

    def _mk_asset(self, account_id: int, **overrides) -> int:
        body = {"account_id": account_id, "type": "stock", "name": "TCS",
                "symbol": "TCS", "value": 1000.0, "units": 10}
        body.update(overrides)
        res = client.post("/api/portfolio/assets", json=body)
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["id"]

    def _valuation_rows(self, asset_id: int) -> list:
        with self.engine.connect() as conn:
            from sqlalchemy import text as _text
            return conn.execute(_text(
                "SELECT as_of, value FROM valuations WHERE asset_id = :a ORDER BY as_of"
            ), {"a": asset_id}).fetchall()

    # ── missing DB ───────────────────────────────────────────────────────────

    def test_missing_database_url_returns_503(self) -> None:
        os.environ.pop("DATABASE_URL", None)
        resp = client.get("/api/portfolio/profiles")
        self.assertEqual(resp.status_code, 503)

    # ── profiles ─────────────────────────────────────────────────────────────

    def test_create_and_list_profile(self) -> None:
        self._mk_profile("household")
        resp = client.get("/api/portfolio/profiles")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([p["name"] for p in resp.json()["profiles"]], ["household"])

    def test_duplicate_profile_name_409(self) -> None:
        self._mk_profile("dup")
        resp = client.post("/api/portfolio/profiles", json={"name": "dup"})
        self.assertEqual(resp.status_code, 409)

    # ── accounts ─────────────────────────────────────────────────────────────

    def test_bad_account_type_422(self) -> None:
        pid = self._mk_profile()
        resp = client.post("/api/portfolio/accounts", json={
            "profile_id": pid, "name": "x", "type": "wallet",
        })
        self.assertEqual(resp.status_code, 422)

    def test_account_for_missing_profile_404(self) -> None:
        resp = client.post("/api/portfolio/accounts", json={
            "profile_id": 999999, "name": "x", "type": "bank",
        })
        self.assertEqual(resp.status_code, 404)

    def test_delete_account_with_assets_422(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        self._mk_asset(acc)
        resp = client.delete(f"/api/portfolio/accounts/{acc}")
        self.assertEqual(resp.status_code, 422)

    def test_delete_empty_account_ok(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.delete(f"/api/portfolio/accounts/{acc}")
        self.assertEqual(resp.status_code, 200)

    def test_delete_missing_account_404(self) -> None:
        resp = client.delete("/api/portfolio/accounts/999999")
        self.assertEqual(resp.status_code, 404)

    # ── assets ───────────────────────────────────────────────────────────────

    def test_units_on_non_security_asset_create_422(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        resp = client.post("/api/portfolio/assets", json={
            "account_id": acc, "type": "cash", "name": "cash", "value": 100.0, "units": 5,
        })
        self.assertEqual(resp.status_code, 422)

    def test_units_on_non_security_asset_patch_422(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        aid = self._mk_asset(acc, type="cash", name="cash", symbol=None, units=None)
        resp = client.patch(f"/api/portfolio/assets/{aid}", json={"units": 5})
        self.assertEqual(resp.status_code, 422)

    def test_create_asset_creates_holding_row(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        aid = self._mk_asset(acc)
        resp = client.get(f"/api/portfolio/assets?account_id={acc}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["assets"][0]["units"], 10.0)
        with self.engine.connect() as conn:
            from sqlalchemy import text as _text
            row = conn.execute(_text("SELECT units FROM holdings WHERE asset_id = :a"), {"a": aid}).first()
        self.assertEqual(float(row[0]), 10.0)

    def test_delete_asset_cascades_valuation_and_holding(self) -> None:
        pid = self._mk_profile()
        aid = self._mk_asset(self._mk_account(pid))
        resp = client.delete(f"/api/portfolio/assets/{aid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._valuation_rows(aid), [])

    # ── valuations ───────────────────────────────────────────────────────────

    def test_valuation_upsert_same_day_overwrites_new_day_appends(self) -> None:
        pid = self._mk_profile()
        aid = self._mk_asset(self._mk_account(pid))  # creates today's valuation
        resp = client.post(f"/api/portfolio/assets/{aid}/valuations", json={"value": 2000.0})
        self.assertEqual(resp.status_code, 201)
        rows = self._valuation_rows(aid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0][1]), 2000.0)
        yesterday = str(date.today() - timedelta(days=1))
        resp = client.post(f"/api/portfolio/assets/{aid}/valuations",
                            json={"value": 1500.0, "as_of": yesterday})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(self._valuation_rows(aid)), 2)

    def test_future_dated_valuation_422(self) -> None:
        pid = self._mk_profile()
        aid = self._mk_asset(self._mk_account(pid))
        tomorrow = str(date.today() + timedelta(days=1))
        resp = client.post(f"/api/portfolio/assets/{aid}/valuations",
                            json={"value": 1.0, "as_of": tomorrow})
        self.assertEqual(resp.status_code, 422)

    def test_valuation_for_missing_asset_404(self) -> None:
        resp = client.post("/api/portfolio/assets/999999/valuations", json={"value": 1.0})
        self.assertEqual(resp.status_code, 404)

    # ── net worth ────────────────────────────────────────────────────────────

    def test_networth_sums_assets_across_accounts(self) -> None:
        pid = self._mk_profile()
        acc1 = self._mk_account(pid)
        acc2 = self._mk_account(pid)
        self._mk_asset(acc1, type="cash", name="cash", symbol=None, units=None, value=100000.0)
        self._mk_asset(acc2, type="loan", name="loan", symbol=None, units=None, value=40000.0)
        resp = client.get(f"/api/portfolio/networth?profile_id={pid}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 60000.0)
        self.assertEqual(len(body["by_account"]), 2)

    def test_networth_excludes_archived_assets(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        aid = self._mk_asset(acc, type="cash", name="cash", symbol=None, units=None, value=500.0)
        client.patch(f"/api/portfolio/assets/{aid}", json={"archived": True})
        resp = client.get(f"/api/portfolio/networth?profile_id={pid}")
        self.assertEqual(resp.json()["total"], 0.0)

    # ── valuation refresh / xirr ─────────────────────────────────────────────

    def test_refresh_valuations_endpoint_values_priced_stock(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        aid = self._mk_asset(acc, symbol="TCS", units=10, value=1.0)
        with self.engine.begin() as conn:
            from sqlalchemy import insert as _insert
            conn.execute(_insert(prices_daily).values(
                symbol="TCS", trade_date=date.today(), close=100.0, adj_close=100.0,
            ))
        resp = client.post("/api/portfolio/refresh-valuations")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["valued"], 1)
        self.assertEqual(body["skipped"], 0)
        rows = self._valuation_rows(aid)
        self.assertEqual(float(rows[-1][1]), 1000.0)

    def test_xirr_endpoint_null_without_transactions(self) -> None:
        pid = self._mk_profile()
        self._mk_asset(self._mk_account(pid))
        resp = client.get(f"/api/portfolio/xirr?profile_id={pid}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["portfolio_xirr"])
        self.assertEqual(len(body["assets"]), 1)
        self.assertIsNone(body["assets"][0]["xirr"])

    # ── CAS import ───────────────────────────────────────────────────────────

    def test_import_cas_endpoint_422_on_parse_error(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        with patch("cas_import.parse_cas", return_value={"error": "Incorrect PDF password."}):
            resp = client.post(
                "/api/portfolio/import-cas",
                files={"file": ("cas.pdf", b"fake", "application/pdf")},
                data={"password": "wrong", "account_id": acc},
            )
        self.assertEqual(resp.status_code, 422)

    def test_import_cas_endpoint_404_on_unknown_account(self) -> None:
        with patch("cas_import.parse_cas", return_value={"cas_type": "DETAILED", "folios": []}):
            resp = client.post(
                "/api/portfolio/import-cas",
                files={"file": ("cas.pdf", b"fake", "application/pdf")},
                data={"password": "pw", "account_id": 9999},
            )
        self.assertEqual(resp.status_code, 404)

    def test_import_cas_endpoint_success_refreshes_and_returns_summary(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        parsed = {"cas_type": "DETAILED", "folios": [{"folio": "F1", "schemes": [{
            "amfi": "INF090I01239", "isin": "INF090I01239", "close": 50.0,
            "scheme": "Test Fund", "rta": "CAMS", "transactions": [],
        }]}]}
        with patch("cas_import.parse_cas", return_value=parsed), \
             patch("cas_import.archive_parsed", return_value="output/_cas/fake.json"):
            resp = client.post(
                "/api/portfolio/import-cas",
                files={"file": ("cas.pdf", b"fake", "application/pdf")},
                data={"password": "pw", "account_id": acc},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["assets_created"], 1)

    # ── CSV import ───────────────────────────────────────────────────────────

    def test_import_csv_preview_returns_headers_and_suggestion(self) -> None:
        content = b"symbol,side,qty,price\nTCS,buy,10,100\n"
        resp = client.post("/api/portfolio/import-csv/preview",
                           files={"file": ("trades.csv", content, "text/csv")})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["headers"], ["symbol", "side", "qty", "price"])
        self.assertEqual(len(body["sample_rows"]), 1)

    def test_import_csv_preview_422_on_empty_file(self) -> None:
        resp = client.post("/api/portfolio/import-csv/preview",
                           files={"file": ("trades.csv", b"", "text/csv")})
        self.assertEqual(resp.status_code, 422)

    def test_import_csv_endpoint_422_on_missing_required_mapping_field(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        content = b"symbol,side,qty,price\nTCS,buy,10,100\n"
        mapping = {"date": None, "symbol": "symbol", "side": "side",
                  "quantity": "qty", "price": "price"}
        resp = client.post(
            "/api/portfolio/import-csv",
            files={"file": ("trades.csv", content, "text/csv")},
            data={"mapping": json.dumps(mapping), "account_id": acc, "broker": "zerodha"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_import_csv_endpoint_404_on_unknown_account(self) -> None:
        content = b"date,symbol,side,qty,price\n2024-01-01,TCS,buy,10,100\n"
        mapping = {"date": "date", "symbol": "symbol", "side": "side",
                  "quantity": "qty", "price": "price"}
        resp = client.post(
            "/api/portfolio/import-csv",
            files={"file": ("trades.csv", content, "text/csv")},
            data={"mapping": json.dumps(mapping), "account_id": 9999, "broker": "zerodha"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_import_csv_endpoint_success_creates_asset_and_transaction(self) -> None:
        pid = self._mk_profile()
        acc = self._mk_account(pid)
        content = b"date,symbol,side,qty,price\n2024-01-01,TCS,buy,10,100\n"
        mapping = {"date": "date", "symbol": "symbol", "side": "side",
                  "quantity": "qty", "price": "price"}
        with patch("csv_import.resolve_symbol", return_value={
            "symbol": None, "exchange": None, "confidence": "unresolved", "candidate_name": None,
        }), patch("csv_import.get_full_securities_master", return_value=[]):
            resp = client.post(
                "/api/portfolio/import-csv",
                files={"file": ("trades.csv", content, "text/csv")},
                data={"mapping": json.dumps(mapping), "account_id": acc, "broker": "zerodha"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["imported"], 1)
        self.assertEqual(body["assets_created"], 1)


if __name__ == "__main__":
    unittest.main()
