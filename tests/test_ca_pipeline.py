import unittest
import warnings
from datetime import date

from sqlalchemy import create_engine, text

from corporate_actions_pipeline import (
    _upsert_actions, recompute_symbol,
)
from db.models import corporate_actions, metadata, prices_daily, securities
from eod_prices_pipeline import _upsert_prices


def _silence_sqlite_date_adapter_warning() -> None:
    warnings.filterwarnings(
        "ignore",
        message="The default date adapter is deprecated",
        category=DeprecationWarning,
    )


def _price_row(symbol: str, d: date, close: float) -> dict:
    return {
        "symbol": symbol, "series": "EQ", "trade_date": d,
        "open": close, "high": close, "low": close, "close": close,
        "prev_close": close, "avg_price": close, "volume": 100,
        "turnover_lacs": 1.0, "trades": 10, "delivery_qty": 50,
        "delivery_pct": 50.0,
    }


def _action(symbol: str, ex: date, typ: str, factor: float | None,
            purpose: str, amount: float | None = None) -> dict:
    return {"symbol": symbol, "ex_date": ex, "type": typ,
            "purpose_raw": purpose, "price_factor": factor,
            "amount": amount, "record_date": None}


class CaPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        _silence_sqlite_date_adapter_warning()
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine,
                            tables=[securities, prices_daily, corporate_actions])

    def _adj(self, symbol: str, d: date) -> float:
        with self.engine.connect() as conn:
            val = conn.execute(
                text("SELECT adj_close FROM prices_daily WHERE symbol = :s AND trade_date = :d"),
                {"s": symbol, "d": d},
            ).scalar_one()
        return float(val)

    def test_insert_sets_adj_close_to_close(self) -> None:
        _upsert_prices(self.engine, [_price_row("TCS", date(2026, 7, 1), 100.0)])
        self.assertEqual(self._adj("TCS", date(2026, 7, 1)), 100.0)

    def test_reingest_preserves_recomputed_adj_close(self) -> None:
        _upsert_prices(self.engine, [_price_row("TCS", date(2026, 7, 1), 100.0)])
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE prices_daily SET adj_close = 12.34 WHERE symbol = 'TCS'"))
        _upsert_prices(self.engine, [_price_row("TCS", date(2026, 7, 1), 100.0)])
        self.assertEqual(self._adj("TCS", date(2026, 7, 1)), 12.34)

    def test_recompute_split_adjusts_only_before_ex_date(self) -> None:
        for d, close in [(date(2026, 6, 29), 1000.0), (date(2026, 6, 30), 1010.0),
                         (date(2026, 7, 1), 101.5), (date(2026, 7, 2), 102.0)]:
            _upsert_prices(self.engine, [_price_row("SPLITCO", d, close)])
        _upsert_actions(self.engine, [
            _action("SPLITCO", date(2026, 7, 1), "split", 0.1,
                    "FACE VALUE SPLIT FROM RS 10 TO RE 1")])
        n = recompute_symbol(self.engine, "SPLITCO")
        self.assertEqual(n, 4)
        self.assertEqual(self._adj("SPLITCO", date(2026, 6, 29)), 100.0)   # 1000 * 0.1
        self.assertEqual(self._adj("SPLITCO", date(2026, 6, 30)), 101.0)   # 1010 * 0.1
        self.assertEqual(self._adj("SPLITCO", date(2026, 7, 1)), 101.5)    # ex-date: unadjusted
        self.assertEqual(self._adj("SPLITCO", date(2026, 7, 2)), 102.0)

    def test_recompute_idempotent(self) -> None:
        _upsert_prices(self.engine, [_price_row("SPLITCO", date(2026, 6, 30), 1000.0)])
        _upsert_actions(self.engine, [
            _action("SPLITCO", date(2026, 7, 1), "split", 0.1, "SPLIT RS 10 TO RE 1")])
        recompute_symbol(self.engine, "SPLITCO")
        recompute_symbol(self.engine, "SPLITCO")
        self.assertEqual(self._adj("SPLITCO", date(2026, 6, 30)), 100.0)

    def test_duplicate_revision_rows_apply_once(self) -> None:
        _upsert_prices(self.engine, [_price_row("BONUSCO", date(2026, 6, 30), 100.0)])
        _upsert_actions(self.engine, [
            _action("BONUSCO", date(2026, 7, 1), "bonus", 0.5, "BONUS 1:1"),
            _action("BONUSCO", date(2026, 7, 1), "bonus", 0.5, "Bonus 1:1 (Revised)")])
        recompute_symbol(self.engine, "BONUSCO")
        self.assertEqual(self._adj("BONUSCO", date(2026, 6, 30)), 50.0)   # once, not 25

    def test_conflicting_factor_revision_applies_highest_id_only(self) -> None:
        _upsert_prices(self.engine, [_price_row("REVCO", date(2026, 6, 30), 100.0)])
        _upsert_actions(self.engine, [
            _action("REVCO", date(2026, 7, 1), "bonus", 0.5, "Bonus 1:1"),
            _action("REVCO", date(2026, 7, 1), "bonus", 0.333333, "Bonus 1:2 (Revised)")])
        recompute_symbol(self.engine, "REVCO")
        # higher-id (later-inserted) row wins: 0.333333, not both multiplied (0.5 * 0.333333)
        self.assertAlmostEqual(self._adj("REVCO", date(2026, 6, 30)), 33.3333, places=3)

    def test_action_upsert_idempotent_and_returns_affected(self) -> None:
        rows = [_action("TCS", date(2026, 7, 1), "bonus", 0.5, "BONUS 1:1"),
                _action("SBIN", date(2026, 7, 1), "dividend", None, "DIVIDEND RS 5", 5.0)]
        affected = _upsert_actions(self.engine, rows)
        self.assertEqual(affected, {"TCS"})   # only price-affecting symbols
        _upsert_actions(self.engine, rows)
        with self.engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM corporate_actions")).scalar_one()
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
