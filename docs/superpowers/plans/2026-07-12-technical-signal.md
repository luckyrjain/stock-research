# Technical Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a technical-analysis signal (RSI(14) momentum + 50/200-day moving-average trend) to `signals/engine.py`, sourced from the existing `prices_daily` EOD price store, so main-board stock analysis and market-picks scoring are no longer fundamentals-only.

**Architecture:** A new pure-scoring module `signals/technical.py` (fetch function + scoring function, mirroring `signals/volume.py`'s pattern) plugs into `run_signal_engine` as a 5th weighted signal. A new `db.models.get_engine_or_none()` primitive lets three call sites (`api.py`, `main.py`, `market_picks_pipeline.py`) pass a Postgres engine — or `None` — without ever raising if `DATABASE_URL` is unset, preserving the stock-analysis flow's historical DB-independence.

**Tech Stack:** Python 3.13, SQLAlchemy Core (existing `db/models.py` tables), `unittest`/`pytest`.

## Global Constraints

- Tools/pipeline functions must never raise — `fetch_technical_features` and `get_engine_or_none` return `{}`/`None` on any failure, never propagate an exception.
- DB-dependent tests use `create_engine("sqlite://")` + `metadata.create_all(engine, tables=[...])` (see `tests/test_sme_ema_pipeline.py:58-60`), never a real Postgres connection.
- `prices_daily.adj_close` is the field for price math (not `close`).
- No new dependencies.
- Quant-only: no changes to `config/analyst.json`, `crew.py`, `main._build_report`'s output schema, or `frontend/types/index.ts`.
- Existing signal weights (valuation 0.4, volume 0.2, growth 0.4, filings 0.2) and score thresholds in `signals/engine.py` are unchanged — only a new `"technical": 0.3` weight is added.
- RSI here is simple (non-Wilder-smoothed) averaging — a documented, deliberate simplification, not a defect.

---

### Task 1: `signals/technical.py` — RSI + trend scoring module

**Files:**
- Create: `signals/technical.py`
- Test: `tests/test_technical_signal.py` (new)

**Interfaces:**
- Consumes: `db.models.prices_daily` table (`symbol`, `trade_date`, `adj_close` — see `db/models.py:125-143`).
- Produces:
  - `fetch_technical_features(engine, symbol: str) -> dict` — returns `{"price": float, "rsi": float|None, "ma50": float, "ma200": float}` or `{}`.
  - `technical_signal(features: dict) -> Signal` (from `signals.models.Signal`) — Task 2 calls both of these directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_technical_signal.py
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine, insert

from db.models import metadata, prices_daily
from signals.technical import _rsi, fetch_technical_features, technical_signal


class RsiTest(unittest.TestCase):
    def test_all_gains_returns_100(self) -> None:
        closes = [100.0 + i for i in range(15)]  # 14 consecutive up days
        self.assertEqual(_rsi(closes, 14), 100.0)

    def test_all_losses_returns_zero(self) -> None:
        closes = [100.0 - i for i in range(15)]  # 14 consecutive down days
        self.assertEqual(_rsi(closes, 14), 0.0)

    def test_known_mixed_sequence(self) -> None:
        # 7 up-days of +1, 7 down-days of -1 -> avg_gain == avg_loss -> RSI == 50
        closes = [100.0]
        for _ in range(7):
            closes.append(closes[-1] + 1)
        for _ in range(7):
            closes.append(closes[-1] - 1)
        self.assertAlmostEqual(_rsi(closes, 14), 50.0)

    def test_insufficient_data_returns_none(self) -> None:
        closes = [100.0, 101.0, 102.0]  # fewer than period+1 points
        self.assertIsNone(_rsi(closes, 14))


class TechnicalSignalTest(unittest.TestCase):
    def test_bullish_neutral(self) -> None:
        sig = technical_signal({"price": 110, "ma50": 105, "ma200": 100, "rsi": 50})
        self.assertEqual(sig.value, "BULLISH_NEUTRAL")
        self.assertAlmostEqual(sig.score, 0.6)

    def test_bullish_overbought_dampens_score(self) -> None:
        sig = technical_signal({"price": 110, "ma50": 105, "ma200": 100, "rsi": 75})
        self.assertEqual(sig.value, "BULLISH_OVERBOUGHT")
        self.assertAlmostEqual(sig.score, 0.3)

    def test_bearish_oversold(self) -> None:
        sig = technical_signal({"price": 90, "ma50": 95, "ma200": 100, "rsi": 25})
        self.assertEqual(sig.value, "BEARISH_OVERSOLD")
        self.assertAlmostEqual(sig.score, -0.3)

    def test_mixed_trend_neutral_rsi(self) -> None:
        sig = technical_signal({"price": 102, "ma50": 100, "ma200": 105, "rsi": 50})
        self.assertEqual(sig.value, "MIXED_NEUTRAL")
        self.assertAlmostEqual(sig.score, 0.0)

    def test_missing_price_data_is_unknown(self) -> None:
        sig = technical_signal({})
        self.assertEqual(sig.value, "UNKNOWN")
        self.assertEqual(sig.score, 0)

    def test_missing_rsi_defaults_to_neutral(self) -> None:
        sig = technical_signal({"price": 110, "ma50": 105, "ma200": 100, "rsi": None})
        self.assertEqual(sig.value, "BULLISH_NEUTRAL")


class FetchTechnicalFeaturesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine, tables=[prices_daily])

    def _insert_prices(self, symbol: str, closes: list[float], start: date) -> None:
        with self.engine.begin() as conn:
            for i, close in enumerate(closes):
                conn.execute(insert(prices_daily).values(
                    symbol=symbol, trade_date=start + timedelta(days=i),
                    adj_close=close,
                ))

    def test_engine_none_returns_empty(self) -> None:
        self.assertEqual(fetch_technical_features(None, "TESTCO"), {})

    def test_sufficient_history_returns_features(self) -> None:
        closes = [100.0 + (i % 5) for i in range(220)]
        self._insert_prices("TESTCO", closes, date.today() - timedelta(days=220))
        features = fetch_technical_features(self.engine, "TESTCO")
        self.assertIn("price", features)
        self.assertIn("rsi", features)
        self.assertIn("ma50", features)
        self.assertIn("ma200", features)

    def test_insufficient_history_returns_empty(self) -> None:
        closes = [100.0 + i for i in range(50)]  # fewer than 200 rows
        self._insert_prices("TESTCO", closes, date.today() - timedelta(days=50))
        self.assertEqual(fetch_technical_features(self.engine, "TESTCO"), {})

    def test_symbol_with_no_rows_returns_empty(self) -> None:
        self.assertEqual(fetch_technical_features(self.engine, "NOSUCHSTOCK"), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_technical_signal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'signals.technical'`

- [ ] **Step 3: Implement `signals/technical.py`**

```python
"""Technical signal: RSI(14) momentum + 50/200-day moving-average trend.

RSI here is a simple (non-Wilder-smoothed) average over the trailing window —
a known simplification, not full Wilder's RSI. Fine for a coarse signal;
revisit if the track-record engine shows it's misleading.
"""

from datetime import date, timedelta

from sqlalchemy import select

from db.models import prices_daily
from signals.models import Signal

_RSI_PERIOD = 14
_MA_SHORT = 50
_MA_LONG = 200
_LOOKBACK_DAYS = 300  # calendar days fetched — covers MA200 + RSI warmup incl. weekends/holidays


def fetch_technical_features(engine, symbol: str) -> dict:
    """Fetch adj_close history and compute RSI/MA features. {} on any failure or insufficient data."""
    if engine is None:
        return {}
    try:
        cutoff = date.today() - timedelta(days=_LOOKBACK_DAYS)
        with engine.connect() as conn:
            rows = conn.execute(
                select(prices_daily.c.trade_date, prices_daily.c.adj_close)
                .where(prices_daily.c.symbol == symbol)
                .where(prices_daily.c.trade_date >= cutoff)
                .order_by(prices_daily.c.trade_date.asc())
            ).fetchall()
    except Exception:
        return {}

    closes = [float(r.adj_close) for r in rows if r.adj_close is not None]
    if len(closes) < _MA_LONG:
        return {}

    return {
        "price": closes[-1],
        "rsi":   _rsi(closes, _RSI_PERIOD),
        "ma50":  sum(closes[-_MA_SHORT:]) / _MA_SHORT,
        "ma200": sum(closes[-_MA_LONG:]) / _MA_LONG,
    }


def _rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = deltas[-period:]
    gains  = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def technical_signal(features: dict) -> Signal:
    price, ma50, ma200, rsi = (
        features.get("price"), features.get("ma50"),
        features.get("ma200"), features.get("rsi"),
    )
    if price is None or ma50 is None or ma200 is None:
        return Signal("technical", "UNKNOWN", 0, {})

    if price > ma50 > ma200:
        trend, trend_score = "BULLISH", 0.6
    elif price < ma50 < ma200:
        trend, trend_score = "BEARISH", -0.6
    else:
        trend, trend_score = "MIXED", 0.0

    rsi_zone, rsi_adj = "NEUTRAL", 0.0
    if rsi is not None:
        if rsi > 70:
            rsi_zone, rsi_adj = "OVERBOUGHT", -0.3
        elif rsi < 30:
            rsi_zone, rsi_adj = "OVERSOLD", 0.3

    score = max(-1.0, min(1.0, trend_score + rsi_adj))
    return Signal("technical", f"{trend}_{rsi_zone}", score,
                   {"rsi": rsi, "ma50": ma50, "ma200": ma200, "price": price})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_technical_signal.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add signals/technical.py tests/test_technical_signal.py
git commit -m "$(cat <<'EOF'
feat: add technical signal module — RSI(14) + 50/200-day MA trend

New signals/technical.py mirrors the existing per-signal-module pattern
(signals/volume.py): fetch_technical_features reads prices_daily and never
raises, technical_signal is a pure scoring function. Not yet wired into
run_signal_engine — that's the next task.
EOF
)"
```

---

### Task 2: Wire technical signal into `signals/engine.py` + soft engine getter

**Files:**
- Modify: `signals/engine.py` (all of it — currently 46 lines)
- Modify: `db/models.py:170-172` (`get_engine`)
- Test: `tests/test_signals_engine.py` (new — no test file currently exists for `signals/engine.py`)

**Interfaces:**
- Consumes: `fetch_technical_features`, `technical_signal` from `signals.technical` (Task 1).
- Produces: `run_signal_engine(symbol: str, all_data: dict, engine=None) -> SignalResult` — the `engine` parameter is new; Task 3's three call sites pass a real engine or `None` here. `db.models.get_engine_or_none(database_url=None) -> Engine | None` — Task 3 uses this at each call site.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_signals_engine.py
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine, insert

from db.models import metadata, prices_daily
from signals.engine import run_signal_engine


class RunSignalEngineTest(unittest.TestCase):
    def test_no_engine_gives_unknown_technical_signal(self) -> None:
        all_data = {"stock_info": {"current_price": 100, "volume": 1000, "avg_volume_10d": 1000,
                                    "pe_ratio": 20, "market_cap_cr": 5000, "sector": "IT"},
                     "research": {"ratios": {}}, "filings": {"filings": []}}
        result = run_signal_engine("TESTCO", all_data)
        self.assertEqual(result.signals["technical"].value, "UNKNOWN")
        self.assertEqual(result.signals["technical"].score, 0)

    def test_engine_with_sufficient_history_gives_real_technical_signal(self) -> None:
        engine = create_engine("sqlite://")
        metadata.create_all(engine, tables=[prices_daily])
        closes = [100.0 + i * 0.5 for i in range(220)]  # steady uptrend
        start = date.today() - timedelta(days=220)
        with engine.begin() as conn:
            for i, close in enumerate(closes):
                conn.execute(insert(prices_daily).values(
                    symbol="TESTCO", trade_date=start + timedelta(days=i), adj_close=close,
                ))

        all_data = {"stock_info": {"current_price": 100, "volume": 1000, "avg_volume_10d": 1000,
                                    "pe_ratio": 20, "market_cap_cr": 5000, "sector": "IT"},
                     "research": {"ratios": {}}, "filings": {"filings": []}}
        result = run_signal_engine("TESTCO", all_data, engine=engine)
        self.assertNotEqual(result.signals["technical"].value, "UNKNOWN")

    def test_technical_weight_present_in_final_score(self) -> None:
        # A strongly bullish technical signal should be able to move final_score
        # even when every other signal is neutral/zero.
        engine = create_engine("sqlite://")
        metadata.create_all(engine, tables=[prices_daily])
        closes = [100.0 + i for i in range(220)]  # strong uptrend -> BULLISH
        start = date.today() - timedelta(days=220)
        with engine.begin() as conn:
            for i, close in enumerate(closes):
                conn.execute(insert(prices_daily).values(
                    symbol="TESTCO", trade_date=start + timedelta(days=i), adj_close=close,
                ))

        all_data = {"stock_info": {"current_price": closes[-1], "volume": 1000, "avg_volume_10d": 1000,
                                    "pe_ratio": None, "market_cap_cr": None, "sector": None},
                     "research": {"ratios": {}}, "filings": {"filings": []}}
        without_tech = run_signal_engine("TESTCO", all_data, engine=None)
        with_tech = run_signal_engine("TESTCO", all_data, engine=engine)
        self.assertGreater(with_tech.final_score, without_tech.final_score)


if __name__ == "__main__":
    unittest.main()
```

```python
# add to tests/test_db_models.py if it exists, otherwise create tests/test_get_engine_or_none.py
import unittest
from unittest.mock import patch

from db.models import get_engine_or_none


class GetEngineOrNoneTest(unittest.TestCase):
    def test_missing_database_url_returns_none(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(get_engine_or_none())

    def test_explicit_url_returns_engine(self) -> None:
        engine = get_engine_or_none("sqlite://")
        self.assertIsNotNone(engine)

    def test_env_var_url_returns_engine(self) -> None:
        with patch.dict("os.environ", {"DATABASE_URL": "sqlite://"}, clear=True):
            self.assertIsNotNone(get_engine_or_none())
```

Check first whether `tests/test_db_models.py` already exists — if not, create `tests/test_get_engine_or_none.py` with the snippet above (drop the "add to ... if it exists" comment, just write the file).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signals_engine.py tests/test_get_engine_or_none.py -v`
Expected: FAIL — `run_signal_engine() got an unexpected keyword argument 'engine'` and `ImportError: cannot import name 'get_engine_or_none'`

- [ ] **Step 3: Implement the changes**

In `db/models.py`, after the existing `get_engine` function (currently lines 170-172):

```python
def get_engine(database_url: str | None = None):
    url = database_url or os.environ["DATABASE_URL"]
    return _create_engine(url)


def get_engine_or_none(database_url: str | None = None):
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        return None
    return _create_engine(url)
```

Replace the full contents of `signals/engine.py` with:

```python
"""Signal engine orchestration for scoring and verdict generation."""

from signals.features import extract_features
from signals.volume import volume_signal
from signals.valuation import valuation_signal
from signals.models import SignalResult
from signals.growth import growth_signal
from signals.filings import filings_signal
from signals.technical import fetch_technical_features, technical_signal

def run_signal_engine(symbol: str, all_data: dict, engine=None) -> SignalResult:
    """Compute weighted signal scores and return a trading verdict."""
    features = extract_features(all_data)
    tech_features = fetch_technical_features(engine, symbol)

    signals = {
        "volume": volume_signal(features),
        "valuation": valuation_signal(features),
        "growth": growth_signal(features),
        "filings": filings_signal(features),
        "technical": technical_signal(tech_features),
    }

    weights = {
        "valuation": 0.4,
        "volume": 0.2,
        "growth": 0.4,
        "filings": 0.2,
        "technical": 0.3,
    }

    score = 0
    for name, weight in weights.items():
        sig = signals.get(name)
        if not sig:
            continue
        score += sig.score * weight

    if score > 0.5:
        verdict = "BUY"
    elif score > 0.1:
        verdict = "WATCHLIST"
    elif score > -0.3:
        verdict = "HOLD"
    elif score > -0.6:
        verdict = "AVOID"
    else:
        verdict = "SELL"

    return SignalResult(
        symbol=symbol,
        signals=signals,
        final_score=round(score, 2),
        verdict=verdict
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signals_engine.py tests/test_get_engine_or_none.py -v`
Expected: PASS (6 tests)

Then run the full suite to confirm no regressions:

Run: `python -m pytest tests/ -v`
Expected: all pass (183 existing + new tests from this plan)

- [ ] **Step 5: Commit**

```bash
git add db/models.py signals/engine.py tests/test_signals_engine.py tests/test_get_engine_or_none.py
git commit -m "$(cat <<'EOF'
feat: wire technical signal into run_signal_engine

run_signal_engine gains an optional engine param (default None — technical
signal contributes UNKNOWN/0, zero behavior change for existing callers).
Adds db.models.get_engine_or_none, a soft variant of get_engine that
returns None instead of raising when DATABASE_URL is unset — the
stock-analysis flow has never required Postgres and shouldn't start now.
Weights/thresholds otherwise unchanged.
EOF
)"
```

---

### Task 3: Wire engine into the 3 call sites

**Files:**
- Modify: `api.py:54-59` (`_get_sme_engine`), `api.py:502` (analysis endpoint)
- Modify: `main.py` (add helper near imports, modify line 290)
- Modify: `market_picks_pipeline.py:553-556` (`MarketPicksPipeline.__init__`), `market_picks_pipeline.py:990` (`_phase_research`)

**Interfaces:**
- Consumes: `run_signal_engine(symbol, all_data, engine=None)` (Task 2), `db.models.get_engine_or_none()` (Task 2).
- Produces: no new interfaces — this task only wires existing ones through to three call sites. Nothing later depends on this task.

- [ ] **Step 1: Update `api.py`'s `_get_sme_engine`**

Current (`api.py:54-59`):

```python
def _get_sme_engine():
    global _SME_ENGINE
    if _SME_ENGINE is None:
        from db.models import get_engine
        _SME_ENGINE = get_engine()
    return _SME_ENGINE
```

Replace with:

```python
def _get_sme_engine():
    global _SME_ENGINE
    if _SME_ENGINE is None:
        from db.models import get_engine_or_none
        _SME_ENGINE = get_engine_or_none()
    return _SME_ENGINE
```

This is safe for existing SME callers: both `/api/sme-signals` and `/api/sme-signals/refresh` already check `os.environ.get("DATABASE_URL")` and return a 503 *before* ever calling `_get_sme_engine()`, so they never observe the `None` case — behavior for them is unchanged. It is now also safe to call from a context where `DATABASE_URL` might be unset.

- [ ] **Step 2: Pass the engine into the analysis endpoint**

In `api.py`, find `signal_result = run_signal_engine(sym, all_data)` (currently line 502) and change it to:

```python
            signal_result = run_signal_engine(sym, all_data, engine=_get_sme_engine())
```

- [ ] **Step 3: Add a soft engine getter to `main.py` and use it**

In `main.py`, add near the top-level module code (after the existing imports, e.g. right after the `from tools.nse_filings_tools import get_nse_filings` line):

```python
_ENGINE = None
_ENGINE_LOADED = False


def _get_engine_or_none():
    global _ENGINE, _ENGINE_LOADED
    if not _ENGINE_LOADED:
        from db.models import get_engine_or_none
        _ENGINE = get_engine_or_none()
        _ENGINE_LOADED = True
    return _ENGINE
```

Then find `signal_result = run_signal_engine(symbol, all_data)` (currently line 290) and change it to:

```python
    signal_result = run_signal_engine(symbol, all_data, engine=_get_engine_or_none())
```

- [ ] **Step 4: Cache the engine on the pipeline instance and use it**

In `market_picks_pipeline.py`, find `MarketPicksPipeline.__init__` (currently lines 553-556):

```python
class MarketPicksPipeline:
    def __init__(self):
        self._run_id = uuid.uuid4().hex[:8]
```

Add an engine field, lazily populated:

```python
class MarketPicksPipeline:
    def __init__(self):
        self._run_id = uuid.uuid4().hex[:8]
        self._engine = None
        self._engine_loaded = False

    def _get_engine(self):
        if not self._engine_loaded:
            from db.models import get_engine_or_none
            self._engine = get_engine_or_none()
            self._engine_loaded = True
        return self._engine
```

Then in `_phase_research`'s inner `_research_one` closure (currently around line 990), find:

```python
                signal_result  = run_signal_engine(symbol, all_data)
```

and change it to:

```python
                signal_result  = run_signal_engine(symbol, all_data, engine=self._get_engine())
```

(`self` is already in scope inside `_research_one` since it's a closure defined inside the `_phase_research` instance method — confirmed by its existing use of `self._run_id` two lines above this call site.)

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all pass, no regressions (this task adds no new test file — it only wires already-tested functions through call sites; the existing suite plus Tasks 1-2's new tests are the coverage)

- [ ] **Step 6: Manual verification**

```bash
source .venv/bin/activate
uvicorn api:app --reload --port 8000 &
sleep 2
curl -s "http://localhost:8000/api/validate/TCS" | python3 -m json.tool | head -5
curl -s "http://localhost:8000/api/analyse/TCS" --max-time 5 | head -c 2000
```

Confirm no 500 error and the SSE stream starts producing events (a full run takes longer than the 5s `--max-time` — that's fine, you're only confirming it starts cleanly, not waiting for completion). If `DATABASE_URL` is unset in this environment, confirm the same command still works (technical signal silently contributes 0 in that case — this is the whole point of the soft-fail design).

Kill the server afterward: `kill %1`.

- [ ] **Step 7: Commit**

```bash
git add api.py main.py market_picks_pipeline.py
git commit -m "$(cat <<'EOF'
feat: pass technical-signal engine through all 3 run_signal_engine call sites

api.py's stock-analysis endpoint reuses _get_sme_engine (now soft — returns
None instead of raising when DATABASE_URL is unset). main.py gets an
analogous module-level memoized getter. market_picks_pipeline.py caches
the engine once per pipeline run on the MarketPicksPipeline instance,
reused across parallel per-stock research calls. All three degrade to
UNKNOWN/0 technical signal with no crash if the DB is unavailable.
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** `signals/technical.py` module (Task 1), `run_signal_engine` wiring + weight + soft engine getter (Task 2), all 3 call sites (Task 3) — every section of the Phase 2 spec is covered. Out-of-scope items (MACD, support/resistance, LLM prompt changes, renormalization, Wilder's RSI) are correctly absent from every task.
- **Placeholder scan:** none found — all steps have complete, runnable code.
- **Type consistency:** `fetch_technical_features(engine, symbol) -> dict` and `technical_signal(features: dict) -> Signal` (Task 1) are called with matching signatures in Task 2's `run_signal_engine`. `run_signal_engine(symbol, all_data, engine=None)` (Task 2) is called identically at all three Task 3 call sites. `get_engine_or_none(database_url=None)` (Task 2, in `db/models.py`) is imported and called the same way (no args, relying on `DATABASE_URL` env var) at all three Task 3 call sites.
