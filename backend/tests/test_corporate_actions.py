import unittest
from datetime import date
from unittest.mock import MagicMock

from corporate_actions_pipeline import _missed_factor_suspects
from tools.corporate_actions import (
    fetch_corporate_actions, parse_corporate_actions, parse_purpose,
)


class ParsePurposeTest(unittest.TestCase):
    def test_bonus_variants(self) -> None:
        self.assertEqual(parse_purpose("Bonus 1:1"),
                         {"type": "bonus", "price_factor": 0.5, "amount": None})
        self.assertEqual(parse_purpose("BONUS 3:2"),
                         {"type": "bonus", "price_factor": 0.4, "amount": None})

    def test_bonus_without_ratio_never_guesses(self) -> None:
        out = parse_purpose("Bonus Issue Of Equity Shares")
        self.assertEqual(out["type"], "other")
        self.assertIsNone(out["price_factor"])

    def test_split_variants(self) -> None:
        out = parse_purpose("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share")
        self.assertEqual(out["type"], "split")
        self.assertAlmostEqual(out["price_factor"], 0.1)
        out = parse_purpose("Face Value Split From Rs 2 To Re 1")
        self.assertEqual(out["type"], "split")
        self.assertAlmostEqual(out["price_factor"], 0.5)

    def test_split_without_parseable_ratio_never_guesses(self) -> None:
        out = parse_purpose("Face Value Split Of Equity Shares")
        self.assertEqual(out["type"], "other")
        self.assertIsNone(out["price_factor"])

    def test_dividend_variants(self) -> None:
        out = parse_purpose("Interim Dividend - Rs 5.50 Per Share")
        self.assertEqual(out, {"type": "dividend", "price_factor": None, "amount": 5.5})
        out = parse_purpose("Final Dividend Re 0.90")
        self.assertEqual(out, {"type": "dividend", "price_factor": None, "amount": 0.9})

    def test_dividend_without_amount(self) -> None:
        out = parse_purpose("Dividend")
        self.assertEqual(out["type"], "dividend")
        self.assertIsNone(out["amount"])

    def test_rights_and_buyback(self) -> None:
        self.assertEqual(parse_purpose("Rights 1:4 @ Premium Rs 90")["type"], "rights")
        self.assertEqual(parse_purpose("Buyback of Equity Shares")["type"], "buyback")

    def test_junk_is_other(self) -> None:
        out = parse_purpose("Annual General Meeting")
        self.assertEqual(out, {"type": "other", "price_factor": None, "amount": None})
        self.assertEqual(parse_purpose("")["type"], "other")


class ParseCorporateActionsTest(unittest.TestCase):
    RAW = [
        {"symbol": "TCS", "subject": "Bonus 1:1", "exDate": "03-Jul-2026",
         "recDate": "04-Jul-2026", "series": "EQ"},
        {"symbol": "SBIN", "subject": "Interim Dividend - Rs 5.50 Per Share",
         "exDate": "02-Jul-2026", "recDate": "-", "series": "EQ"},
        {"symbol": "", "subject": "Bonus 1:1", "exDate": "03-Jul-2026"},      # no symbol
        {"symbol": "BADDATE", "subject": "Bonus 1:1", "exDate": "garbage"},   # bad date
    ]

    def test_rows_parsed_and_bad_rows_skipped(self) -> None:
        rows = parse_corporate_actions(self.RAW)
        self.assertEqual(len(rows), 2)
        tcs = next(r for r in rows if r["symbol"] == "TCS")
        self.assertEqual(tcs["ex_date"], date(2026, 7, 3))
        self.assertEqual(tcs["type"], "bonus")
        self.assertEqual(tcs["price_factor"], 0.5)
        self.assertEqual(tcs["record_date"], date(2026, 7, 4))
        sbin = next(r for r in rows if r["symbol"] == "SBIN")
        self.assertEqual(sbin["type"], "dividend")
        self.assertEqual(sbin["amount"], 5.5)
        self.assertIsNone(sbin["record_date"])


class MissedFactorSuspectsTest(unittest.TestCase):
    def test_unparsed_bonus_is_suspect(self) -> None:
        rows = [{"type": "other", "purpose_raw": "Bonus Issue Of Equity Shares"}]
        self.assertEqual(_missed_factor_suspects(rows), ["Bonus Issue Of Equity Shares"])

    def test_agm_is_not_suspect(self) -> None:
        rows = [{"type": "other", "purpose_raw": "Annual General Meeting"}]
        self.assertEqual(_missed_factor_suspects(rows), [])


class FetchCorporateActionsTest(unittest.TestCase):
    def test_ok_list_payload(self) -> None:
        session = MagicMock()
        session.get.return_value.status_code = 200
        session.get.return_value.text = "[]"
        session.get.return_value.json.return_value = [{"symbol": "TCS"}]
        out = fetch_corporate_actions(date(2026, 6, 1), date(2026, 7, 1), session)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["raw"], [{"symbol": "TCS"}])

    def test_html_body_is_error(self) -> None:
        session = MagicMock()
        session.get.return_value.status_code = 200
        session.get.return_value.text = "<html>blocked</html>"
        out = fetch_corporate_actions(date(2026, 6, 1), date(2026, 7, 1), session)
        self.assertEqual(out["status"], "error")
        self.assertIn("bot-block", out["error"])

    def test_http_error(self) -> None:
        session = MagicMock()
        session.get.return_value.status_code = 503
        session.get.return_value.text = ""
        out = fetch_corporate_actions(date(2026, 6, 1), date(2026, 7, 1), session)
        self.assertEqual(out["status"], "error")


if __name__ == "__main__":
    unittest.main()
