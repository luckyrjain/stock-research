# SME Golden Cross Screener + Audit Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace price-vs-EMA detection in the SME pipeline with EMA20/EMA50 golden-cross/death-cross detection (events + current regime), and fix audit bugs: dead analyst guardrail, stale tests, engine leak, dead `/?symbol=` links, missing pipeline scheduling.

**Architecture:** The batch pipeline (`sme_ema_pipeline.py`) computes crosses over 1 year of yfinance data and stores the last 63 trading days per stock in PostgreSQL. FastAPI serves the signals plus a background refresh endpoint. The Next.js SME page consumes the new shape. Independent of that, `crew.py` gets its guardrail restored with a one-shot corrective LLM retry.

**Tech Stack:** Python 3.13, pandas/numpy, SQLAlchemy Core + raw `text()` SQL, PostgreSQL, FastAPI, litellm, Next.js 15 / React 19 / Tailwind v3, unittest (run via pytest).

**Spec:** `docs/superpowers/specs/2026-07-03-sme-golden-cross-and-fixes-design.md`

## Global Constraints

- Always `source .venv/bin/activate` before any Python command; repo root is `/Users/luckyratanlaljain/project/stock-research`.
- Backend tests: `python -m pytest tests/` (unittest-style tests, collected by pytest). No external network/LLM calls in tests — mock everything.
- Frontend gate: `cd frontend && npx tsc --noEmit` must pass before any frontend task is done. npm only.
- Tool/pipeline functions never raise — return `{"error": "...", "symbol": sym}`.
- **DB column is `cross_type`** (CROSS is a reserved keyword in PostgreSQL); **the JSON/API/TS field is `cross`**. Values: `'golden'` / `'death'` / `NULL`.
- `direction` API values: `all | golden | death`. Lookback: 1–30 days, default 5.
- Match surrounding code style (aligned dict colons, `_`-prefixed private helpers, `snake_case`).
- Commit after every task. Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Repair tooling (pytest + node_modules)

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Produces: a working `python -m pytest tests/` command and a working `npx tsc --noEmit` gate for all later tasks.

- [ ] **Step 1: Add pytest to requirements.txt**

Append to the end of `requirements.txt` (after the optional-providers block):

```
# Dev / test
pytest>=8.0.0
```

- [ ] **Step 2: Install backend deps and repair frontend node_modules**

```bash
source .venv/bin/activate && pip install pytest
cd frontend && npm install
```

Expected: pytest installs; `npm install` completes (repairs the broken typescript package whose `lib/*.d.ts` files are missing).

- [ ] **Step 3: Verify both gates run**

```bash
source .venv/bin/activate && python -m pytest tests/ -q
cd frontend && npx tsc --noEmit
```

Expected: pytest runs 14 tests with **2 known failures** in `test_analysis_guardrails.py` (fixed in Task 2). `tsc` must exit 0 — if it reports errors in existing code, stop and report; do not fix unrelated code.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "Add pytest to requirements so the documented test command works"
```

---

### Task 2: crew.py guardrail fixes + retry (TDD)

**Files:**
- Modify: `crew.py` (`_validate_analysis_payload`, `_analysis_support_issues`, `run_analysis_with_fallback`)
- Test: `tests/test_analysis_guardrails.py`

**Interfaces:**
- Produces: `run_analysis_with_fallback(symbol, all_data, signal_context=None, run_id=None) -> dict` — same signature, new behavior: one corrective retry on guardrail failure before `_safe_analysis_fallback`. `_analysis_support_issues` appends `"<label> claim is not supported by the provided source data"` for ungrounded claims.

- [ ] **Step 1: Rewrite the stale fallback test and add the retry test**

In `tests/test_analysis_guardrails.py`, add `import json` to the imports at the top, add this module-level helper after the imports, **replace** `test_invalid_structured_analysis_falls_back_safely` entirely, and add the two new tests inside `AnalysisGuardrailFallbackTest`:

```python
def _llm_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
```

```python
    _INVALID_PAYLOAD = {
        "symbol": "TCS",
        "recommendation": "HOLD",
        "confidence": "LOW",
        "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
        "valuation": {"verdict": "Fairly Valued", "comment": "P/E 20, ROCE 25, ROE 18."},
        "business_quality": "Reasonable return ratios.",
        "bull_factors": ["Only one", "Only two"],
        "bear_factors": ["Risk one", "Risk two"],
        "key_risks": ["Risk A", "Risk B", "Risk C"],
        "news_highlights": "Headline summary",
        "institutional_trend": "Promoters 50%, FIIs 10%, DIIs 12%",
        "news_sentiment": "Neutral",
    }

    _VALID_PAYLOAD = {
        "symbol": "SAILIFE",
        "recommendation": "HOLD",
        "confidence": "LOW",
        "summary": "Sentence one. Sentence two. Sentence three. Sentence four.",
        "valuation": {"verdict": "Fairly Valued", "comment": "P/E 64.8, P/B 9.4, ROCE 14.1, ROE 11.0."},
        "business_quality": "ROCE is 14.1 and ROE is 11.0.",
        "bull_factors": ["P/E is 64.8.", "ROCE is 14.1.", "DIIs hold 31.54%."],
        "bear_factors": ["P/B is 9.4.", "Promoters hold 34.61%."],
        "key_risks": ["Premium valuation at P/E 64.8.", "Limited news coverage.", "Promoter ownership is 34.61%."],
        "news_highlights": "One RSI-based headline was available.",
        "institutional_trend": "Promoters hold 34.61%, FIIs 21.17%, DIIs 31.54%.",
        "news_sentiment": "Neutral",
    }

    def test_invalid_structured_analysis_falls_back_safely(self) -> None:
        # Both attempts return an invalid payload (too few bull_factors) →
        # guardrail retry fires once, then the safe HOLD fallback is used.
        with patch("litellm.completion", return_value=_llm_response(json.dumps(self._INVALID_PAYLOAD))) as mock_completion:
            analysis = crew.run_analysis_with_fallback("TCS", {name: {} for name in crew.ALL_DATA_TASKS})

        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(analysis["recommendation"], "HOLD")
        self.assertEqual(analysis["confidence"], "LOW")
        self.assertEqual(len(analysis["bull_factors"]), 3)
        self.assertIn("bull_factors", analysis["valuation"]["comment"])

    def test_guardrail_failure_retries_once_then_succeeds(self) -> None:
        responses = [
            _llm_response(json.dumps(self._INVALID_PAYLOAD)),
            _llm_response(json.dumps(self._VALID_PAYLOAD)),
        ]
        with patch("litellm.completion", side_effect=responses) as mock_completion:
            analysis = crew.run_analysis_with_fallback("SAILIFE", self.all_data)

        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(analysis["bull_factors"], self._VALID_PAYLOAD["bull_factors"])
        second_messages = mock_completion.call_args_list[1].kwargs["messages"]
        self.assertIn("failed validation", second_messages[-1]["content"])

    def test_llm_exception_returns_safe_fallback(self) -> None:
        with patch("litellm.completion", side_effect=RuntimeError("boom")):
            analysis = crew.run_analysis_with_fallback("TCS", {name: {} for name in crew.ALL_DATA_TASKS})

        self.assertEqual(analysis["recommendation"], "HOLD")
        self.assertIn("boom", analysis["valuation"]["comment"])
```

- [ ] **Step 2: Run the tests to verify they fail for the right reasons**

```bash
source .venv/bin/activate && python -m pytest tests/test_analysis_guardrails.py -v
```

Expected failures: `test_invalid_structured_analysis_falls_back_safely` (call_count is 1, no retry yet), `test_guardrail_failure_retries_once_then_succeeds` (no retry yet), `test_validate_analysis_payload_rejects_unsupported_regulatory_risk` (check is dead code). No test may hit the network.

- [ ] **Step 3: Fix the indentation bug in `_validate_analysis_payload`**

In `crew.py`, replace the `for field ...` block (currently the length checks are nested inside the loop and run 7×):

```python
    for field in ("summary", "business_quality", "bull_factors", "bear_factors",
                  "key_risks", "news_highlights", "institutional_trend"):
        if not data.get(field):
            return False, f"Field '{field}' is required and cannot be empty."

    if len(data.get("bull_factors", [])) < 3:
        return False, "Field 'bull_factors' must contain at least 3 items."

    if len(data.get("bear_factors", [])) < 2:
        return False, "Field 'bear_factors' must contain at least 2 items."

    if len(data.get("key_risks", [])) < 3:
        return False, "Field 'key_risks' must contain at least 3 items."
```

- [ ] **Step 4: Restore the grounded-claims checks**

In `crew.py` `_analysis_support_issues`, replace the dead loop body:

```python
    for label, trigger_phrases, source_terms in grounded_checks:
        if any(phrase in analysis_text for phrase in trigger_phrases) and not any(term in source_text for term in source_terms):
            issues.append(f"{label} claim is not supported by the provided source data")
```

(Remove the `# downgrade instead of fail` / `continue` lines.)

- [ ] **Step 5: Add the guardrail retry to `run_analysis_with_fallback`**

Replace the `for attempt in range(2): ...` loop (everything from `for attempt` to the final `return _safe_analysis_fallback(symbol, "analyst failed after rate-limit retry")`) with:

```python
    messages: list[dict] = [{"role": "user", "content": prompt}]
    rate_limit_retry_used = False
    guardrail_retry_used = False

    while True:
        try:
            started_at = time.perf_counter()
            log_event(LOGGER, "analyst_llm_started", run_id=run_id, symbol=symbol,
                      guardrail_retry=guardrail_retry_used)
            response = litellm.completion(
                model=model,
                messages=messages,
                api_key=api_key,
            )
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            text = response.choices[0].message.content or ""
            parsed = parse_json_object(text)
            ok, validated = _validate_analysis_payload(parsed, all_data)
            if ok:
                log_event(LOGGER, "analyst_llm_succeeded", run_id=run_id, symbol=symbol, latency_ms=elapsed_ms)
                return parsed

            if not guardrail_retry_used:
                guardrail_retry_used = True
                log_event(
                    LOGGER, "analyst_guardrail_retry", level="warning",
                    run_id=run_id, symbol=symbol, latency_ms=elapsed_ms, error=str(validated),
                )
                messages = [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        f"Your previous response failed validation: {validated} "
                        "Return only the corrected JSON object — no markdown, no prose."
                    )},
                ]
                continue

            log_event(
                LOGGER, "analyst_llm_failed", level="warning",
                run_id=run_id, symbol=symbol, latency_ms=elapsed_ms,
                error=str(validated), failure_stage="guardrail",
            )
            return _safe_analysis_fallback(symbol, str(validated))

        except Exception as exc:  # pylint: disable=broad-exception-caught
            if _is_rate_limit(exc) and not rate_limit_retry_used:
                rate_limit_retry_used = True
                wait = _rate_limit_wait_secs(exc)
                log_event(
                    LOGGER, "analyst_rate_limited", level="warning",
                    run_id=run_id, symbol=symbol, wait_seconds=wait, error=str(exc),
                )
                time.sleep(wait)
                continue

            log_event(
                LOGGER, "analyst_llm_failed", level="error",
                run_id=run_id, symbol=symbol, error=str(exc), failure_stage="exception",
            )
            return _safe_analysis_fallback(symbol, str(exc))
```

Note: every branch either `return`s or sets a one-shot flag before `continue`, so the loop cannot spin.

- [ ] **Step 6: Run the full backend suite**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all tests pass (16 total: the 14 existing — one of them rewritten — plus two new).

- [ ] **Step 7: Commit**

```bash
git add crew.py tests/test_analysis_guardrails.py
git commit -m "Restore grounded-claims guardrail with corrective retry; fix stale tests"
```

---

### Task 3: DB schema — `cross_type` column

**Files:**
- Modify: `db/models.py`, `db/schema.sql`

**Interfaces:**
- Produces: `ema_signals` table with `cross_type VARCHAR(10)` (`'golden'`/`'death'`/`NULL`) replacing `crossed_ema20`, `crossed_ema50`, `cross_direction`. All other columns/constraints unchanged. `db.models.get_engine` unchanged.

- [ ] **Step 1: Update `db/models.py`**

Replace the `ema_signals` table definition with:

```python
ema_signals = Table(
    "ema_signals",
    metadata,
    Column("id",          Integer, primary_key=True, autoincrement=True),
    Column("symbol",      String(20), ForeignKey("sme_stocks.symbol"), nullable=False),
    Column("trade_date",  Date,       nullable=False),
    Column("close_price", Numeric(12, 4)),
    Column("ema20",       Numeric(12, 4)),
    Column("ema50",       Numeric(12, 4)),
    Column("cross_type",  String(10)),   # 'golden' | 'death' | NULL ('cross' is reserved in SQL)
    Column("run_at",      DateTime(timezone=True), server_default=text("NOW()")),
    UniqueConstraint("symbol", "trade_date", name="uq_ema_signals_symbol_date"),
    Index("idx_ema_signals_date",  "trade_date"),
    Index("idx_ema_signals_cross", "cross_type"),
)
```

Also remove `Boolean` from the sqlalchemy import list (it becomes unused).

- [ ] **Step 2: Update `db/schema.sql`**

Replace the `ema_signals` table + index block with:

```sql
CREATE TABLE IF NOT EXISTS ema_signals (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL REFERENCES sme_stocks(symbol),
    trade_date      DATE        NOT NULL,
    close_price     NUMERIC(12, 4),
    ema20           NUMERIC(12, 4),
    ema50           NUMERIC(12, 4),
    cross_type      VARCHAR(10),          -- 'golden' | 'death' | NULL
    run_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_ema_signals_date  ON ema_signals(trade_date);
CREATE INDEX IF NOT EXISTS idx_ema_signals_cross ON ema_signals(cross_type);
```

- [ ] **Step 3: Verify the module still imports**

```bash
source .venv/bin/activate && python -c "from db.models import metadata, get_engine; print([c.name for c in metadata.tables['ema_signals'].columns])"
```

Expected output includes `cross_type` and excludes `crossed_ema20`.

- [ ] **Step 4: Commit**

```bash
git add db/models.py db/schema.sql
git commit -m "Replace price-cross columns with cross_type (golden/death) in ema_signals"
```

---

### Task 4: Golden/death cross detection in the pipeline (TDD)

**Files:**
- Modify: `sme_ema_pipeline.py`
- Test: Create `tests/test_sme_ema_pipeline.py`

**Interfaces:**
- Consumes: `db.models.metadata` (Task 3).
- Produces: `_compute_ema_signals(result: dict) -> list[dict]` returning rows `{symbol, trade_date, close_price, ema20, ema50, cross}` with `cross ∈ {'golden','death',None}`, at most `_STORE_DAYS` (63) rows. `run(force=False, lookback_days=5)` unchanged signature (used by Task 5's refresh endpoint). New CLI flag `--reset-db`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sme_ema_pipeline.py`:

```python
import unittest
from datetime import date, timedelta

import pandas as pd

from sme_ema_pipeline import _compute_ema_signals, _STORE_DAYS


def _make_result(closes: list[float]) -> dict:
    start = date(2026, 1, 1)
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    df = pd.DataFrame({"Close": closes}, index=idx)
    return {"symbol": "TESTSME", "exchange": "NSE", "df": df}


class ComputeEmaSignalsTest(unittest.TestCase):
    def test_golden_cross_detected_once_on_v_shaped_recovery(self) -> None:
        # 60 days falling, then 60 days rising strongly: EMA20 crosses above EMA50 once.
        closes = [200.0 - i for i in range(60)] + [140.0 + 3.0 * i for i in range(60)]
        rows = _compute_ema_signals(_make_result(closes))

        golden = [r for r in rows if r["cross"] == "golden"]
        death = [r for r in rows if r["cross"] == "death"]
        self.assertEqual(len(golden), 1)
        self.assertEqual(len(death), 0)
        # On the cross day EMA20 is above EMA50, and it is the first stored day above.
        self.assertGreater(golden[0]["ema20"], golden[0]["ema50"])
        first_above = next(r for r in rows if r["ema20"] > r["ema50"])
        self.assertEqual(first_above["trade_date"], golden[0]["trade_date"])

    def test_death_cross_detected_once_on_peak_and_decline(self) -> None:
        closes = [100.0 + i for i in range(60)] + [160.0 - 2.0 * i for i in range(60)]
        rows = _compute_ema_signals(_make_result(closes))

        golden = [r for r in rows if r["cross"] == "golden"]
        death = [r for r in rows if r["cross"] == "death"]
        self.assertEqual(len(death), 1)
        self.assertEqual(len(golden), 0)
        self.assertLess(death[0]["ema20"], death[0]["ema50"])

    def test_only_last_store_days_rows_are_returned(self) -> None:
        closes = [100.0 + (i % 7) for i in range(250)]
        rows = _compute_ema_signals(_make_result(closes))
        self.assertEqual(len(rows), _STORE_DAYS)

    def test_short_series_returns_all_rows(self) -> None:
        closes = [100.0 + i for i in range(40)]
        rows = _compute_ema_signals(_make_result(closes))
        self.assertEqual(len(rows), 40)

    def test_error_result_returns_empty_list(self) -> None:
        self.assertEqual(_compute_ema_signals({"error": "no data", "symbol": "X"}), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_sme_ema_pipeline.py -v
```

Expected: ImportError (`_STORE_DAYS` doesn't exist yet) or assertion failures against the old row shape.

- [ ] **Step 3: Rewrite the detection logic in `sme_ema_pipeline.py`**

Update the module docstring's first paragraph to say: *"...downloads 1 year of daily OHLCV, computes EMA 20 and EMA 50, detects golden/death crosses (EMA20 crossing EMA50) and stores the last ~3 months in PostgreSQL."*

Replace the constants:

```python
_LOOKBACK_DAYS = 5
_OHLCV_PERIOD  = "1y"    # full year so EMA 50 is converged before the stored window
_STORE_DAYS    = 63      # ~3 months of trading days kept in the DB
_MAX_WORKERS   = 8
```

Replace `_compute_ema_signals` entirely:

```python
def _compute_ema_signals(result: dict) -> list[dict]:
    """
    Compute EMA 20/50 and golden/death cross flags for one stock.
    A golden cross fires when EMA20 crosses from <= EMA50 to > EMA50; death is the reverse.
    EMAs are computed over the full fetched series so EMA50 is converged;
    only the last _STORE_DAYS rows are returned for storage.
    """
    if "error" in result:
        return []

    symbol = result["symbol"]
    df = result["df"].copy()

    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()

    above = df["ema20"] > df["ema50"]
    prev_above = above.shift(1)
    golden = above & (prev_above == False)   # noqa: E712 — elementwise; NaN first row never flags
    death  = (~above) & (prev_above == True)  # noqa: E712
    df["cross"] = np.where(golden, "golden", np.where(death, "death", None))

    df = df.iloc[-_STORE_DAYS:]

    rows = []
    for idx, row in df.iterrows():
        trade_date = idx.date() if hasattr(idx, "date") else idx
        cross = row["cross"]
        rows.append({
            "symbol":      symbol,
            "trade_date":  trade_date,
            "close_price": _safe_float(row["Close"]),
            "ema20":       _safe_float(row["ema20"]),
            "ema50":       _safe_float(row["ema50"]),
            "cross":       None if (cross is None or pd.isna(cross)) else str(cross),
        })
    return rows
```

- [ ] **Step 4: Update the upsert SQL**

Replace `_upsert_signals`'s SQL (keep batching unchanged):

```python
                text("""
                    INSERT INTO ema_signals
                        (symbol, trade_date, close_price, ema20, ema50, cross_type, run_at)
                    VALUES
                        (:symbol, :trade_date, :close_price, :ema20, :ema50, :cross, NOW())
                    ON CONFLICT ON CONSTRAINT uq_ema_signals_symbol_date DO UPDATE SET
                        close_price = EXCLUDED.close_price,
                        ema20       = EXCLUDED.ema20,
                        ema50       = EXCLUDED.ema50,
                        cross_type  = EXCLUDED.cross_type,
                        run_at      = NOW()
                """),
```

- [ ] **Step 5: Update `_print_summary`**

Replace the query and row formatting:

```python
    query = text("""
        SELECT
            s.symbol,
            s.name,
            s.exchange,
            e.trade_date,
            e.close_price,
            e.cross_type,
            e.ema20,
            e.ema50
        FROM ema_signals e
        JOIN sme_stocks  s USING (symbol)
        WHERE e.cross_type IS NOT NULL
          AND e.trade_date >= CURRENT_DATE - (:lookback * INTERVAL '1 day')
        ORDER BY e.trade_date DESC, s.symbol
    """)
```

Header/table lines become:

```python
    print(f"  SME Stocks — EMA20/EMA50 Golden & Death Crosses (last {lookback_days} days)")
    ...
    hdr = f"{'Date':<12} {'Symbol':<16} {'Exch':<6} {'Cross':<10} {'Close':>9} {'EMA20':>9} {'EMA50':>9}"
```

and the row print drops the `crossed`/`direction` pair for a single field:

```python
        print(
            f"{str(row.trade_date):<12} "
            f"{row.symbol:<16} "
            f"{row.exchange:<6} "
            f"{(row.cross_type or ''):<10} "
            f"{float(row.close_price or 0):>9.2f} "
            f"{float(row.ema20 or 0):>9.2f} "
            f"{float(row.ema50 or 0):>9.2f}"
        )
```

Also update the empty-result message: `No golden/death crosses found in the last {lookback_days} days.` and the log line in `run()` Phase 3 to `"Phase 3 — Computing EMA20/EMA50 golden/death crosses..."`.

- [ ] **Step 6: Add `--reset-db`**

In `main()` add the argument and handling:

```python
    parser.add_argument("--reset-db", action="store_true",
                        help="Drop and recreate DB tables, then exit")
```

```python
    if args.reset_db:
        engine = get_engine()
        metadata.drop_all(engine)
        metadata.create_all(engine)
        logger.info("Database tables dropped and recreated")
        return
```

Update the usage block in the module docstring to list `--reset-db`.

- [ ] **Step 7: Run the tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_sme_ema_pipeline.py -v && python -m pytest tests/ -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add sme_ema_pipeline.py tests/test_sme_ema_pipeline.py
git commit -m "Detect EMA20/EMA50 golden and death crosses over 1y window"
```

---

### Task 5: API — new signals query, cached engine, refresh endpoint

**Files:**
- Modify: `api.py` (`get_sme_signals`, new `_get_sme_engine`, new `refresh_sme_signals`)

**Interfaces:**
- Consumes: `sme_ema_pipeline.run` (Task 4), `db.models.get_engine` (Task 3).
- Produces:
  - `GET /api/sme-signals?lookback=&direction=` → `{signals: [{symbol, name, exchange, trade_date, close_price, ema20, ema50, cross, in_golden_cross}], total_monitored, golden_now, last_run, refreshing}`
  - `POST /api/sme-signals/refresh` → `202 {"started": true}` or `409` when running / `503` when no `DATABASE_URL`.

- [ ] **Step 1: Add the cached engine and refreshing flag**

In `api.py`, below the `_PICKS_CACHE_*` block, add:

```python
# ── SME signals: shared engine + refresh state ───────────────────────────────
_SME_ENGINE = None
_SME_REFRESHING = False


def _get_sme_engine():
    global _SME_ENGINE
    if _SME_ENGINE is None:
        from db.models import get_engine
        _SME_ENGINE = get_engine()
    return _SME_ENGINE
```

- [ ] **Step 2: Rewrite `get_sme_signals`**

Replace the whole route with:

```python
@app.get("/api/sme-signals")
async def get_sme_signals(
    lookback:  int = Query(5, ge=1, le=30, description="Days back to check for crosses"),
    direction: str = Query("all", description="all | golden | death"),
):
    """Return SME stocks with an EMA20/EMA50 golden or death cross in the last N days."""
    import os
    from fastapi import HTTPException

    if direction not in ("all", "golden", "death"):
        raise HTTPException(status_code=422, detail="direction must be one of: all, golden, death")
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured. Run the SME pipeline first.")

    def _query_sync() -> dict:
        from sqlalchemy import text as _text

        engine = _get_sme_engine()
        with engine.connect() as conn:
            rows = conn.execute(_text("""
                WITH latest AS (
                    SELECT DISTINCT ON (symbol) symbol, (ema20 > ema50) AS in_golden_cross
                    FROM ema_signals
                    ORDER BY symbol, trade_date DESC
                )
                SELECT
                    s.symbol,
                    s.name,
                    s.exchange,
                    e.trade_date::text   AS trade_date,
                    e.close_price::float AS close_price,
                    e.ema20::float       AS ema20,
                    e.ema50::float       AS ema50,
                    e.cross_type         AS "cross",
                    COALESCE(l.in_golden_cross, FALSE) AS in_golden_cross
                FROM ema_signals e
                JOIN sme_stocks  s USING (symbol)
                LEFT JOIN latest l USING (symbol)
                WHERE e.cross_type IS NOT NULL
                  AND (:direction = 'all' OR e.cross_type = :direction)
                  AND e.trade_date >= CURRENT_DATE - (:lookback * INTERVAL '1 day')
                ORDER BY e.trade_date DESC, s.symbol
            """), {"lookback": lookback, "direction": direction}).mappings().fetchall()

            total_monitored = conn.execute(
                _text("SELECT COUNT(*) FROM sme_stocks")
            ).scalar() or 0

            golden_now = conn.execute(_text("""
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT ON (symbol) (ema20 > ema50) AS ig
                    FROM ema_signals
                    ORDER BY symbol, trade_date DESC
                ) t WHERE t.ig
            """)).scalar() or 0

            last_run = conn.execute(
                _text("SELECT MAX(run_at)::text FROM ema_signals")
            ).scalar()

        return {
            "signals":         [dict(r) for r in rows],
            "total_monitored": int(total_monitored),
            "golden_now":      int(golden_now),
            "last_run":        last_run,
            "refreshing":      _SME_REFRESHING,
        }

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _query_sync)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")
```

- [ ] **Step 3: Add the refresh endpoint**

Directly below `get_sme_signals`, add:

```python
@app.post("/api/sme-signals/refresh", status_code=202)
async def refresh_sme_signals():
    """Run the SME EMA pipeline in the background. 409 if a run is in progress."""
    import os
    from fastapi import HTTPException

    global _SME_REFRESHING
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")
    if _SME_REFRESHING:
        raise HTTPException(status_code=409, detail="A refresh is already running.")

    _SME_REFRESHING = True
    loop = asyncio.get_running_loop()

    def _run_pipeline():
        global _SME_REFRESHING
        try:
            from sme_ema_pipeline import run as run_sme_pipeline
            run_sme_pipeline()
        except Exception as exc:
            log_event(LOGGER, "sme_refresh_failed", level="error", error=str(exc))
        finally:
            _SME_REFRESHING = False

    async def _launch():
        await loop.run_in_executor(None, _run_pipeline)

    asyncio.create_task(_launch())
    log_event(LOGGER, "sme_refresh_started")
    return {"started": True}
```

(Note the `_launch` coroutine wrapper — per CLAUDE.md, never pass `run_in_executor` directly to `create_task`.)

- [ ] **Step 4: Verify the app imports and routes exist**

```bash
source .venv/bin/activate && python -c "
import api
routes = {getattr(r, 'path', '') for r in api.app.routes}
assert '/api/sme-signals' in routes and '/api/sme-signals/refresh' in routes, routes
print('routes ok')"
```

Expected: `routes ok`.

- [ ] **Step 5: Commit**

```bash
git add api.py
git commit -m "Serve golden/death cross signals; cache DB engine; add background refresh endpoint"
```

---

### Task 6: Frontend — types, refresh proxy, SME page rework

**Files:**
- Modify: `frontend/types/index.ts` (`SmeSignal`, `SmeSignalsResponse`)
- Create: `frontend/app/api/sme-signals/refresh/route.ts`
- Modify: `frontend/app/sme-signals/page.tsx`

**Interfaces:**
- Consumes: the Task 5 response shape.
- Produces: the SME page consuming `cross`/`in_golden_cross`/`golden_now`/`refreshing`.

- [ ] **Step 1: Update the SME types in `frontend/types/index.ts`**

Replace `SmeSignal` and `SmeSignalsResponse` with:

```typescript
export interface SmeSignal {
  symbol:          string;
  name:            string | null;
  exchange:        string;
  trade_date:      string;           // 'YYYY-MM-DD'
  close_price:     number | null;
  ema20:           number | null;
  ema50:           number | null;
  cross:           'golden' | 'death';
  in_golden_cross: boolean;
}

export interface SmeSignalsResponse {
  signals:          SmeSignal[];
  total_monitored:  number;
  golden_now:       number;          // stocks currently in golden-cross regime
  last_run:         string | null;   // ISO timestamp or null
  refreshing:       boolean;         // a pipeline refresh is running server-side
}
```

- [ ] **Step 2: Create the refresh proxy route**

Create `frontend/app/api/sme-signals/refresh/route.ts`:

```typescript
const API = process.env.API_URL ?? 'http://localhost:8000';

export async function POST() {
  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/sme-signals/refresh`, { method: 'POST', cache: 'no-store' });
  } catch {
    return Response.json(
      { error: 'Backend unavailable. Make sure the analysis service is running.' },
      { status: 503 },
    );
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
```

- [ ] **Step 3: Rework `frontend/app/sme-signals/page.tsx`**

Apply these changes (keep everything else — nav, header, skeleton, FilterChip — as is):

3a. Replace the filter types and remove `EmaFilter`:

```typescript
type Lookback  = 1 | 3 | 5 | 10;
type Direction = 'all' | 'golden' | 'death';
```

3b. Replace `DirectionBadge` and `CrossedBadge` with:

```tsx
function CrossBadge({ cross }: { cross: 'golden' | 'death' }) {
  return cross === 'golden' ? (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
      ⚡ Golden
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-sell/12 text-sell border-sell/25">
      💀 Death
    </span>
  );
}

function RegimeBadge({ inGolden }: { inGolden: boolean }) {
  return inGolden ? (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
      In Golden Cross
    </span>
  ) : (
    <span className="text-muted text-[10px]">—</span>
  );
}
```

3c. Replace state + fetch logic in the component body (drop `ema` state; add abort + refresh):

```tsx
  const [data,       setData]       = useState<SmeSignalsResponse | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState<string | null>(null);
  const [lookback,   setLookback]   = useState<Lookback>(5);
  const [direction,  setDirection]  = useState<Direction>('all');
  const [refreshing, setRefreshing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const fetchSignals = useCallback(async (lb: Lookback, dir: Direction, silent = false) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    if (!silent) setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ lookback: String(lb), direction: dir });
      const res = await fetch(`/api/sme-signals?${qs}`, { signal: ac.signal });
      const json = await res.json() as SmeSignalsResponse & { error?: string };
      if (!res.ok) {
        setError(json.error ?? `Error ${res.status}`);
        setData(null);
      } else {
        setData(json);
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      setError('Could not reach the backend. Is the server running?');
      setData(null);
    } finally {
      if (abortRef.current === ac && !silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSignals(lookback, direction);
  }, [lookback, direction, fetchSignals]);

  // Track server-side refresh state; poll while a refresh runs, reload when done.
  useEffect(() => {
    if (data) setRefreshing(data.refreshing);
  }, [data]);

  useEffect(() => {
    if (!refreshing) return;
    const t = setInterval(() => fetchSignals(lookback, direction, true), 10000);
    return () => clearInterval(t);
  }, [refreshing, lookback, direction, fetchSignals]);

  const startRefresh = useCallback(async () => {
    try {
      const res = await fetch('/api/sme-signals/refresh', { method: 'POST' });
      if (res.status === 202 || res.status === 409) setRefreshing(true);
    } catch {
      setError('Could not reach the backend. Is the server running?');
    }
  }, []);
```

Add `useRef` to the react import: `import { useState, useEffect, useCallback, useRef } from 'react';`

3d. In the nav bar, replace the existing refresh button with two buttons:

```tsx
          <div className="ml-auto flex items-center gap-3">
            <button
              onClick={startRefresh}
              disabled={refreshing}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-accent/40 text-accent
                         hover:bg-accent/10 transition-colors disabled:opacity-40"
            >
              {refreshing ? 'Refreshing data…' : '⟳ Refresh Data'}
            </button>
            <button
              onClick={() => fetchSignals(lookback, direction)}
              disabled={loading}
              className="text-xs text-muted hover:text-tx transition-colors disabled:opacity-40"
            >
              {loading ? 'Loading…' : '↺ Reload'}
            </button>
          </div>
```

3e. Replace the derived stats and stats strip entries:

```tsx
  const deathCount = signals.filter(s => s.cross === 'death').length;
```

(The old `bullishCount`/`bearishCount` lines are removed; only `deathCount` is needed by the stats strip.)

Stats array becomes:

```tsx
            { label: 'Stocks Monitored',    value: data?.total_monitored ?? '—',        color: 'text-tx',   sub: 'NSE Emerge + BSE SME' },
            { label: 'Crosses Found',       value: loading ? '—' : signals.length,      color: 'text-accent', sub: `last ${lookback} day${lookback > 1 ? 's' : ''}` },
            { label: 'In Golden Cross Now', value: data?.golden_now ?? '—',             color: 'text-buy',  sub: 'EMA20 above EMA50 today' },
            { label: 'Death Crosses',       value: loading ? '—' : deathCount,          color: 'text-sell', sub: `last ${lookback} day${lookback > 1 ? 's' : ''}` },
```

3f. Replace the Direction filter options and **delete the EMA filter block** (and its divider):

```tsx
                  { value: 'all',    label: 'All'      },
                  { value: 'golden', label: '⚡ Golden' },
                  { value: 'death',  label: '💀 Death'  },
```

3g. Update header copy: badge text `NSE Emerge · BSE SME · Golden Cross Screener`, `<h1>` to `SME Golden <span className="text-accent">Cross</span> Signals`, and the description sentence to *"SME-listed stocks (NSE Emerge + BSE SME) whose EMA 20 crossed their EMA 50 (golden/death cross) in the selected window."*

3h. Table header: use literal alignment classes (fixes the dynamic-class bug) and the new columns:

```tsx
                    {[
                      { label: 'Symbol',    cls: 'text-left'  },
                      { label: 'Company',   cls: 'text-left'  },
                      { label: 'Cross Date', cls: 'text-left' },
                      { label: 'Cross',     cls: 'text-left'  },
                      { label: 'Regime',    cls: 'text-left'  },
                      { label: 'Close',     cls: 'text-right' },
                      { label: 'EMA 20',    cls: 'text-right' },
                      { label: 'EMA 50',    cls: 'text-right' },
                    ].map(({ label, cls }) => (
                      <th
                        key={label}
                        className={`px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider ${cls}`}
                      >
                        {label}
                      </th>
                    ))}
```

3i. Table body rows: symbol cell links only for NSE; Cross/Regime cells use the new badges; EMA cells lose the per-EMA highlight (both render `text-muted`, close stays `text-tx`):

```tsx
                        {/* Symbol */}
                        <td className="px-4 py-4">
                          <div className="flex items-center gap-1.5">
                            {s.exchange === 'NSE' ? (
                              <Link
                                href={`/?symbol=${s.symbol}`}
                                className="font-semibold text-tx hover:text-accent transition-colors text-sm"
                              >
                                {s.symbol}
                              </Link>
                            ) : (
                              <span className="font-semibold text-tx text-sm">{s.symbol}</span>
                            )}
                            <ExchangeBadge exchange={s.exchange} />
                          </div>
                        </td>
```

```tsx
                        {/* Cross */}
                        <td className="px-4 py-4">
                          <CrossBadge cross={s.cross} />
                        </td>

                        {/* Regime */}
                        <td className="px-4 py-4">
                          <RegimeBadge inGolden={s.in_golden_cross} />
                        </td>
```

3j. Footer hint: *"Click an NSE symbol to run full analysis. BSE SME symbols are scrip codes and can't be analysed directly."*

- [ ] **Step 4: Type-check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: exit 0. Fix any errors within the files touched by this task only.

- [ ] **Step 5: Commit**

```bash
git add frontend/types/index.ts frontend/app/api/sme-signals/refresh/route.ts frontend/app/sme-signals/page.tsx
git commit -m "SME page: golden/death cross UI, regime badges, refresh button, literal align classes"
```

---

### Task 7: Home page `?symbol=` deep link

**Files:**
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: existing `handleAnalyse(symbol: string, force?: boolean)` in the component.
- Produces: `/?symbol=FOO` auto-starts analysis for `FOO`.

- [ ] **Step 1: Wrap the page in Suspense and read the param**

In `frontend/app/page.tsx`:

- Change the react import to `import { useState, useRef, useCallback, useEffect, Suspense } from 'react';`
- Add `import { useSearchParams } from 'next/navigation';`
- Rename the existing `export default function HomePage()` to `function HomePageInner()`.
- Inside `HomePageInner`, after `handleAnalyse` is defined, add:

```tsx
  const searchParams = useSearchParams();
  const deepLinkDone = useRef(false);

  // Deep link: /?symbol=TCS auto-starts analysis (used by SME signals page links)
  useEffect(() => {
    const sym = searchParams.get('symbol');
    if (sym && !deepLinkDone.current) {
      deepLinkDone.current = true;
      handleAnalyse(sym.toUpperCase());
    }
  }, [searchParams, handleAnalyse]);
```

- At the bottom of the file, add the new default export (Next 15 requires a Suspense boundary around `useSearchParams`):

```tsx
export default function HomePage() {
  return (
    <Suspense fallback={null}>
      <HomePageInner />
    </Suspense>
  );
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "Support ?symbol= deep links on the home page"
```

---

### Task 8: Documentation (CLAUDE.md + cron)

**Files:**
- Modify: `CLAUDE.md` (repo-level, `/Users/luckyratanlaljain/project/stock-research/CLAUDE.md`)

- [ ] **Step 1: Add an SME pipeline section**

In CLAUDE.md, after the "Market picks flow" subsection under **Agent Orchestration**, add:

```markdown
### SME golden cross flow

`sme_ema_pipeline.py` is a standalone batch job (PostgreSQL, `DATABASE_URL` env var):

1. Fetches all NSE Emerge + BSE SME stocks (`tools/sme_tools.py`, 24 h list cache)
2. Downloads 1 year of daily OHLCV per stock via yfinance
3. Computes EMA 20/50 over the full year; flags **golden crosses** (EMA20 crosses above
   EMA50) and **death crosses** (crosses below); stores only the last ~3 months of rows
4. `GET /api/sme-signals` serves cross events + current regime (`ema20 > ema50` on the
   latest row); `POST /api/sme-signals/refresh` runs the pipeline in the background
   (409 if already running; `refreshing` flag in the GET response)

CLI: `--setup-db` (create tables), `--reset-db` (drop + recreate — required after schema
changes; data is fully regenerable), `--force` (bypass list cache), `--lookback N`.

The DB column for the cross is named `cross_type` (`'golden'`/`'death'`/`NULL`) because
`CROSS` is a reserved SQL keyword; the API/TS field is `cross`.

Daily auto-run (crontab, assumes system TZ is IST; NSE closes 15:30):

    30 18 * * 1-5 cd /Users/luckyratanlaljain/project/stock-research && .venv/bin/python sme_ema_pipeline.py >> output/sme_cron.log 2>&1
```

- [ ] **Step 2: Update the repo-structure tree**

In the CLAUDE.md repo-structure block, add these lines (keep tree formatting):

```
├── sme_ema_pipeline.py     SME golden/death cross batch pipeline (PostgreSQL)
├── db/                     SQLAlchemy Core tables (models.py) + schema.sql reference
```

and under `tools/`: `│   ├── sme_tools.py           NSE Emerge + BSE SME stock-list fetchers`

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Document SME golden cross pipeline, refresh endpoint, and cron schedule"
```

---

### Task 9: End-to-end verification (manual, needs local Postgres)

**Files:** none (verification only)

- [ ] **Step 1: Reset and repopulate the DB**

```bash
source .venv/bin/activate
python sme_ema_pipeline.py --reset-db
python sme_ema_pipeline.py
```

Expected: phases 1–5 log; summary table prints golden/death crosses (or a clean "none found" message). Takes several minutes (~600 stocks × yfinance).

- [ ] **Step 2: Verify the API**

```bash
uvicorn api:app --port 8000 &   # or use the already-running dev server
curl -s 'http://localhost:8000/api/sme-signals?lookback=10&direction=golden' | python -m json.tool | head -40
curl -s -X POST 'http://localhost:8000/api/sme-signals/refresh' -o /dev/null -w '%{http_code}\n'
curl -s -X POST 'http://localhost:8000/api/sme-signals/refresh' -o /dev/null -w '%{http_code}\n'
```

Expected: JSON with `signals[].cross == "golden"`, `golden_now`, `refreshing`; first POST → `202`, immediate second POST → `409`.

- [ ] **Step 3: Verify the UI**

```bash
cd frontend && npm run dev
```

Open `http://localhost:3000/sme-signals`: stats strip shows "In Golden Cross Now"; filters work; Refresh Data button shows "Refreshing data…" and the table reloads when the run finishes. Click an NSE symbol → home page auto-starts analysis (deep link). BSE rows are not links.

- [ ] **Step 4: Full gates one last time**

```bash
source .venv/bin/activate && python -m pytest tests/ -q
cd frontend && npx tsc --noEmit
```

Expected: all pass. Report results to the user; no commit (nothing changed).
