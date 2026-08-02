# Track Record Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute realized hit-rate per recommendation tier (BUY/WATCHLIST/HOLD/SELL) by joining past market-picks history snapshots against the EOD price store, and surface it via API + a panel on `/market-picks`.

**Architecture:** Extend the existing `output/_history/*.json` snapshot with the fields already computed at pick time (`recommendation`, `entry_price`, `target_price`, `stop_loss`). A new standalone module `track_record.py` reads those files, looks up realized prices from `prices_daily.adj_close` (Postgres, via SQLAlchemy Core), and returns hit-rate/avg-return aggregates per tier. A new FastAPI endpoint exposes it; a new frontend panel renders it on the existing market-picks page.

**Tech Stack:** Python 3.13, SQLAlchemy Core (existing `db/models.py` tables), FastAPI, Next.js 15 / React 19 / TypeScript.

## Global Constraints

- Tools/pipeline functions must never raise — return empty/skip on error, matching repo convention (see `CLAUDE.md`).
- `prices_daily.adj_close` is the only field to use for return math (`close` is unadjusted raw close).
- Tests use `unittest`, collected by `pytest`. DB-dependent tests use `create_engine("sqlite://")` + `metadata.create_all(engine, tables=[...])` (see `tests/test_sme_ema_pipeline.py:58-60`), never a real Postgres connection.
- No new dependencies — SQLAlchemy, FastAPI, React are already in the stack.
- Run `cd frontend && npx tsc --noEmit` before considering the frontend task done (no other frontend check exists).

---

### Task 1: Extend history snapshot with recommendation + prices

**Files:**
- Modify: `market_picks_pipeline.py:274-289` (`_save_history`)
- Test: `tests/test_market_picks_history.py` (new)

**Interfaces:**
- Consumes: existing `picks` list passed into `_save_history(picks: list[dict])` — each `p` already has `p["recommendation"]`, `p["entry_price"]`, `p["target_price"]`, `p["stop_loss"]` set in `_phase_score` (`market_picks_pipeline.py:1206-1219`).
- Produces: `output/_history/YYYY-MM-DD.json` snapshot entries now include `recommendation`, `entry_price`, `target_price`, `stop_loss` keys (in addition to the existing `symbol`, `confidence`, `effective_signal`, `mention_count`). Task 2 reads these exact key names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_market_picks_history.py
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import market_picks_pipeline as mpp


class SaveHistoryTest(unittest.TestCase):
    def test_snapshot_includes_recommendation_and_prices(self) -> None:
        pick = {
            "symbol": "TESTCO",
            "confidence_score": 72.1,
            "mention_count": 3,
            "recommendation": "BUY",
            "entry_price": 100.0,
            "target_price": 115.0,
            "stop_loss": 92.0,
            "_sources_raw": [],
        }
        with TemporaryDirectory() as tmp:
            history_dir = Path(tmp) / "_history"
            orig_dir = mpp._HISTORY_DIR
            mpp._HISTORY_DIR = history_dir
            try:
                mpp._save_history([pick])
                files = list(history_dir.glob("*.json"))
                self.assertEqual(len(files), 1)
                data = json.loads(files[0].read_text())
                snap = data["picks"][0]
                self.assertEqual(snap["recommendation"], "BUY")
                self.assertEqual(snap["entry_price"], 100.0)
                self.assertEqual(snap["target_price"], 115.0)
                self.assertEqual(snap["stop_loss"], 92.0)
            finally:
                mpp._HISTORY_DIR = orig_dir


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_market_picks_history.py -v`
Expected: FAIL — `KeyError: 'recommendation'` (snapshot dict has no such key yet).

- [ ] **Step 3: Implement the schema change**

In `market_picks_pipeline.py`, find `_save_history` (currently around line 274-289):

```python
def _save_history(picks: list[dict]) -> None:
    """Append today's pick snapshot to output/_history/YYYY-MM-DD.json."""
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = _HISTORY_DIR / f"{date_str}.json"
    snapshot = [
        {
            "symbol":           p["symbol"],
            "confidence":       p["confidence_score"],
            "effective_signal": round(_effective_signal(p.get("_sources_raw", [])), 3),
            "mention_count":    p["mention_count"],
        }
        for p in picks
    ]
    try:
        path.write_text(json.dumps({"date": date_str, "picks": snapshot}))
    except Exception:
        pass
```

Replace the `snapshot = [...]` block with:

```python
    snapshot = [
        {
            "symbol":           p["symbol"],
            "confidence":       p["confidence_score"],
            "effective_signal": round(_effective_signal(p.get("_sources_raw", [])), 3),
            "mention_count":    p["mention_count"],
            "recommendation":   p.get("recommendation"),
            "entry_price":      p.get("entry_price"),
            "target_price":     p.get("target_price"),
            "stop_loss":        p.get("stop_loss"),
        }
        for p in picks
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_market_picks_history.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add market_picks_pipeline.py tests/test_market_picks_history.py
git commit -m "$(cat <<'EOF'
feat: store recommendation + trade levels in market-picks history snapshot

Adds recommendation/entry_price/target_price/stop_loss to the daily
history snapshot so a future track-record engine can compute realized
returns per call. Old snapshot files lack these fields by design —
consumers must treat them as optional.
EOF
)"
```

---

### Task 2: `track_record.py` — core calibration module

**Files:**
- Create: `track_record.py`
- Test: `tests/test_track_record.py` (new)

**Interfaces:**
- Consumes: `db.models.prices_daily` table (columns `symbol`, `trade_date`, `adj_close` — see `db/models.py:125-143`); `output/_history/*.json` files shaped `{"date": "YYYY-MM-DD", "picks": [{"symbol", "recommendation", "entry_price", ...}]}` (Task 1's output).
- Produces: `compute_track_record(engine, horizon_days: int = 30, history_dir: Path | None = None, today: date | None = None) -> dict` returning:
  ```json
  {
    "horizon_days": 30,
    "as_of": "2026-07-11",
    "tiers": {"BUY": {"count": int, "hit_rate": float|None, "avg_return_pct": float|None}, "WATCHLIST": {...}, "HOLD": {...}, "SELL": {...}},
    "picks": [{"symbol": str, "date": str, "recommendation": str, "entry_price": float, "realized_return_pct": float, "hit": bool}]
  }
  ```
  Task 3 (API endpoint) calls this function directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_track_record.py
import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, insert

from db.models import metadata, prices_daily
from track_record import compute_track_record, _hit


class HitRuleTest(unittest.TestCase):
    def test_buy_hit_on_positive_return(self) -> None:
        self.assertTrue(_hit("BUY", 5.0))
        self.assertFalse(_hit("BUY", -1.0))

    def test_watchlist_hit_on_positive_return(self) -> None:
        self.assertTrue(_hit("WATCHLIST", 0.1))
        self.assertFalse(_hit("WATCHLIST", 0.0))

    def test_sell_hit_on_negative_return(self) -> None:
        self.assertTrue(_hit("SELL", -3.0))
        self.assertFalse(_hit("SELL", 3.0))

    def test_hold_hit_within_band(self) -> None:
        self.assertTrue(_hit("HOLD", 4.9))
        self.assertTrue(_hit("HOLD", -4.9))
        self.assertFalse(_hit("HOLD", 5.1))


class ComputeTrackRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine, tables=[prices_daily])
        self.tmpdir = TemporaryDirectory()
        self.history_dir = Path(self.tmpdir.name)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _insert_price(self, symbol: str, trade_date: date, adj_close: float) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(prices_daily).values(
                symbol=symbol, trade_date=trade_date, adj_close=adj_close,
            ))

    def _write_snapshot(self, snap_date: date, picks: list[dict]) -> None:
        path = self.history_dir / f"{snap_date.isoformat()}.json"
        path.write_text(json.dumps({"date": snap_date.isoformat(), "picks": picks}))

    def test_buy_pick_that_rose_is_a_hit(self) -> None:
        snap_date = date(2026, 1, 1)
        self._write_snapshot(snap_date, [{
            "symbol": "TESTCO", "recommendation": "BUY", "entry_price": 100.0,
        }])
        self._insert_price("TESTCO", snap_date + timedelta(days=30), 110.0)

        result = compute_track_record(
            self.engine, horizon_days=30, history_dir=self.history_dir,
            today=date(2026, 3, 1),
        )
        self.assertEqual(result["tiers"]["BUY"]["count"], 1)
        self.assertEqual(result["tiers"]["BUY"]["hit_rate"], 1.0)
        self.assertAlmostEqual(result["tiers"]["BUY"]["avg_return_pct"], 10.0)
        self.assertEqual(result["picks"][0]["symbol"], "TESTCO")
        self.assertTrue(result["picks"][0]["hit"])

    def test_pick_missing_price_data_is_skipped(self) -> None:
        snap_date = date(2026, 1, 1)
        self._write_snapshot(snap_date, [{
            "symbol": "NODATA", "recommendation": "BUY", "entry_price": 100.0,
        }])
        # No price row inserted for NODATA at all.

        result = compute_track_record(
            self.engine, horizon_days=30, history_dir=self.history_dir,
            today=date(2026, 3, 1),
        )
        self.assertEqual(result["tiers"]["BUY"]["count"], 0)
        self.assertIsNone(result["tiers"]["BUY"]["hit_rate"])
        self.assertEqual(result["picks"], [])

    def test_pick_without_recommendation_field_is_skipped(self) -> None:
        snap_date = date(2026, 1, 1)
        self._write_snapshot(snap_date, [{
            "symbol": "OLDFMT", "confidence": 50.0,  # pre-Task-1 snapshot shape
        }])

        result = compute_track_record(
            self.engine, horizon_days=30, history_dir=self.history_dir,
            today=date(2026, 3, 1),
        )
        self.assertEqual(result["picks"], [])

    def test_snapshot_too_recent_for_horizon_is_excluded(self) -> None:
        snap_date = date(2026, 2, 20)  # only 9 days before "today" 2026-03-01
        self._write_snapshot(snap_date, [{
            "symbol": "TESTCO", "recommendation": "BUY", "entry_price": 100.0,
        }])
        self._insert_price("TESTCO", snap_date + timedelta(days=30), 110.0)

        result = compute_track_record(
            self.engine, horizon_days=30, history_dir=self.history_dir,
            today=date(2026, 3, 1),
        )
        self.assertEqual(result["tiers"]["BUY"]["count"], 0)

    def test_no_history_dir_returns_empty_tiers(self) -> None:
        result = compute_track_record(
            self.engine, horizon_days=30,
            history_dir=self.history_dir / "does-not-exist",
            today=date(2026, 3, 1),
        )
        for tier in ("BUY", "WATCHLIST", "HOLD", "SELL"):
            self.assertEqual(result["tiers"][tier]["count"], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_track_record.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'track_record'`

- [ ] **Step 3: Implement `track_record.py`**

```python
"""Track record engine: joins market-picks history snapshots against the
EOD price store to compute realized hit-rate per recommendation tier.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from db.models import prices_daily

_HOLD_BAND_PCT = 5.0
_VALID_TIERS = ("BUY", "WATCHLIST", "HOLD", "SELL")
_PRICE_LOOKUP_WINDOW_DAYS = 10


def _hit(recommendation: str, return_pct: float) -> bool:
    if recommendation in ("BUY", "WATCHLIST"):
        return return_pct > 0
    if recommendation == "SELL":
        return return_pct < 0
    return abs(return_pct) <= _HOLD_BAND_PCT  # HOLD


def _load_snapshots(history_dir: Path, cutoff: date) -> list[dict]:
    """Load picks from history files dated on/before cutoff, tagging each
    with its snapshot date. Skips unreadable files and picks that predate
    the recommendation/entry_price fields.
    """
    picks: list[dict] = []
    if not history_dir.exists():
        return picks
    for path in sorted(history_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            snap_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        if snap_date > cutoff:
            continue
        for p in data.get("picks", []):
            if p.get("recommendation") not in _VALID_TIERS:
                continue
            if not p.get("entry_price"):
                continue
            picks.append({**p, "_snapshot_date": snap_date})
    return picks


def _price_on_or_after(engine, symbol: str, target: date) -> float | None:
    """Nearest adj_close on or after target, within a short search window."""
    with engine.connect() as conn:
        row = conn.execute(
            select(prices_daily.c.adj_close)
            .where(prices_daily.c.symbol == symbol)
            .where(prices_daily.c.trade_date >= target)
            .where(prices_daily.c.trade_date <= target + timedelta(days=_PRICE_LOOKUP_WINDOW_DAYS))
            .order_by(prices_daily.c.trade_date.asc())
            .limit(1)
        ).first()
    return float(row[0]) if row and row[0] is not None else None


def compute_track_record(
    engine,
    horizon_days: int = 30,
    history_dir: Path | None = None,
    today: date | None = None,
) -> dict:
    """Realized hit-rate and avg return per recommendation tier, at a given horizon."""
    history_dir = history_dir or Path("output/_history")
    today = today or date.today()
    cutoff = today - timedelta(days=horizon_days)

    snapshots = _load_snapshots(history_dir, cutoff)

    tiers = {t: {"count": 0, "hits": 0, "return_sum": 0.0} for t in _VALID_TIERS}
    detail = []

    for p in snapshots:
        entry = float(p["entry_price"])
        if entry <= 0:
            continue
        target_date = p["_snapshot_date"] + timedelta(days=horizon_days)
        price_then = _price_on_or_after(engine, p["symbol"], target_date)
        if price_then is None:
            continue

        return_pct = (price_then - entry) / entry * 100
        rec = p["recommendation"]
        hit = _hit(rec, return_pct)

        tiers[rec]["count"] += 1
        tiers[rec]["hits"] += int(hit)
        tiers[rec]["return_sum"] += return_pct

        detail.append({
            "symbol":               p["symbol"],
            "date":                 p["_snapshot_date"].isoformat(),
            "recommendation":       rec,
            "entry_price":          entry,
            "realized_return_pct":  round(return_pct, 2),
            "hit":                  hit,
        })

    tier_summary = {}
    for t, agg in tiers.items():
        count = agg["count"]
        tier_summary[t] = {
            "count":          count,
            "hit_rate":       round(agg["hits"] / count, 3) if count else None,
            "avg_return_pct": round(agg["return_sum"] / count, 2) if count else None,
        }

    return {
        "horizon_days": horizon_days,
        "as_of":        today.isoformat(),
        "tiers":        tier_summary,
        "picks":        detail,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_track_record.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add track_record.py tests/test_track_record.py
git commit -m "$(cat <<'EOF'
feat: add track record engine — realized hit-rate per recommendation tier

Joins market-picks history snapshots against prices_daily.adj_close to
compute whether each past BUY/WATCHLIST/HOLD/SELL call actually played out,
at a configurable horizon. Foundation for validating (and later tuning)
the signal engine's weights against real outcomes.
EOF
)"
```

---

### Task 3: `GET /api/track-record` endpoint

**Files:**
- Modify: `api.py` (insert after the `/api/prices` endpoint, before `/api/sme-signals` — currently lines 666-690)

**Interfaces:**
- Consumes: `compute_track_record(engine, horizon_days: int) -> dict` from Task 2; `_get_sme_engine()` (`api.py:54-59`, a shared `db.models.get_engine()` wrapper — reused here despite the SME-specific name, it just returns the configured Postgres engine).
- Produces: `GET /api/track-record?horizon=<int>` returning the same JSON shape as `compute_track_record`. Task 4 (frontend) consumes this exact shape.

- [ ] **Step 1: Add the endpoint**

In `api.py`, insert immediately after the `/api/prices` endpoint (after the line `return {"prices": dict(results)}`, before `@app.get("/api/sme-signals")`):

```python
@app.get("/api/track-record")
async def get_track_record(horizon: int = Query(30, ge=7, le=180)):
    """Return realized hit-rate per recommendation tier from market-picks history."""
    import os
    from fastapi import HTTPException

    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")

    def _compute_sync() -> dict:
        from track_record import compute_track_record
        return compute_track_record(_get_sme_engine(), horizon_days=horizon)

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _compute_sync)
    except Exception as exc:
        log_event(LOGGER, "track_record_query_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")
```

- [ ] **Step 2: Verify manually**

There is no existing automated test harness for `api.py`'s endpoints directly (same is true of the pre-existing `/api/sme-signals` — it's only covered indirectly). Verify by hand:

```bash
source .venv/bin/activate
uvicorn api:app --reload --port 8000 &
sleep 2
curl -s "http://localhost:8000/api/track-record?horizon=30" | python3 -m json.tool
```

Expected (assuming `DATABASE_URL` set and at least one history file older than 30 days exists): a JSON object with `horizon_days`, `as_of`, `tiers` (BUY/WATCHLIST/HOLD/SELL keys), `picks`. With no qualifying history yet, `tiers.*.count` will all be `0` and `hit_rate`/`avg_return_pct` will be `null` — this is expected, not an error.

If `DATABASE_URL` is unset, expect a `503` with `"DATABASE_URL not configured."`.

Kill the server afterward: `kill %1` (or the relevant background job).

- [ ] **Step 3: Commit**

```bash
git add api.py
git commit -m "$(cat <<'EOF'
feat: expose GET /api/track-record endpoint

Thin wrapper around track_record.compute_track_record, following the
existing /api/sme-signals pattern (shared engine getter, 503 on missing
DATABASE_URL or query failure).
EOF
)"
```

---

### Task 4: Frontend — types, proxy route, and panel on `/market-picks`

**Files:**
- Modify: `frontend/types/index.ts` (insert after `MarketPicksPhase`, currently lines 133-134)
- Create: `frontend/app/api/track-record/route.ts`
- Create: `frontend/components/track-record-panel.tsx`
- Modify: `frontend/app/market-picks/page.tsx:655-663` (render the new panel)

**Interfaces:**
- Consumes: `GET /api/track-record?horizon=30` (Task 3's exact response shape) via the new proxy route.
- Produces: `<TrackRecordPanel />` — a self-contained component with no props (fetches its own data), rendered inside the existing `phase === 'done' && picks.length > 0` block.

- [ ] **Step 1: Add TypeScript types**

In `frontend/types/index.ts`, after the `MarketPicksPhase` type (currently ending at line 134) and before the `// ── SME EMA Signals ──` comment, insert:

```typescript
// ── Track Record ─────────────────────────────────────────────────────────────

export interface TrackRecordTier {
  count: number;
  hit_rate: number | null;
  avg_return_pct: number | null;
}

export interface TrackRecordPick {
  symbol: string;
  date: string;
  recommendation: string;
  entry_price: number;
  realized_return_pct: number;
  hit: boolean;
}

export interface TrackRecord {
  horizon_days: number;
  as_of: string;
  tiers: Record<string, TrackRecordTier>;
  picks: TrackRecordPick[];
}
```

- [ ] **Step 2: Add the proxy route**

Create `frontend/app/api/track-record/route.ts`:

```typescript
import { NextRequest } from 'next/server';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: NextRequest) {
  const horizon = req.nextUrl.searchParams.get('horizon') ?? '30';
  try {
    const res = await fetch(`${API}/api/track-record?horizon=${encodeURIComponent(horizon)}`, {
      cache: 'no-store',
    });
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch {
    return Response.json({ tiers: {}, picks: [] }, { status: 503 });
  }
}
```

- [ ] **Step 3: Build the panel component**

Create `frontend/components/track-record-panel.tsx`:

```tsx
'use client';

import { useEffect, useState } from 'react';
import type { TrackRecord } from '@/types';

const TIER_ORDER = ['BUY', 'WATCHLIST', 'HOLD', 'SELL'] as const;

const TIER_STYLE: Record<string, string> = {
  BUY:       'text-buy',
  WATCHLIST: 'text-buy/75',
  HOLD:      'text-hold',
  SELL:      'text-sell',
};

export default function TrackRecordPanel() {
  const [data, setData] = useState<TrackRecord | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/track-record?horizon=30')
      .then((res) => res.json())
      .then((d: TrackRecord) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, []);

  if (failed || !data) return null;

  const hasData = TIER_ORDER.some((t) => (data.tiers[t]?.count ?? 0) > 0);
  if (!hasData) return null;

  return (
    <div className="rounded-xl border border-muted/15 bg-card p-4 mb-6">
      <h3 className="text-sm font-bold text-tx mb-3">
        Track Record — {data.horizon_days}d
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {TIER_ORDER.map((tier) => {
          const t = data.tiers[tier];
          if (!t || t.count === 0) return null;
          const avgReturn = t.avg_return_pct != null ? `${t.avg_return_pct.toFixed(1)}%` : '—';
          return (
            <div key={tier} className="text-center">
              <div className={`text-xs font-bold ${TIER_STYLE[tier]}`}>{tier}</div>
              <div className="text-lg font-mono tabular-nums text-tx">
                {t.hit_rate != null ? `${(t.hit_rate * 100).toFixed(0)}%` : '—'}
              </div>
              <div className="text-[10px] text-muted">
                {t.count} call{t.count === 1 ? '' : 's'} · avg {avgReturn}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire the panel into the market-picks page**

In `frontend/app/market-picks/page.tsx`, add the import near the top (alongside the existing `MarketPicksDashboard` import, currently line 10):

```typescript
import TrackRecordPanel from '@/components/track-record-panel';
```

Then in the `{/* ── Done ── */}` block (currently lines 654-663):

```tsx
        {/* ── Done ── */}
        {phase === 'done' && picks.length > 0 && (
          <MarketPicksDashboard
            picks={picks}
            generatedAt={generatedAt}
            fromCache={fromCache}
            onRescan={() => startScan(true)}
            pricesLastUpdated={pricesLastUpdated}
          />
        )}
```

replace with:

```tsx
        {/* ── Done ── */}
        {phase === 'done' && picks.length > 0 && (
          <>
            <TrackRecordPanel />
            <MarketPicksDashboard
              picks={picks}
              generatedAt={generatedAt}
              fromCache={fromCache}
              onRescan={() => startScan(true)}
              pricesLastUpdated={pricesLastUpdated}
            />
          </>
        )}
```

- [ ] **Step 5: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Manual verification**

```bash
cd frontend && npm run dev
```

Open `http://localhost:3000/market-picks`, trigger a scan (or load from cache). Confirm: page loads without console errors; the panel renders nothing (not a broken box) when there's no qualifying track-record data yet, which is the expected state on a fresh install (no history old enough).

- [ ] **Step 7: Commit**

```bash
git add frontend/types/index.ts frontend/app/api/track-record/route.ts \
        frontend/components/track-record-panel.tsx frontend/app/market-picks/page.tsx
git commit -m "$(cat <<'EOF'
feat: surface track record panel on the market-picks page

Adds the TrackRecord types, a proxy route to the new backend endpoint,
and a self-fetching panel showing hit-rate/avg-return per recommendation
tier. Renders nothing until there's at least one qualifying past call —
avoids showing an empty/misleading box on a fresh install.
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** snapshot schema (Task 1), core engine + hit rule + skip behavior (Task 2), endpoint (Task 3), frontend panel (Task 4) — all four Phase 1 spec sections covered. Caching and single-stock-analysis track record were explicitly out of scope in the spec and are not included here.
- **Placeholder scan:** none found — all steps have runnable code.
- **Type consistency:** `compute_track_record` signature matches between Task 2's implementation and Task 3's call site (`engine`, `horizon_days=horizon`). `TrackRecord`/`TrackRecordTier` TypeScript types (Task 4) match the JSON shape returned by `compute_track_record` (Task 2) and re-exposed by the endpoint (Task 3) — verified field names: `horizon_days`, `as_of`, `tiers`, `picks`, and per-pick `symbol`/`date`/`recommendation`/`entry_price`/`realized_return_pct`/`hit`.
