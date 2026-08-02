import unittest
import warnings
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine, insert, text

from db.models import (
    accounts, assets, holdings, metadata, mf_nav_daily, prices_daily,
    profiles, transactions, valuations,
)
from portfolio.portfolio_valuation import refresh_valuations, xirr


def _silence_sqlite_date_adapter_warning() -> None:
    # Python 3.13 deprecates sqlite3's default date adapter; SQLAlchemy's SQLite
    # dialect still uses it. Test-only noise — production runs PostgreSQL.
    warnings.filterwarnings(
        "ignore",
        message="The default date adapter is deprecated",
        category=DeprecationWarning,
    )


class XirrTest(unittest.TestCase):
    def test_single_period_ten_percent(self) -> None:
        flows = [(date(2025, 1, 1), -10_000.0), (date(2026, 1, 1), 11_000.0)]
        rate = xirr(flows)
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(rate, 0.10, places=3)

    def test_multi_flow(self) -> None:
        # Two 10k investments, final value 23k after growth.
        flows = [
            (date(2024, 1, 1), -10_000.0),
            (date(2025, 1, 1), -10_000.0),
            (date(2026, 1, 1), 23_000.0),
        ]
        rate = xirr(flows)
        self.assertIsNotNone(rate)
        # NPV at the returned rate must be ~0.
        npv = sum(
            cf / (1 + rate) ** ((d - date(2024, 1, 1)).days / 365.0)
            for d, cf in flows
        )
        self.assertAlmostEqual(npv, 0.0, places=2)
        self.assertGreater(rate, 0.0)

    def test_negative_return(self) -> None:
        flows = [(date(2025, 1, 1), -10_000.0), (date(2026, 1, 1), 8_000.0)]
        rate = xirr(flows)
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(rate, -0.20, places=3)

    def test_none_for_empty_and_single_flow(self) -> None:
        self.assertIsNone(xirr([]))
        self.assertIsNone(xirr([(date(2025, 1, 1), -10_000.0)]))

    def test_none_for_all_same_sign(self) -> None:
        self.assertIsNone(xirr([(date(2025, 1, 1), -1.0), (date(2026, 1, 1), -2.0)]))
        self.assertIsNone(xirr([(date(2025, 1, 1), 1.0), (date(2026, 1, 1), 2.0)]))


class RefreshValuationsTest(unittest.TestCase):
    def setUp(self) -> None:
        _silence_sqlite_date_adapter_warning()
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine, tables=[
            profiles, accounts, assets, holdings, valuations, transactions,
            prices_daily, mf_nav_daily,
        ])
        with self.engine.begin() as conn:
            pid = conn.execute(insert(profiles).values(name="p").returning(profiles.c.id)).scalar()
            self.account_id = conn.execute(
                insert(accounts).values(profile_id=pid, name="broker", type="broker")
                .returning(accounts.c.id)
            ).scalar()

    def _add_asset(self, type_: str, symbol: str | None, units: float) -> int:
        with self.engine.begin() as conn:
            aid = conn.execute(
                insert(assets).values(
                    account_id=self.account_id, type=type_, name=f"{type_}-{symbol}",
                    symbol=symbol,
                ).returning(assets.c.id)
            ).scalar()
            conn.execute(insert(holdings).values(asset_id=aid, units=units))
        return aid

    def _add_price(self, symbol: str, d: date, close: float) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(prices_daily).values(
                symbol=symbol, trade_date=d, close=close, adj_close=close,
            ))

    def _valuation_rows(self, asset_id: int) -> list[tuple]:
        with self.engine.connect() as conn:
            return conn.execute(text(
                "SELECT as_of, value FROM valuations WHERE asset_id = :a ORDER BY as_of"
            ), {"a": asset_id}).fetchall()

    def test_stock_valued_from_prices_daily(self) -> None:
        aid = self._add_asset("stock", "TCS", units=10)
        self._add_price("TCS", date(2026, 7, 2), 100.0)
        self._add_price("TCS", date(2026, 7, 3), 110.0)   # latest close wins
        result = refresh_valuations(self.engine)
        self.assertEqual(result["valued"], 1)
        self.assertEqual(result["skipped"], 0)
        rows = self._valuation_rows(aid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0][1]), 1100.0)

    def test_mf_valued_from_nav_daily(self) -> None:
        aid = self._add_asset("mf", "120503", units=250.5)
        with self.engine.begin() as conn:
            conn.execute(insert(mf_nav_daily).values(
                scheme_code="120503", nav_date=date(2026, 7, 3), nav=80.0,
            ))
        result = refresh_valuations(self.engine)
        self.assertEqual(result["valued"], 1)
        rows = self._valuation_rows(aid)
        self.assertEqual(float(rows[0][1]), 20040.0)

    def test_missing_price_skipped_prior_valuation_intact(self) -> None:
        aid = self._add_asset("stock", "NOPRICE", units=5)
        with self.engine.begin() as conn:
            conn.execute(insert(valuations).values(
                asset_id=aid, as_of=date(2026, 7, 1), value=500.0,
            ))
        with patch("portfolio.portfolio_valuation._yfinance_price", return_value=None):
            result = refresh_valuations(self.engine)
        self.assertEqual(result["valued"], 0)
        self.assertEqual(result["skipped"], 1)
        rows = self._valuation_rows(aid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0][1]), 500.0)

    def test_yfinance_fallback_used_on_prices_miss(self) -> None:
        aid = self._add_asset("stock", "NEWLIST", units=2)
        with patch("portfolio.portfolio_valuation._yfinance_price", return_value=42.5) as mock_yf:
            result = refresh_valuations(self.engine)
        mock_yf.assert_called_once_with("NEWLIST")
        self.assertEqual(result["valued"], 1)
        rows = self._valuation_rows(aid)
        self.assertEqual(float(rows[0][1]), 85.0)

    def test_same_day_upsert_overwrites(self) -> None:
        aid = self._add_asset("stock", "TCS", units=10)
        self._add_price("TCS", date(2026, 7, 3), 100.0)
        refresh_valuations(self.engine)
        self._add_price("TCS", date(2026, 7, 4), 120.0)
        refresh_valuations(self.engine)
        rows = self._valuation_rows(aid)
        self.assertEqual(len(rows), 1)   # same as_of=today row overwritten
        self.assertEqual(float(rows[0][1]), 1200.0)

    def test_archived_and_no_holdings_assets_ignored(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(assets).values(
                account_id=self.account_id, type="stock", name="archived",
                symbol="TCS", archived=True,
            ))
            conn.execute(insert(assets).values(
                account_id=self.account_id, type="stock", name="no-holdings",
                symbol="TCS",
            ))
            conn.execute(insert(assets).values(
                account_id=self.account_id, type="cash", name="cash-asset",
            ))
        self._add_price("TCS", date(2026, 7, 3), 100.0)
        result = refresh_valuations(self.engine)
        self.assertEqual(result["valued"], 0)
        self.assertEqual(result["skipped"], 0)

    def test_missing_symbol_skipped(self) -> None:
        self._add_asset("stock", None, units=5)
        result = refresh_valuations(self.engine)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["details"][0]["reason"], "no symbol")


if __name__ == "__main__":
    unittest.main()
