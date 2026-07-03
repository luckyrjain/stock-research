# SME Golden Cross Screener + Audit Fixes — Design

**Date:** 2026-07-03
**Branch:** `feature/market-picks-enhancements`
**Status:** Approved by user (pending final spec review)

## Goal

Replace the SME pipeline's price-vs-EMA crossing detection with true moving-average
crossover detection (EMA20 vs EMA50 — golden cross / death cross), surface both recent
cross events and current regime state in the UI, and fix a set of bugs found in a
codebase audit: a dead analyst guardrail, stale tests that can make real LLM calls,
dead `/?symbol=` deep links, a per-request DB engine leak, and missing pipeline
scheduling.

## Out of scope

- SME ISIN dedup no-op in `tools/sme_tools.py` (documented behavior mismatch; separate task)
- Market picks pipeline changes
- CI workflow setup
- Broken `frontend/node_modules` is an environment repair (`npm install`), not a code change,
  but it is a prerequisite for verification

---

## 1. Signal logic (`sme_ema_pipeline.py`)

**Detection change.** A **golden cross** fires on the day EMA20 crosses from
`<= EMA50` to `> EMA50`; a **death cross** fires on the reverse. All
price-vs-EMA20/EMA50 crossing logic in `_compute_ema_signals` is deleted.

**Data window.** `_OHLCV_PERIOD` changes from `"3mo"` to `"1y"`. EMAs are computed
over the full ~250-bar series so EMA50 is properly converged, but only the last
~3 months (63 trading days) of rows are stored — same DB footprint as today,
without initialization artifacts near the series start.

**Current regime is derived, not stored.** "In golden cross now" means
`ema20 > ema50` on the stock's latest stored row. No extra column; cannot go stale.

**Row shape** produced by `_compute_ema_signals`:
`{symbol, trade_date, close_price, ema20, ema50, cross}` where
`cross ∈ {'golden', 'death', None}`.

## 2. Database (`db/models.py`)

`ema_signals` changes:

- **Drop** columns: `crossed_ema20`, `crossed_ema50`, `cross_direction`
- **Add** column: `cross VARCHAR(10)` (`'golden'` / `'death'` / `NULL`)
- Keep: `id`, `symbol` (FK), `trade_date`, `close_price`, `ema20`, `ema50`, `run_at`,
  unique constraint `uq_ema_signals_symbol_date`, date index
- Replace `idx_ema_signals_cross` (on the dropped booleans) with an index on `cross`

**No migration.** Data is fully regenerable from yfinance. Add a `--reset-db` CLI flag
(`metadata.drop_all()` then `create_all()`); the user runs
`python sme_ema_pipeline.py --reset-db && python sme_ema_pipeline.py` once.
`db/schema.sql` is updated to match.

## 3. API (`GET /api/sme-signals` in `api.py`)

**Query params:**

- `lookback` — unchanged (1–30, default 5)
- `direction` — becomes `all | golden | death` (replaces `all | bullish | bearish`)
- `ema` param — **removed**

**Response:**

```json
{
  "signals": [
    {"symbol": "...", "name": "...", "exchange": "NSE",
     "trade_date": "...", "close_price": 0, "ema20": 0, "ema50": 0,
     "cross": "golden", "in_golden_cross": true}
  ],
  "total_monitored": 0,
  "golden_now": 0,
  "last_run": "...",
  "refreshing": false
}
```

- `in_golden_cross` — the stock's *current* regime, from its latest stored row
- `golden_now` — count of all monitored stocks currently in golden-cross regime
- `refreshing` — see §9

**Bug fixes in the same function:**

- Module-level cached engine (create once, reuse) instead of `create_engine()` per
  request — fixes the connection-pool leak. Reuse `db.models.get_engine` with a
  lazy singleton.
- `direction` filter becomes a bound SQL parameter instead of an f-string interpolation.

## 4. Frontend (`frontend/app/sme-signals/page.tsx`, `frontend/types/index.ts`)

- **Filters:** Period chips unchanged (1/3/5/10d). Direction chips become
  All / ⚡ Golden / 💀 Death (buy/sell design tokens per `design.md`). The EMA 20/50
  chip row is removed.
- **Table columns:** Symbol · Company · Cross Date · Cross badge (golden/death) ·
  Regime badge (in / out of golden cross now) · Close · EMA20 · EMA50.
- **Stats strip:** Stocks Monitored · Crosses Found (window) · In Golden Cross Now
  (`golden_now`) · Death Crosses (window).
- **Symbol links:** only NSE rows link to analysis; BSE rows (numeric scrip codes the
  NSE-centric analysis flow cannot handle) render as plain text.
- **Bug fix:** replace the dynamic `text-${align}` header class with literal
  `text-left` / `text-right` classes (Tailwind JIT cannot see composed class names).
- `SmeSignal` / `SmeSignalsResponse` types in `frontend/types/index.ts` updated to the
  new shape.
- Fetch race: pass an `AbortController` signal and cancel the in-flight request when
  filters change.

## 5. Verification

1. `cd frontend && npm install` (repairs broken node_modules), then `npx tsc --noEmit`
   must pass.
2. New unit test for cross detection on a synthetic series (no network, no DB): a price
   series engineered so EMA20 crosses EMA50 at a known index → exactly one golden cross
   flagged on that date; reversed series → death cross.
3. Manual: `python sme_ema_pipeline.py --reset-db && python sme_ema_pipeline.py`
   against local Postgres; summary prints golden/death crosses; SME page renders them.
4. `python -m pytest tests/` passes (see §7).

## 6. Guardrail restore + retry (`crew.py`)

- **Indentation bug fix** in `_validate_analysis_payload`: the `bull_factors` /
  `bear_factors` / `key_risks` length checks move out of the `for field` loop (they
  currently run 7× redundantly).
- **Restore grounded-claims checks** in `_analysis_support_issues`: for each of the
  three checks (regulatory risk, customer-concentration risk, competition risk), when
  the analysis text contains a trigger phrase but the source text contains none of the
  matching source terms, append the issue label. The current loop body
  (`continue` under a "downgrade instead of fail" comment) is dead code and is replaced.
- **Guardrail retry** in `run_analysis_with_fallback`: on guardrail failure, re-call the
  LLM **once** with the validation error appended to the prompt
  ("Your previous response failed validation: <error>. Return only the corrected JSON
  object."). Fall back to `_safe_analysis_fallback` only if the retry also fails
  validation. The existing rate-limit retry (one retry with parsed wait) is unchanged
  and independent.

## 7. Test fixes (`tests/test_analysis_guardrails.py`, `requirements.txt`)

- `test_invalid_structured_analysis_falls_back_safely` is rewritten to mock
  `litellm.completion` (the current version patches `build_crew` / `_resolve_llm`,
  which the refactored code no longer calls — so today the test attempts a **real**
  LLM call and only fails safe because no API key is set).
- `test_validate_analysis_payload_rejects_unsupported_regulatory_risk` passes once §6
  restores the check — no test change.
- **New test:** first LLM response fails the guardrail, corrective retry returns a valid
  payload → the valid payload is returned (verifies retry path, `litellm.completion`
  mocked with `side_effect`).
- Add `pytest` to `requirements.txt` so the documented `python -m pytest tests/`
  command works.

## 8. `/?symbol=` deep links (`frontend/app/page.tsx`)

The home page reads the `symbol` query param via `useSearchParams` (wrapped in a
`<Suspense>` boundary per Next 15 requirements) and auto-starts analysis for that
symbol on mount. This makes SME-page symbol links (and any external deep link) work.

## 9. Pipeline scheduling (`api.py`, SME page, CLAUDE.md)

- **`POST /api/sme-signals/refresh`** — kicks off `sme_ema_pipeline.run()` in a
  background executor. A module-level flag guards concurrency: returns
  `202 {"started": true}` when started, `409` when a run is already in progress.
  The flag is exposed as `refreshing` in the GET response.
- **Frontend proxy route** `frontend/app/api/sme-signals/refresh/route.ts` (POST,
  same thin-proxy pattern as existing routes).
- **Refresh Data button** on the SME page: fires the POST, shows a running state,
  polls the GET endpoint (~10s interval) until `refreshing` is false, then reloads
  the table.
- **Cron:** documented crontab entry in CLAUDE.md for weekday runs after NSE close:
  `30 18 * * 1-5 cd <repo> && .venv/bin/python sme_ema_pipeline.py >> output/sme_cron.log 2>&1`
  (18:30 IST assumes system TZ is IST; noted in the doc).

## Component summary

| File | Change |
|---|---|
| `sme_ema_pipeline.py` | Golden/death cross detection, 1y fetch window, 3mo storage, `--reset-db` |
| `db/models.py`, `db/schema.sql` | `cross` column replaces the three old cross columns |
| `api.py` | New signal query + `golden_now`/`in_golden_cross`/`refreshing`, cached engine, bound params, refresh endpoint |
| `crew.py` | Indentation fix, restored grounded checks, guardrail retry |
| `tests/` | Fixed litellm mocking, new retry test, new cross-detection test |
| `requirements.txt` | + `pytest` |
| `frontend/app/sme-signals/page.tsx` | New filters/columns/stats, refresh button, literal align classes, abortable fetch |
| `frontend/app/page.tsx` | `?symbol=` deep link support |
| `frontend/app/api/sme-signals/refresh/route.ts` | New proxy route |
| `frontend/types/index.ts` | Updated SME types |
| `CLAUDE.md` | SME section: new semantics, cron entry |
