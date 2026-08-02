# Portfolio-Aware Picks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flag a market-picks recommendation's sector when the user's existing stock holdings are already concentrated (≥25% of stock-portfolio value) in that sector — a display-only overlay, computed at request time, that never touches the shared market-picks scoring/cache.

**Architecture:** Stop discarding the sector the pipeline already resolves per stock (currently thrown away). Add a pure aggregation function (`compute_sector_concentration`) plus a new `GET /api/portfolio/concentration` endpoint that joins a profile's stock holdings' latest valuations against their sectors (looked up via the existing 1h-cached `stock_info` task). The frontend calls this once per completed scan, using whichever profile the `/portfolio` page last selected (`localStorage.pf_profile_id`), and renders a small badge on flagged picks.

**Tech Stack:** Python 3.13, SQLAlchemy Core, FastAPI, Next.js 15 / React 19 / TypeScript.

## Global Constraints

- Tools/pipeline functions must never raise (see `CLAUDE.md`).
- Tests use `unittest`, collected by pytest.
- No new dependencies.
- Scope: only `assets.type = 'stock'` holdings count toward concentration — MF/FD/EPF/etc excluded.
- Threshold: a sector is flagged if existing stock holdings there are ≥25% of total stock-portfolio value.
- This is display-only — no change to market-picks scoring, ranking, confidence, entry/target/stop-loss, or the pipeline's 6h cache.
- Profile source is `localStorage.pf_profile_id` (same key `frontend/app/portfolio/page.tsx:97,129` already writes) — if unset, the feature is invisible, not an error.
- Run `cd frontend && npx tsc --noEmit` before considering the frontend task done (no other frontend check exists).

---

### Task 1: Sector passthrough + concentration aggregation function

**Files:**
- Modify: `market_picks_pipeline.py:1257`
- Modify: `portfolio_api.py` (add `compute_sector_concentration`, alongside the existing `compute_networth` at line 28)
- Test: `tests/test_portfolio_concentration.py` (new)

**Interfaces:**
- Produces: `compute_sector_concentration(stock_rows: list[dict], threshold_pct: float = 25.0) -> dict[str, dict]` — Task 2's endpoint calls this directly. Input rows are `{"sector": str, "value": float}`; output is `{sector: {"concentration_pct": float, "flag": bool}}` for every sector present in the input.
- Produces: each pick dict returned by `MarketPicksPipeline._phase_score` (and thus the final `/api/market-picks` response) now has a `"sector"` key instead of discarding it — Task 3 (frontend) relies on this field existing on `MarketPick`.

- [ ] **Step 1: Fix the sector passthrough (trivial, no dedicated test)**

In `market_picks_pipeline.py`, find (currently line 1257, inside `_phase_score`'s sector-balancing loop):

```python
        for pick in picks:
            sector = pick.pop("_sector", "Unknown")
```

Change to:

```python
        for pick in picks:
            sector = pick["sector"] = pick.pop("_sector", "Unknown")
```

This is a mechanical one-line change (assign-and-use the same value that was already being extracted) with no new branch or logic — matches this repo's convention that trivial one-liners don't need a dedicated test. The existing full test suite (run in Step 5 below) confirms nothing else broke.

- [ ] **Step 2: Write the failing tests for `compute_sector_concentration`**

```python
# tests/test_portfolio_concentration.py
import unittest

from portfolio_api import compute_sector_concentration


def _row(sector, value):
    return {"sector": sector, "value": value}


class ComputeSectorConcentrationTest(unittest.TestCase):
    def test_below_and_above_threshold(self) -> None:
        rows = [_row("IT", 20000.0), _row("Banking", 20000.0),
                _row("Pharma", 20000.0), _row("Auto", 40000.0)]
        out = compute_sector_concentration(rows)
        self.assertEqual(out["IT"], {"concentration_pct": 20.0, "flag": False})
        self.assertEqual(out["Banking"], {"concentration_pct": 20.0, "flag": False})
        self.assertEqual(out["Auto"], {"concentration_pct": 40.0, "flag": True})

    def test_at_threshold_exactly_flags(self) -> None:
        rows = [_row("IT", 25000.0), _row("Other", 75000.0)]
        out = compute_sector_concentration(rows)
        self.assertEqual(out["IT"]["concentration_pct"], 25.0)
        self.assertTrue(out["IT"]["flag"])

    def test_empty_rows_returns_empty_dict(self) -> None:
        self.assertEqual(compute_sector_concentration([]), {})

    def test_all_zero_value_returns_empty_dict(self) -> None:
        rows = [_row("IT", 0.0), _row("Banking", 0.0)]
        self.assertEqual(compute_sector_concentration(rows), {})

    def test_missing_sector_key_defaults_to_unknown(self) -> None:
        rows = [{"value": 100.0}]
        out = compute_sector_concentration(rows)
        self.assertEqual(out, {"Unknown": {"concentration_pct": 100.0, "flag": True}})

    def test_same_sector_rows_are_aggregated(self) -> None:
        rows = [_row("IT", 10000.0), _row("IT", 10000.0), _row("Banking", 5000.0)]
        out = compute_sector_concentration(rows)
        self.assertEqual(out["IT"], {"concentration_pct": 80.0, "flag": True})
        self.assertEqual(out["Banking"], {"concentration_pct": 20.0, "flag": False})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_portfolio_concentration.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_sector_concentration' from 'portfolio_api'`

- [ ] **Step 4: Implement `compute_sector_concentration`**

In `portfolio_api.py`, add immediately after `compute_networth` (currently ending at line 49):

```python
def compute_sector_concentration(stock_rows: list[dict], threshold_pct: float = 25.0) -> dict[str, dict]:
    """Aggregate stock-holding rows into per-sector concentration.

    stock_rows: [{"sector": str, "value": float}, ...] — one row per stock holding.
    Returns {sector: {"concentration_pct": float, "flag": bool}} for every sector present.
    """
    total = sum(r.get("value") or 0 for r in stock_rows)
    if total <= 0:
        return {}

    by_sector: dict[str, float] = {}
    for r in stock_rows:
        sector = r.get("sector") or "Unknown"
        by_sector[sector] = by_sector.get(sector, 0) + (r.get("value") or 0)

    return {
        sector: {
            "concentration_pct": round(value / total * 100, 1),
            "flag": (value / total * 100) >= threshold_pct,
        }
        for sector, value in by_sector.items()
    }
```

- [ ] **Step 5: Run tests to verify they pass, then run the full suite**

Run: `python -m pytest tests/test_portfolio_concentration.py -v`
Expected: PASS (6 tests)

Run: `python -m pytest tests/ -v`
Expected: all pass, no regressions (confirms the `market_picks_pipeline.py` one-liner didn't break anything)

- [ ] **Step 6: Commit**

```bash
git add market_picks_pipeline.py portfolio_api.py tests/test_portfolio_concentration.py
git commit -m "$(cat <<'EOF'
feat: surface pick sector + add sector-concentration aggregation

market_picks_pipeline.py stops discarding the sector it already resolves
per stock (was pop()'d and thrown away, used only for the sector-balancing
cap). compute_sector_concentration (portfolio_api.py) aggregates stock
holdings into per-sector % of total stock value with a 25% flag threshold
— pure function, mirrors compute_networth's existing separation of SQL
(endpoint) from computation. Not yet wired into an endpoint or the
pipeline's balancing loop's output usage — that's the next task.
EOF
)"
```

---

### Task 2: `GET /api/portfolio/concentration` endpoint

**Files:**
- Modify: `portfolio_api.py` (add imports, `_get_sector` helper, and the endpoint)

**Interfaces:**
- Consumes: `compute_sector_concentration(stock_rows, threshold_pct=25.0)` (Task 1).
- Produces: `GET /api/portfolio/concentration?profile_id=<int>&sectors=<comma-separated>` returning `{sector: {"concentration_pct": float, "flag": bool}}` — only for sectors both requested and present among the profile's stock holdings. Task 3 (frontend) calls this exact endpoint shape.

- [ ] **Step 1: Add a sector-lookup helper**

In `portfolio_api.py`, add near the top-level imports (after the existing `from portfolio_valuation import refresh_valuations, xirr_report` line):

```python
import cache
import uuid
from main import _fetch_task
from schemas import normalize as schema_normalize
```

Then add this helper function after `_require_db` (currently ending at line 61):

```python
def _get_sector(symbol: str, run_id: str) -> str | None:
    """Look up a stock's sector via the existing 1h-cached stock_info task. Never raises."""
    try:
        if cache.is_fresh(symbol, "stock_info"):
            raw = cache.load(symbol, "stock_info")
        else:
            raw = _fetch_task("stock_info", symbol, run_id)
            cache.save(symbol, "stock_info", raw)
        return schema_normalize("stock_info", raw).get("sector")
    except Exception:
        return None
```

This reuses the same cache/fetch/normalize pipeline the single-stock analysis flow already uses (`main.py`'s equivalent logic) — no new data source, no bypassing the existing 1h TTL.

- [ ] **Step 2: Add the endpoint**

Add after the existing `get_networth` endpoint (currently ending at line 451):

```python
@router.get("/concentration")
async def get_concentration(profile_id: int, sectors: str = ""):
    _require_db()
    requested = [s.strip() for s in sectors.split(",") if s.strip()]
    if not requested:
        return {}

    def _q() -> dict:
        with _engine().connect() as conn:
            rows = conn.execute(_text("""
                SELECT a.symbol, v.value::float AS value
                FROM assets a
                JOIN accounts ac ON ac.id = a.account_id
                JOIN LATERAL (
                    SELECT value FROM valuations
                    WHERE asset_id = a.id ORDER BY as_of DESC LIMIT 1
                ) v ON TRUE
                WHERE ac.profile_id = :pid AND NOT a.archived AND a.type = 'stock'
            """), {"pid": profile_id}).mappings().fetchall()

        run_id = uuid.uuid4().hex[:8]
        stock_rows = []
        for r in rows:
            sector = _get_sector(r["symbol"], run_id)
            if sector:
                stock_rows.append({"sector": sector, "value": r["value"]})

        return compute_sector_concentration(stock_rows)

    result = await _run(_q)
    return {k: v for k, v in result.items() if k in requested}
```

Note: `compute_sector_concentration` is called over the profile's *entire* stock portfolio (not just requested sectors) so the percentages are correct relative to the whole portfolio — only the returned dict is filtered down to the sectors the caller asked about.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all pass, no regressions

- [ ] **Step 4: Manual verification**

This endpoint has no dedicated automated test — matching this repo's existing precedent (`/api/sme-signals`, `/api/track-record` also have none; manual verification is the accepted method).

```bash
source .venv/bin/activate
uvicorn api:app --reload --port 8000 &
sleep 2
curl -s "http://localhost:8000/api/portfolio/concentration?profile_id=1&sectors=IT,Banking" | python3 -m json.tool
```

Expected: `{}` if profile 1 has no stock holdings, or `DATABASE_URL not configured` 503 if Postgres isn't set up — either is a valid confirmation the endpoint responds without a 500. If you have real stock holdings under profile 1, confirm the returned percentages look plausible (sum of all sectors' values ÷ your total stock value).

Kill the server afterward: `kill %1`.

- [ ] **Step 5: Commit**

```bash
git add portfolio_api.py
git commit -m "$(cat <<'EOF'
feat: add GET /api/portfolio/concentration endpoint

Joins a profile's stock holdings' latest valuations against their sectors
(via the existing 1h-cached stock_info task) and returns per-sector
concentration for the requested sectors. No dedicated endpoint test,
matching the existing /api/sme-signals and /api/track-record precedent —
verified manually via curl.
EOF
)"
```

---

### Task 3: Frontend — sector type, concentration fetch, badge

**Files:**
- Modify: `frontend/types/index.ts:130` (add `sector` to `MarketPick`)
- Modify: `frontend/app/market-picks/page.tsx` (fetch concentration, pass to dashboard)
- Modify: `frontend/components/market-picks-dashboard.tsx` (accept concentration prop, render badge)

**Interfaces:**
- Consumes: `GET /api/portfolio/concentration?profile_id=<int>&sectors=<comma-separated>` (Task 2's exact response shape), forwarded automatically by the existing catch-all `frontend/app/api/portfolio/[...path]/route.ts` — no new proxy route needed.
- Produces: no new interfaces — this is the final consumer.

- [ ] **Step 1: Add `sector` to the `MarketPick` type**

In `frontend/types/index.ts`, inside the `MarketPick` interface (currently lines 103-131), add after the `market_cap_cr` line (currently line 119):

```typescript
  market_cap_cr: number | null;
  sector: string;
```

- [ ] **Step 2: Add a `Concentration` type**

In the same file, after the `MarketPicksPhase` type (currently lines 133-134):

```typescript
export type Concentration = Record<string, { concentration_pct: number; flag: boolean }>;
```

- [ ] **Step 3: Fetch concentration once per completed scan**

In `frontend/app/market-picks/page.tsx`, add a new state variable near the other `useState` declarations (currently around line 201, after `pricesLastUpdated`):

```typescript
  const [concentration, setConcentration] = useState<Concentration>({});
```

Add `Concentration` to the existing type import at the top of the file (find the line importing from `@/types` and add `Concentration` to the list of named imports).

Add a new effect after the existing "Refresh LTP every 30 s" effect (currently ending at line 229, `}, [phase, picks.length]);`):

```typescript
  // Fetch sector concentration once per completed scan, if a portfolio profile is selected
  useEffect(() => {
    if (phase !== 'done' || picks.length === 0) return;
    const profileId = localStorage.getItem('pf_profile_id');
    if (!profileId) return;

    const sectors = Array.from(new Set(picks.map(p => p.sector))).join(',');

    fetch(`/api/portfolio/concentration?profile_id=${encodeURIComponent(profileId)}&sectors=${encodeURIComponent(sectors)}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: Concentration) => setConcentration(data))
      .catch(() => setConcentration({}));
  }, [phase, picks.length]); // eslint-disable-line react-hooks/exhaustive-deps
```

- [ ] **Step 4: Pass concentration into the dashboard**

Find the `<MarketPicksDashboard` render (currently lines 656-666, inside the `{/* ── Done ── */}` block) and add the new prop:

```tsx
        {phase === 'done' && picks.length > 0 && (
          <>
            <TrackRecordPanel />
            <MarketPicksDashboard
              picks={picks}
              generatedAt={generatedAt}
              fromCache={fromCache}
              onRescan={() => startScan(true)}
              pricesLastUpdated={pricesLastUpdated}
              concentration={concentration}
            />
          </>
        )}
```

- [ ] **Step 5: Accept the prop and render a badge**

In `frontend/components/market-picks-dashboard.tsx`, update the `Props` interface (currently lines 9-15):

```typescript
interface Props {
  picks: MarketPick[];
  generatedAt: string;
  fromCache?: boolean;
  onRescan: () => void;
  pricesLastUpdated?: Date | null;
  concentration: Concentration;
}
```

Add `Concentration` to the existing type import at the top of the file (currently line 4: `import type { MarketPick, PickSource } from '@/types';` — add `Concentration` to that list).

Add a new badge component after `HorizonBadge` (currently ending at line 76):

```tsx
function ConcentrationBadge({ sector, concentration }: { sector: string; concentration: Concentration }) {
  const info = concentration[sector];
  if (!info?.flag) return null;
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold
                     border text-hold/80 border-hold/20 bg-hold/8"
          title={`${info.concentration_pct}% of your stock portfolio is already in ${sector}`}>
      ⚠ {info.concentration_pct}% in {sector}
    </span>
  );
}
```

Update the component's destructured props (find `export default function MarketPicksDashboard({ picks, generatedAt, fromCache, onRescan, pricesLastUpdated }: Props) {`) to also destructure `concentration`:

```typescript
export default function MarketPicksDashboard({ picks, generatedAt, fromCache, onRescan, pricesLastUpdated, concentration }: Props) {
```

Then in the Rating + Horizon cell (currently lines 596-602):

```tsx
                      {/* Rating + Horizon — side by side */}
                      <td className="px-4 py-4">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <SignalBadge verdict={pick.recommendation} />
                          <HorizonBadge horizon={pick.horizon} />
                          <ConcentrationBadge sector={pick.sector} concentration={concentration} />
                        </div>
                      </td>
```

- [ ] **Step 6: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Manual verification**

```bash
cd frontend && npm run dev
```

Open `http://localhost:3000/market-picks`, run a scan. Confirm: page loads without console errors; no `ConcentrationBadge` renders if `localStorage.pf_profile_id` was never set (open devtools, run `localStorage.getItem('pf_profile_id')` — confirm it's `null` in a fresh browser profile, and confirm no badges appear and no console error). If you have a portfolio profile with stock holdings concentrated in a sector matching one of the picks, set `localStorage.setItem('pf_profile_id', '<id>')` in devtools, reload, and confirm the badge appears with a plausible percentage.

- [ ] **Step 8: Commit**

```bash
git add frontend/types/index.ts frontend/app/market-picks/page.tsx frontend/components/market-picks-dashboard.tsx
git commit -m "$(cat <<'EOF'
feat: show sector-concentration badge on market-picks

Fetches /api/portfolio/concentration once per completed scan, using
whichever profile localStorage.pf_profile_id points at (set by the
/portfolio page). Renders a small warning badge on any pick whose sector
is already >=25% of the user's stock portfolio. No badge, no error, and
no fetch at all if no profile has ever been selected in this browser.
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** sector passthrough fix + pure aggregation function (Task 1), endpoint (Task 2), frontend type/fetch/badge (Task 3) — all sections of the Phase 3 spec covered. Out-of-scope items (MF/FD concentration, scoring changes, profile-selector UI, caching, historical trend) correctly absent from every task.
- **Placeholder scan:** none found — all steps have complete, runnable code.
- **Type consistency:** `compute_sector_concentration(stock_rows, threshold_pct=25.0) -> dict[str, dict]` (Task 1) is called identically in Task 2's endpoint. The endpoint's response shape (`{sector: {concentration_pct, flag}}`) matches the `Concentration` TypeScript type (Task 3) and the `ConcentrationBadge` component's usage (`info.concentration_pct`, `info.flag`) exactly. `MarketPick.sector` (Task 3, Step 1) is populated by Task 1's pipeline fix and consumed by Task 3's `Array.from(new Set(picks.map(p => p.sector)))` and `ConcentrationBadge`.
