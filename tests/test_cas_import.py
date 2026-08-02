import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from cas_import import _scrub, import_cas, parse_cas
from db.models import accounts, assets, holdings, metadata, profiles, transactions


def _sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    metadata.create_all(engine, tables=[profiles, accounts, assets, holdings, transactions])
    return engine


def _mk_account(engine) -> int:
    from sqlalchemy import insert
    with engine.begin() as conn:
        pid = conn.execute(insert(profiles).values(name="p")).inserted_primary_key[0]
        return conn.execute(insert(accounts).values(profile_id=pid, name="a", type="broker")).inserted_primary_key[0]


def _scheme(amfi="INF090I01239", isin="INF090I01239", close=100.0, txns=None, scheme_name="Test Fund"):
    return {"amfi": amfi, "isin": isin, "close": close, "scheme": scheme_name,
            "rta": "CAMS", "transactions": txns or []}


def _txn(date_str, ttype, amount=1000.0, units=10.0):
    return {"date": date_str, "type": ttype, "amount": amount, "units": units}


class ParseCasTest(unittest.TestCase):
    def test_wrong_password_returns_error(self) -> None:
        import casparser
        from casparser.exceptions import IncorrectPasswordError
        with patch.object(casparser, "read_cas_pdf", side_effect=IncorrectPasswordError()):
            result = parse_cas(b"fake pdf bytes", "wrong")
        self.assertIn("Incorrect PDF password", result["error"])

    def test_unreadable_pdf_returns_error(self) -> None:
        import casparser
        with patch.object(casparser, "read_cas_pdf", side_effect=ValueError("boom")):
            result = parse_cas(b"not a pdf", "pw")
        self.assertIn("Unreadable PDF", result["error"])

    def test_summary_statement_rejected(self) -> None:
        import casparser
        import json
        with patch.object(casparser, "read_cas_pdf", return_value=json.dumps({"cas_type": "SUMMARY"})):
            result = parse_cas(b"fake", "pw")
        self.assertIn("detailed", result["error"])

    def test_detailed_statement_returned(self) -> None:
        import casparser
        import json
        parsed = {"cas_type": "DETAILED", "folios": []}
        with patch.object(casparser, "read_cas_pdf", return_value=json.dumps(parsed)):
            result = parse_cas(b"fake", "pw")
        self.assertEqual(result, parsed)


class ScrubTest(unittest.TestCase):
    def test_removes_investor_info_and_folio_pii(self) -> None:
        parsed = {
            "investor_info": {"name": "John Doe", "email": "j@example.com"},
            "folios": [{"folio": "123", "PAN": "ABCDE1234F", "KYC": "OK", "schemes": []}],
        }
        clean = _scrub(parsed)
        self.assertEqual(clean["investor_info"], {})
        self.assertNotIn("PAN", clean["folios"][0])
        self.assertNotIn("KYC", clean["folios"][0])
        self.assertEqual(clean["folios"][0]["folio"], "123")


class ImportCasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _sqlite_engine()
        self.account_id = _mk_account(self.engine)

    def test_unknown_account_returns_error(self) -> None:
        parsed = {"folios": [{"folio": "1", "schemes": [_scheme()]}]}
        result = import_cas(self.engine, parsed, 9999)
        self.assertEqual(result, {"error": "account not found"})

    def test_creates_new_mf_asset_with_holdings_and_transactions(self) -> None:
        parsed = {"folios": [{"folio": "F1", "schemes": [
            _scheme(txns=[_txn("2024-01-01", "PURCHASE")]),
        ]}]}
        result = import_cas(self.engine, parsed, self.account_id)
        self.assertEqual(result["assets_created"], 1)
        self.assertEqual(result["transactions"], 1)
        with self.engine.connect() as conn:
            asset = conn.execute(select(assets)).mappings().first()
            hold = conn.execute(select(holdings)).mappings().first()
        self.assertEqual(asset["type"], "mf")
        self.assertEqual(asset["symbol"], "INF090I01239")
        self.assertFalse(asset["archived"])
        self.assertEqual(float(hold["units"]), 100.0)

    def test_closed_folio_with_history_archived_but_no_holdings(self) -> None:
        parsed = {"folios": [{"folio": "F1", "schemes": [
            _scheme(close=0.0, txns=[_txn("2024-01-01", "PURCHASE"), _txn("2024-06-01", "REDEMPTION")]),
        ]}]}
        result = import_cas(self.engine, parsed, self.account_id)
        self.assertEqual(result["assets_created"], 1)
        with self.engine.connect() as conn:
            asset = conn.execute(select(assets)).mappings().first()
            hold_count = conn.execute(select(holdings)).mappings().fetchall()
        self.assertTrue(asset["archived"])
        self.assertEqual(hold_count, [])

    def test_closed_folio_with_no_transactions_skipped_entirely(self) -> None:
        parsed = {"folios": [{"folio": "F1", "schemes": [_scheme(close=0.0, txns=[])]}]}
        result = import_cas(self.engine, parsed, self.account_id)
        self.assertEqual(result["assets_created"], 0)
        self.assertTrue(any("closed scheme" in w for w in result["warnings"]))

    def test_scheme_without_amfi_or_isin_skipped(self) -> None:
        parsed = {"folios": [{"folio": "F1", "schemes": [
            {"amfi": None, "isin": None, "close": 100.0, "scheme": "X", "transactions": []},
        ]}]}
        result = import_cas(self.engine, parsed, self.account_id)
        self.assertEqual(result["assets_created"], 0)
        self.assertTrue(any("without AMFI code or ISIN" in w for w in result["warnings"]))

    def test_transaction_type_mapping_and_skip_rows(self) -> None:
        parsed = {"folios": [{"folio": "F1", "schemes": [_scheme(txns=[
            _txn("2024-01-01", "PURCHASE"),
            _txn("2024-02-01", "REDEMPTION"),
            _txn("2024-03-01", "DIVIDEND_PAYOUT"),
            _txn("2024-04-01", "DIVIDEND_REINVEST"),
            _txn("2024-05-01", "STT_TAX"),
            _txn("2024-06-01", "SOME_UNKNOWN_TYPE"),
        ])]}]}
        result = import_cas(self.engine, parsed, self.account_id)
        self.assertEqual(result["transactions"], 4)  # buy, sell, dividend, dividend_reinvest
        self.assertEqual(result["skipped_rows"], 2)   # STT_TAX + unknown
        with self.engine.connect() as conn:
            types = [r[0] for r in conn.execute(select(transactions.c.type))]
        self.assertEqual(sorted(types), ["buy", "dividend", "dividend_reinvest", "sell"])

    def test_row_with_no_amount_is_skipped_not_inserted_as_null(self) -> None:
        # Regression test: transactions.amount is NOT NULL — a CAS row
        # missing amount previously reached the insert with amount=None,
        # raising IntegrityError instead of being skipped like any other
        # unwritable row.
        parsed = {"folios": [{"folio": "F1", "schemes": [_scheme(txns=[
            {"date": "2024-01-01", "type": "PURCHASE", "amount": None, "units": 10.0},
            _txn("2024-02-01", "PURCHASE"),
        ])]}]}
        result = import_cas(self.engine, parsed, self.account_id)
        self.assertEqual(result["transactions"], 1)
        self.assertEqual(result["skipped_rows"], 1)

    def test_matches_existing_asset_by_amfi_and_backfills_isin(self) -> None:
        from sqlalchemy import insert
        with self.engine.begin() as conn:
            aid = conn.execute(insert(assets).values(
                account_id=self.account_id, type="mf", name="Existing Fund",
                symbol="INF090I01239", meta={},
            )).inserted_primary_key[0]
        parsed = {"folios": [{"folio": "F1", "schemes": [_scheme(txns=[_txn("2024-01-01", "PURCHASE")])]}]}
        result = import_cas(self.engine, parsed, self.account_id)
        self.assertEqual(result["assets_matched"], 1)
        self.assertEqual(result["assets_created"], 0)
        with self.engine.connect() as conn:
            meta = conn.execute(select(assets.c.meta).where(assets.c.id == aid)).scalar()
        self.assertEqual(meta["isin"], "INF090I01239")

    def test_reimport_replaces_cas_rows_but_keeps_manual_rows(self) -> None:
        parsed = {"folios": [{"folio": "F1", "schemes": [
            _scheme(txns=[_txn("2024-01-01", "PURCHASE", amount=1000, units=10)]),
        ]}]}
        import_cas(self.engine, parsed, self.account_id)
        with self.engine.connect() as conn:
            asset_id = conn.execute(select(assets.c.id)).scalar()
        from datetime import date
        from sqlalchemy import insert
        with self.engine.begin() as conn:
            conn.execute(insert(transactions).values(
                asset_id=asset_id, date=date(2024, 1, 1), type="buy",
                amount=1.0, units=1.0, meta={"source": "manual"},
            ))
        # Re-import with a different transaction set for the same scheme.
        parsed2 = {"folios": [{"folio": "F1", "schemes": [
            _scheme(txns=[_txn("2024-02-01", "PURCHASE", amount=2000, units=20)]),
        ]}]}
        import_cas(self.engine, parsed2, self.account_id)
        with self.engine.connect() as conn:
            rows = conn.execute(select(transactions.c.date, transactions.c.meta)).mappings().fetchall()
        sources = sorted(r["meta"]["source"] for r in rows)
        self.assertEqual(sources, ["cas", "manual"])
        cas_row = next(r for r in rows if r["meta"]["source"] == "cas")
        self.assertEqual(str(cas_row["date"]), "2024-02-01")


if __name__ == "__main__":
    unittest.main()
