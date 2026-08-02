import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, insert

from db.models import metadata, securities
from tools.securities_master import (
    load_nse_main_board, fetch_bse_main_board,
    get_full_securities_master, resolve_symbol,
)


class LoadNseMainBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine, tables=[securities])

    def _insert(self, **row) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(securities).values(**row))

    def test_rows_with_company_name_are_returned(self) -> None:
        self._insert(symbol="TCS", isin="INE467B01029",
                     company_name="Tata Consultancy Services Limited",
                     series="EQ")
        out = load_nse_main_board(self.engine)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], {
            "symbol": "TCS", "name": "Tata Consultancy Services Limited",
            "isin": "INE467B01029", "exchange": "NSE", "series": "EQ",
        })

    def test_rows_without_company_name_are_excluded(self) -> None:
        self._insert(symbol="NOENRICH", isin=None, company_name=None, series=None)
        self.assertEqual(load_nse_main_board(self.engine), [])


def _bse_row(scrip_cd, scrip_id, name, isin, group):
    return {"SCRIP_CD": scrip_cd, "scrip_id": scrip_id, "Scrip_Name": name,
            "ISIN_NUMBER": isin, "GROUP": group}


def _resp(rows):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = rows
    return resp


class FetchBseMainBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cache_path = Path("output/_bse_main_master.json")
        if self.cache_path.exists():
            self.cache_path.unlink()

    def tearDown(self) -> None:
        if self.cache_path.exists():
            self.cache_path.unlink()

    @patch("tools.securities_master.requests.get")
    def test_merges_and_dedups_across_groups(self, mock_get: MagicMock) -> None:
        # 9 groups fetched (A,B,T,Z,X,XT,P,MT,TS). Group A and B each return a
        # row for scrip_cd "500001" (must dedup to one); group T contributes
        # "500002"; the remaining 6 groups return no rows.
        mock_get.side_effect = [
            _resp([_bse_row("500001", "ABC", "ABC Ltd", "INE000A01011", "A")]),
            _resp([_bse_row("500001", "ABC", "ABC Ltd", "INE000A01011", "B")]),
            _resp([_bse_row("500002", "XYZ", "XYZ Ltd", "INE000B01022", "T")]),
            _resp([]), _resp([]), _resp([]), _resp([]), _resp([]), _resp([]),
        ]
        out = fetch_bse_main_board(force=True)
        self.assertEqual(len(out), 2)
        symbols = {s["symbol"] for s in out}
        self.assertEqual(symbols, {"ABC", "XYZ"})

    @patch("tools.securities_master.requests.get")
    def test_one_failing_group_does_not_drop_others(self, mock_get: MagicMock) -> None:
        # First group's request raises; the other 8 still contribute rows.
        mock_get.side_effect = [
            Exception("boom"),
            _resp([_bse_row("500002", "XYZ", "XYZ Ltd", "INE000B01022", "B")]),
            _resp([]), _resp([]), _resp([]), _resp([]), _resp([]), _resp([]), _resp([]),
        ]
        out = fetch_bse_main_board(force=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "XYZ")


def _master_row(symbol, name, isin, exchange="NSE"):
    return {"symbol": symbol, "name": name, "isin": isin, "exchange": exchange, "series": "EQ"}


class GetFullSecuritiesMasterTest(unittest.TestCase):
    @patch("tools.securities_master.get_all_sme_stocks")
    @patch("tools.securities_master.fetch_bse_main_board")
    @patch("tools.securities_master.load_nse_main_board")
    def test_isin_collision_prefers_nse(self, mock_nse, mock_bse, mock_sme) -> None:
        mock_nse.return_value = [_master_row("BAJAJHFL", "Bajaj Housing Finance Limited", "INE377Y01014")]
        mock_bse.return_value = [_master_row("500001", "Bajaj Housing Finance Limited", "INE377Y01014", "BSE")]
        mock_sme.return_value = []
        out = get_full_securities_master(engine=None)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "BAJAJHFL")
        self.assertEqual(out[0]["exchange"], "NSE")

    @patch("tools.securities_master.get_all_sme_stocks")
    @patch("tools.securities_master.fetch_bse_main_board")
    @patch("tools.securities_master.load_nse_main_board")
    def test_nse_failure_does_not_crash_and_other_sources_still_contribute(
        self, mock_nse, mock_bse, mock_sme
    ) -> None:
        # NSE fails with DB error; BSE and SME should still contribute.
        mock_nse.side_effect = Exception("DB connection failed")
        mock_bse.return_value = [_master_row("RELIANCE", "Reliance Industries Limited", "INE002A01018", "BSE")]
        mock_sme.return_value = [_master_row("EMERGE1", "Emerge Stock 1", "INE999Z01001")]
        out = get_full_securities_master(engine=None)
        self.assertEqual(len(out), 2)
        symbols = {s["symbol"] for s in out}
        self.assertEqual(symbols, {"RELIANCE", "EMERGE1"})


class ResolveSymbolTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("tools.securities_master.get_full_securities_master")
        self.mock_master = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_master.return_value = [
            _master_row("BAJAJHFL", "Bajaj Housing Finance Limited", "INE377Y01014"),
            _master_row("OCCL", "AG Ventures Ltd", "INE501C01021", "BSE"),
            _master_row("CRSL", "Cressanda Railway Solutions Ltd", "INE716D01033", "BSE"),
        ]

    def test_isin_match_wins(self) -> None:
        out = resolve_symbol(None, "WRONGCODE", isin="INE377Y01014")
        self.assertEqual(out["symbol"], "BAJAJHFL")
        self.assertEqual(out["confidence"], "isin")

    def test_exact_code_match(self) -> None:
        out = resolve_symbol(None, "BAJAJHFL")
        self.assertEqual(out["symbol"], "BAJAJHFL")
        self.assertEqual(out["confidence"], "exact")

    def test_suffix_stripped_match(self) -> None:
        out = resolve_symbol(None, "BAJAJHFLEQ")
        self.assertEqual(out["symbol"], "BAJAJHFL")
        self.assertEqual(out["confidence"], "exact")

    def test_fuzzy_name_match_above_threshold(self) -> None:
        out = resolve_symbol(None, "ORICAREQ", company_name="AG Ventures Limited")
        self.assertEqual(out["symbol"], "OCCL")
        self.assertEqual(out["confidence"], "fuzzy")

    def test_fuzzy_match_is_case_insensitive(self) -> None:
        # Broker CSVs are typically ALL CAPS; master company names are Title
        # Case. token_set_ratio is case-sensitive by default, so this needs
        # the processor= param to normalize case before scoring.
        out = resolve_symbol(None, "CRESOCEQ",
                              company_name="CRESSANDA RAILWAY  SOLUTIONS LIMITED")
        self.assertEqual(out["symbol"], "CRSL")
        self.assertEqual(out["confidence"], "fuzzy")

    def test_unresolved_returns_no_symbol(self) -> None:
        out = resolve_symbol(None, "TOTALLYUNKNOWN", company_name="Nonexistent Company Ltd")
        self.assertIsNone(out["symbol"])
        self.assertIsNone(out["exchange"])
        self.assertEqual(out["confidence"], "unresolved")

    def test_unresolved_with_no_company_name(self) -> None:
        out = resolve_symbol(None, "TOTALLYUNKNOWN")
        self.assertEqual(out["confidence"], "unresolved")
        self.assertIsNone(out["candidate_name"])


if __name__ == "__main__":
    unittest.main()
