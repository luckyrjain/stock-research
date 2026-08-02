# CLAUDE.md (backend)

This file provides guidance to Claude Code when working with code in `backend/`. See the
repo-root [`CLAUDE.md`](../CLAUDE.md) for the project overview, full repo structure, and pointers
to the frontend/product docs.

---

## Backend (Python)

### Runtime & install

- **Python 3.13** (venv at repo-root `.venv/` — one venv shared by the whole backend, kept at the
  repo root rather than inside `backend/` since it's a local build artifact, not part of the
  package layout)
- **pip** — no poetry/uv
- All commands below assume `cd backend` first (from the repo root):
  ```bash
  source .venv/bin/activate   # from the repo root — venv lives there, not inside backend/
  cd backend
  pip install -r requirements.txt
  ```

### Running the server

```bash
cd backend && uvicorn api:app --reload --port 8000
```

### Running the CLI pipeline

```bash
cd backend
python main.py TCS
python main.py RELIANCE --force   # bypass cache
```

### Running tests

```bash
cd backend
python -m pytest tests/
python -m pytest tests/test_analysis_guardrails.py -v   # single file
```

Tests use `unittest` and are collected by pytest. They mock heavy dependencies (crewai, tool imports) via `sys.modules` patching — no external calls made.

`tests_live/` is a **separate** test root, never collected by the command above — it makes real
network calls to a handful of third-party sites and only runs opt-in (`RUN_LIVE_TESTS=1`), on a
weekly schedule. See "Live scraper contract checks" further down for why it exists and why it's
deliberately excluded from every other test invocation in this repo.

### Key libraries

| Library | Purpose |
|---|---|
| `fastapi` + `uvicorn` | HTTP server and SSE streaming |
| `crewai` | Only its `@tool` decorator (`crewai.tools`) is used, for a stable `.run()` calling convention on the data-fetching functions in `tools/`. The Agent/Task/Crew orchestration layer was removed (see "Agent architecture" below) — it was never on the production path. |
| `litellm` | Provider-agnostic LLM calls (analyst step) |
| `yfinance` | NSE/BSE price quotes; also used for ISIN → symbol resolution |
| `requests` + `beautifulsoup4` | Screener.in scraping, NSE API calls |
| `gnews` + `feedparser` | News articles from Google News RSS; RSS feeds for 5 financial news sources |
| `rapidfuzz` | Fuzzy company-name matching in market picks consolidation phase |
| `python-dotenv` | `.env` loading |

### Agent architecture

**Data fetching**: the API and CLI call `_fetch_task()` directly using `ThreadPoolExecutor` for parallel fetching — no agent orchestration involved. Each task wraps exactly one tool function and returns its raw JSON output.

| Task name | Tool | Data source |
|---|---|---|
| `stock_info` | `get_stock_quote` | yfinance + NSE API |
| `research` | `get_fundamentals` | Screener.in |
| `news` | `get_latest_news` | gnews (Google News) |
| `shareholding` | `get_holdings` | Screener.in |
| `mf_holdings` | `get_mf_holdings` | NSE API |
| `filings` | `get_nse_filings` | NSE corporate announcements |

`research` also carries a `quarterly_trend` (Sales/EPS mini-trend, oldest-first, from Screener's Quarterly Results table — the same company page `get_fundamentals` already fetches, so it's free) and `shareholding` carries `pledge_pct` (promoter pledge %, parsed from the same shareholding table `get_holdings` already fetches, as its own field rather than folded into `shareholding_pattern`). Both are absent/empty rather than guessed when Screener doesn't have a clean, fully-numeric window for them (e.g. a recent IPO with fewer quarters on record) — same "never invent" convention as everywhere else in this pipeline. `quarterly_trend` also carries an independently-optional `operating_margin` (Screener's own OPM % row, never derived/computed here) — several sectors (banks, NBFCs) routinely omit that row even when Sales/EPS are present, so it's dropped from the payload entirely rather than backfilled, distinct from the whole-object-absent case above. `results-dashboard.tsx` renders them as a "Quarterly Trend" card (two or three `Sparkline`s depending on whether `operating_margin` is present) and a "Promoter Pledge" line atop the Shareholding Pattern card (warning-styled when > 0%).

These tool functions are decorated with `@tool` from `crewai.tools` purely for a consistent `.run(**kwargs)` calling convention (see `main._fetch_task`) — that's the only thing this codebase still uses CrewAI for. There used to be a second, parallel orchestration path (`build_crew()` in `crew.py`, wiring per-task `Agent`/`Task`/`Crew` objects from `config/agents.json` + `config/tasks.json`) but it had zero callers and zero test coverage — data collection has always gone through `_fetch_task()` in production — so it was removed rather than left as unverified dead code. If you're looking for `LLM_MODEL` / the "data-agent tier" model config from an older version of this doc: it only ever fed that removed path and has been dropped too — `ANALYST_MODEL` (below) is the only model-selection env var that does anything.

**Analyst (direct LLM call)**: `run_analysis_with_fallback()` in `crew.py` calls `litellm.completion` directly — no CrewAI involved. It receives all six data slices plus signal engine context, and must return a specific JSON schema defined in `config/analyst.json`. Guardrails in `_validate_analysis_payload()` enforce structural rules and grounded-claims checks; a guardrail failure triggers one corrective LLM retry with the validation error appended. Only if that *also* fails — and a second configured provider, if any, also fails the same way — does it return a safe HOLD fallback via `_safe_analysis_fallback()`. See "LLM cost instrumentation + cross-provider failover" below.

**Market picks pipeline** (`market_picks_pipeline.py`): Six sequential phases, all blocking work offloaded to `ThreadPoolExecutor`. Communicates back to the SSE stream via `on_event` callbacks bridged through `asyncio.Queue` with `loop.call_soon_threadsafe`.

| Phase | What it does |
|---|---|
| `_phase_scrape` | Parallel fetch from 20 sources (5 RSS + 12 GNews + 3 structured). 6 workers. |
| `_phase_extract` | One LLM call per source (parallel, up to 6 workers). Checks extraction cache first. Detects syndicated articles (Jaccard ≥ 0.60) across sources to down-weight them. |
| `_phase_consolidate` | Groups picks by ticker, validates against NSE equity master, confirms live price via yfinance (guards pre-IPO / unlisted names). Uses rapidfuzz for fuzzy company-name matching. |
| `_phase_research` | Fetches `stock_info` + `research` + signal engine + a valuation percentile per stock (4 workers, up to `_MAX_STOCKS` stocks). |
| `_phase_analyze` | Batched LLM calls (8 stocks/batch, parallel) for qualitative summary + bull/bear factors. Does NOT ask the LLM for prices. |
| `_phase_score` | Deterministic confidence scoring (`_compute_confidence`: 50% signal engine + 30% consensus + 20% recency, 0–100, plus a small ±3-point valuation nudge layered on top — see below). The 4-tier rec (BUY / WATCHLIST / HOLD / SELL) is a *separate* formula on top — `combined_dir = 0.55 × consensus + 0.45 × signal_score`, thresholded, with a quant-veto that demotes BUY → WATCHLIST on a strongly negative signal score. Entry/target/stop-loss computed from price and signal score — no LLM. Sector-balanced (`_apply_sector_balance()`): max 2 stocks per sector promoted to the primary list, excess deferred to the end — `sector` stays on every pick in the response (real, filterable data, not popped like the old internal-only `_sector`). Saves a daily snapshot under the `market_picks_history` namespace in `state_store.py` for trend tracking. |

**Deliberately not decomposed in this pass**: at ~1,600 lines, `market_picks_pipeline.py` is the
single largest Python module in this repo, and `_phase_extract`/`_phase_consolidate` are each
150-200+ lines mixing scraping, LLM calls, fuzzy matching, and validation — the same
maintainability gap the `routes/` split (below) and the `results-dashboard.tsx` component
extraction (further below) already closed for their own respective files. Flagged directly by a
deep gap analysis but not attempted here: unlike those two prior extractions (each a mechanical,
behavior-preserving reorganization with an existing test suite to lean on), this pipeline's six
phases share mutable state and threading/async coordination that make a safe split materially
riskier to get right without a much larger, dedicated verification pass — the same
"disclosed, not silently dropped" instinct as `routes/` split's own "future work" note just below,
not a claim this doesn't need doing.

### LLM cost instrumentation + cross-provider failover

Two related gaps a CTO/investor-lens review flagged directly: "no per-analysis LLM cost
instrumentation and no margin model anywhere" (user growth scales a real, metered API cost
against a product that currently monetizes nobody), and "a full provider outage converges every
analysis, platform-wide, to the same generic HOLD, indistinguishable from a real call, with no
user-facing 'degraded' signal and no attempt at a second configured provider."

1. **Cost instrumentation** (`llm_cost.py`) — `crew.py::_attempt_provider()` calls
   `llm_cost.record_call_cost()` after *every* `litellm.completion()` call, not just the one that
   ultimately validates: a guardrail-retry or a failed failover attempt still spent real tokens.
   `estimate_cost_usd()` wraps `litellm.completion_cost()` — never raises, never guesses: litellm
   doesn't have pricing data for every model (a self-hosted Ollama model, a brand-new release its
   pricing table hasn't caught up to), and a missing price degrades to `None`, never a fabricated
   number that looks like a real cost. Each call's cost/tokens are logged immediately
   (`llm_call_cost` event, queryable through whatever this deployment already does with structured
   logs) and accumulated into a running per-UTC-day total under the `llm_cost` namespace in
   `state_store.py` (`call_count`, `total_cost_usd`, `calls_with_unknown_cost`) — the same "one
   counter plus a log line" convention as `scraper_error_counters.py`/`source_health.py`, a real
   answer to "what's today's total LLM spend" without a second billing/observability platform.
   The daily read-modify-write cycle is serialized by `state_store.mutate()`'s row lock — an
   own-adversarial-review pass caught that the first version of this module used only an
   in-process `threading.Lock`, which does nothing to prevent two backend *worker processes* (the
   exact multi-worker/`REDIS_URL` topology `docs/deployment.md`'s "Scaling" section documents as
   supported) from both reading the same prior `call_count`, both incrementing locally, and the
   second write silently overwriting the first — undercounting cost with no warning logged,
   directly undermining the one thing this module exists to get right. That was first fixed with
   an `fcntl.flock` advisory lock over a JSON file at `output/_llm_cost/<date>.json`; the row lock
   that replaced it holds across separate *hosts* too, which `flock` never did. Covered by a
   `ConcurrencySafetyTest` in `tests/test_llm_cost.py`, mirroring `source_health.py`'s own.
2. **Cross-provider failover** (`crew.py`) — `run_analysis_with_fallback()`'s single-provider LLM
   call is extracted into `_attempt_provider()` (its own guardrail retry once, rate-limit retry
   once — unchanged from before this existed), so a second, differently-configured provider can
   get exactly one full attempt before falling through to the safe HOLD fallback.
   `_configured_providers()` returns every provider with a usable API key
   (`_API_KEY_ENV`, declared order); the primary is `_resolve_provider()` (respects
   `LLM_PROVIDER` if set, else the auto-detected first configured key), and the failover
   candidate is the first *other* configured provider, if any — **but only when `LLM_PROVIDER`
   itself was never explicitly set**. An explicit `LLM_PROVIDER` is this deployment's own
   deliberate pin (e.g. a local-only Ollama setup kept off the cloud on purpose for data
   residency), not merely "whichever key happened to be configured first" — a stray second
   provider's key left in the same environment for an unrelated reason (shared with another
   service, leftover from testing) must not silently send this analysis's fetched data to that
   other provider on a transient failure of the pinned one. Caught in the same adversarial-review
   pass as point 1 above; regression-tested
   (`test_explicit_llm_provider_pin_disables_failover_even_with_a_second_key_present`). With only
   one key configured at all — the common case per `.env.example`'s "set exactly one" instruction
   — the distinction is moot and this is a no-op either way: behavior is byte-identical to before
   failover existed, and existing tests confirm it (no provider key is set in this test
   environment, so `providers_to_try` has exactly one entry). `ANALYST_MODEL` (if set) only ever
   overrides the *primary* provider's model — reusing it for a failover attempt against a
   different provider would very likely be an invalid model string for that provider, so the
   failover attempt always uses that provider's own `_ANALYST_DEFAULTS` entry.
3. **Visible degraded state** — previously, `crew._safe_analysis_fallback()` already set an
   internal `_degraded: True` marker, but `main._strip_meta()` dropped it (underscore-prefixed,
   same convention as `_meta`) before the report ever left the backend — a provider outage's
   safe-fallback HOLD was genuinely indistinguishable from a real HOLD verdict anywhere in the
   UI, exactly the gap the review called out. `main._build_report()` now promotes this into a
   proper sibling `degraded: bool` field on the `Report` itself (never inside `analysis`, so it
   isn't subject to the four-file analyst-schema lockstep rule — the LLM never produces this
   field, it's a backend-computed meta-flag, the same instinct as `filings_summary` sitting
   alongside `filings` rather than folded into it). `frontend/types/index.ts`'s `Report.degraded`
   is a required `boolean` (never optional/undefined — always explicitly `true`/`false`).
   `results-dashboard.tsx` renders a `⚠ Analysis degraded` banner above the hero when true,
   explaining that this is a safe fallback, not a genuine analyst call, while noting that the
   scraped market data elsewhere in the report is still real.
4. **Disclosed scope**: this is a one-shot failover (primary → at most one alternate), not an
   n-provider cascade — matching this codebase's other disclosed "sufficient increment, not
   maximal engineering" scope calls (e.g. the live-contract-check harness covering 4 scrapers,
   not all ~10). The cost tracker is a grep-able counter file, not a billing/margin model — it
   answers "what did today cost", not "what's our unit economics at scale," which remains
   unaddressed and is flagged as such in the "out of scope" notes elsewhere in this doc.

**Peer/valuation-anchor wired into scoring**: `GET /api/peers/{symbol}`'s `absolute_anchor` (where a
stock's current P/E sits within its own last 3-5 years of Screener-published P/E — see "Absolute
valuation anchor" below) previously only reached the single-stock analysis flow; Market Picks
scoring had no valuation-quality input at all. `peer_analytics.py` (repo root) holds the pure
percentile/anchor math (`compute_peer_percentiles`, `compute_valuation_anchor`) extracted out of
`api.py`, plus `build_peer_result(symbol, raw)` — the single source of truth for the response/cache
*shape* both call sites read and write (`{symbol, self, peers, sector_median, percentiles,
absolute_anchor}`) — both `api.py`'s `GET /api/peers/{symbol}` and `market_picks_pipeline.py`'s
`_phase_research` now import from this one shared module rather than duplicating the math, the
shape, or having one pipeline module reach into the other. `_phase_research`'s
`_fetch_valuation_percentile()` fetches `get_peer_comparison()` for each candidate stock (a third
parallel fetch alongside `stock_info`/`research`, `ThreadPoolExecutor(max_workers=3)`) and computes
only the *absolute* anchor (own P/E history), not the peer-relative percentile — it needs just that
one stock's own Screener page, not a second peer-group lookup, so it's cheap to add to every
candidate's research step. `None` (never guessed) when Screener didn't have a parseable current P/E
or fewer than 3 years of valuation-band history for that stock.

**Shares the `"peers"` cache with `GET /api/peers/{symbol}`** (`cache.load`/`cache.save(symbol,
"peers", ...)`, 24h TTL) rather than scraping Screener.in on every pipeline run — without this, a
weekly-cron run or `?force=true` re-scan would issue up to `2 * _MAX_STOCKS` fresh, fully uncached
Screener.in requests (`get_peer_comparison()` makes two HTTP round trips per call) on top of the
`research` task's own cached Screener.in hit, on every single run, contradicting this codebase's
documented Screener/NSE rate-limit caution. Because both call sites go through
`build_peer_result()` for the cached value's shape, they transparently share one entry per symbol
regardless of which one populates it first — a shape mismatch between the two (e.g. one caching the
raw scrape, the other caching the computed result) would otherwise make the other reader silently
see missing fields instead of a real cache hit.

`_compute_confidence()` folds the resulting percentile in as a confirmation signal, not a fourth
primary component: ≤33rd percentile (cheap vs. own history) adds +3, ≥67th percentile (expensive)
subtracts 3, mid-range and `None` are both no-ops — bounded by the existing final `min(100, max(0,
...))` clamp rather than reallocating weight from the 50/30/20 split. Surfaced on each pick as
`valuation_percentile` (nullable) and rendered as a "Valuation" key-metric row in
`market-picks-dashboard.tsx`'s expanded row.

---

---

## Code Style & Conventions

### Python

- **No formatter configured** (no black/ruff/autopep8 in requirements or config). Match surrounding code style.
- **pylint** is referenced via `# pylint: disable=` comments in `crew.py` and `main.py` but is not enforced in CI.
- **Type hints** are used on public function signatures throughout (`-> dict`, `-> str | None`, `list[dict]`). Use Python 3.10+ union syntax (`X | Y`, not `Optional[X]`).
- Private helpers are prefixed with `_`. All internal functions in `api.py` are `_*_sync` to signal they are blocking.
- Return `dict` from tools and pipeline functions. Never raise exceptions from tool functions — return `{"error": "...", "symbol": sym}` instead.

### Naming

- Python: `snake_case` everywhere; constants in `UPPER_SNAKE_CASE`; private helpers prefixed `_`
- Task names (the six data slices) are always lowercase strings: `"stock_info"`, `"research"`, etc. These are used as dict keys, cache filenames, and SSE event fields — keep consistent

---

## Environment & Config

All configuration is via `.env` at the repo root (copy from `.env.example`) — python-dotenv's
`load_dotenv()` walks up from `backend/` and finds it there; see the repo-root CLAUDE.md's
"Repo Structure" note.

### Required — set exactly one API key

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `OPENAI_API_KEY` | OpenAI provider |
| `GROQ_API_KEY` | Groq provider |
| `GOOGLE_API_KEY` | Google Gemini provider |
| `OPENROUTER_API_KEY` | OpenRouter (access to 300+ models) |

Provider is auto-detected from whichever key is present (checked in the order above). Set `LLM_PROVIDER` explicitly to override.

### Optional

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | auto | `anthropic` / `openai` / `groq` / `google` / `openrouter` / `ollama` |
| `ANALYST_MODEL` | provider default | Model for the analyst LLM call — the only model-selection env var that does anything; data fetching doesn't call an LLM (see "Agent architecture") |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Only needed when `LLM_PROVIDER=ollama` |
| `LOG_LEVEL` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`) |
| `DATABASE_URL` | unset | PostgreSQL DSN — required for the SME signals pipeline (`/api/sme-signals`), the watchlist (`/api/watchlist`), the verdict timeline (`/api/verdict-history/{symbol}`), account/magic-link auth (`/api/auth/*`), and Alembic schema migrations (see "Schema migrations" below — `migrations/env.py` reads this same env var) |
| `FRONTEND_URL` | `http://localhost:3000` | Canonical frontend origin embedded in magic-link sign-in emails (`/auth/verify?token=...` must run in the browser to receive the session cookie, so it can't point at the FastAPI backend directly) |
| `SMTP_HOST` | unset | SMTP server for magic-link emails. Without it, sign-in links are created and stored but never emailed (logged as a warning; the request still returns success) |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` / `SMTP_PASSWORD` | unset | SMTP auth — skipped if either is unset |
| `SMTP_FROM` | `SMTP_USER` or `noreply@alphapulse.local` | From address on the sign-in email |
| `SMTP_USE_TLS` | `true` | Set to `false` only for a local/dev relay that doesn't speak STARTTLS |
| `SENTRY_DSN` | unset | Forwards every error-level `observability.log_event()` call to a Sentry-compatible ingest endpoint (see "Error tracking / APM hook" below). No-op without it — `sentry-sdk` is a hard dependency but does nothing until this is set |
| `SENTRY_ENVIRONMENT` | `production` | Tag attached to every event sent to Sentry when `SENTRY_DSN` is set (e.g. `staging`) |
| `TRUSTED_PROXY_SECRET` | unset | Shared secret proving a request's `X-Forwarded-For` header genuinely came through the Next.js proxy routes (see "Trusted client IP for per-IP rate limiting" below) — set to the same value on both this backend and the frontend process. Without it, every per-IP rate limiter keys off `request.client.host`, which is always the Next.js server's own IP |

See `frontend/CLAUDE.md` for the frontend's own `API_URL`/`TRUSTED_PROXY_SECRET` env vars.

**No central config module.** Roughly 20 backend env vars are read via scattered `os.getenv`/
`os.environ` calls across `api.py` and the standalone pipelines — this table (and the equivalent
prose throughout this file) is the closest thing to a schema, not a typed, validated module a
future reader could import and trust. `api.py::_log_startup_config_warnings()` (see "Trusted
client IP" above and the LLM-provider-count check next to it) covers the two highest-value cases a
deep gap analysis flagged — zero/multiple LLM provider keys, a non-default `ALLOWED_ORIGINS` with
no `TRUSTED_PROXY_SECRET` — as targeted, low-risk startup warnings, deliberately not as a first
step toward a full `config.py` typed-dataclass rewrite: touching every existing `os.getenv` call
site to route through one is a larger, more speculative refactor than the two concrete
misconfigurations that prompted this in the first place. Flagged here rather than silently
assumed solved.

---

## Agent Orchestration

## Agent Orchestration

### Stock analysis flow

1. Browser opens `EventSource` → Next.js proxy route → FastAPI `GET /api/analyse/{symbol}`
2. `api.py` checks `cache.is_fresh()` for each of the six tasks
3. Stale tasks are dispatched concurrently via `asyncio`/`ThreadPoolExecutor` calling `main._fetch_task()`
4. Each completed task emits a `task_done` SSE event; the browser updates its progress tracker
5. All six raw outputs are normalized through `schemas.normalize()` → canonical dicts
6. `signals.engine.run_signal_engine()` scores the canonical data and produces a verdict
7. `crew.run_analysis_with_fallback()` calls `litellm.completion` in a thread; the SSE loop sends heartbeats (`: heartbeat`) every 15s while waiting
8. Final `done` event carries the merged report dict

### Technical signal (RSI14 + EMA20/50 posture)

Extends the momentum-screener math `sme_ema_pipeline.py` already computes for SME stocks
(golden/death cross, RSI(14)) to every symbol the main stock-analysis flow scores — previously
that confirmation signal only existed for SME/Emerge stocks, not the primary NSE/BSE large-cap
flow this whole product is centered on.

1. `tools/price_history_tools.py::get_price_series(symbol, days=180)` is the shared daily-close
   OHLCV fetch — extracted out of `GET /api/prices/history/{symbol}` (the sparkline endpoint)
   rather than duplicated, so both call sites share one yfinance `.NS`/`.BO` fallback and one
   `price_history` cache (6 h TTL, same as before this extraction).
2. `signals/technical.py::technical_signal(symbol)` is the one signal in `signals/engine.py`
   that does its own I/O — every other signal (`volume`, `valuation`, `growth`, `filings`) reads
   from `features`, already-fetched data with no network calls of its own. It computes RSI(14)
   (same Wilder-style `ewm` formula as `sme_ema_pipeline._compute_rsi`) and EMA20/EMA50 trend
   posture over the cached close series, returning `UNKNOWN` (score 0, never guessed) when fewer
   than `_MIN_CLOSES` (75, same value as `sme_ema_pipeline._MIN_HISTORY_DAYS`) closes are
   available — not enough history for EMA50 to have meaningfully converged (e.g. a recent IPO).
   The `price_history` cache this reads (see point 1) is on its own 6 h TTL, independent of the
   six-task caches — a `?force=true` re-analysis bypasses `ALL_DATA_TASKS` but not this cache, so
   the technical signal can lag up to 6 h behind a forced refresh of everything else. Acceptable
   for a momentum-confirmation signal on daily-close data (a 6 h-old RSI/EMA reading rarely
   flips), but worth knowing if it's ever surprising in a support ticket.
3. `run_signal_engine(symbol, all_data)` calls `technical_signal(symbol)` directly (it already
   received `symbol`, no signature change needed) and blends it in at weight 0.2 — the same tier
   as `volume`/`filings` (confirmation signals), below `valuation`/`growth` (0.4, the primary
   fundamental drivers).
4. **Blocking-I/O consequence**: `run_signal_engine()` was previously pure CPU (dict lookups +
   arithmetic over already-fetched data) and so was called directly inside `api.py`'s
   `/api/analyse/{symbol}` async SSE generator, unwrapped. Adding a (cached, but still
   potentially network-hitting) call inside it means that call site now must run through
   `loop.run_in_executor()` like every other blocking call in the SSE path — the same "never
   block the event loop" rule the "SSE bridge pattern" section below already documents. The
   other three call sites (`main.py`'s CLI, `watchlist_alerts.py`'s batch loop,
   `market_picks_pipeline.py`'s `_phase_research`) were already running inside a synchronous
   script or a `ThreadPoolExecutor` worker, so they needed no change.
5. `results-dashboard.tsx`'s existing "Quant Signals" card renders every entry in
   `signal_context.signals` generically (`Object.entries(...).map(...)`), so the new
   `technical` entry appears automatically with no frontend code change — only its tooltip copy
   was updated to mention it.

**Stock-vs-Nifty relative performance**: `GET /api/prices/history/{symbol}?benchmark=true` is an
opt-in addition to the sparkline endpoint from point 1 above — `api._compute_benchmark_sync()`
diffs the requested window's first/last close against the Nifty50 over the identical window
(reusing `_fetch_nifty_closes()`, the same 24h `"NSEI"`-pseudo-symbol-cached series
`GET /api/market-picks/history`'s alpha stat already shares — no second index-fetch path).
Returns `{stock_change_pct, nifty_change_pct, alpha_pct}` or `null` (never guessed) when there
are fewer than 2 closes to compare or the Nifty fetch itself fails. Opt-in rather than
always-on since most callers of this endpoint (the Quarterly Trend card's revenue/EPS/margin
sparklines) aren't plotting a price series at all, so a Nifty comparison would be meaningless
for them — only `PriceSparkline` in the hero passes `?benchmark=true`, rendering a small
"+N% vs Nifty" line (buy/sell-toned by sign) under the sparkline.

**Sparkline hover tooltip**: `frontend/components/sparkline.tsx` gained an optional `dates` prop
— when supplied and aligned 1:1 with `closes`, hovering the chart shows the date + value at the
nearest point. `PriceHistory.dates` was fetched by every call site of this component but never
actually read before this — no chart anywhere in the app was previously inspectable. A caller
without `dates` (or a length mismatch) gets the exact same non-interactive chart as before.

### Macro overlay signal (FII/DII flow + RBI rate/inflation)

A market-wide overlay on top of the per-stock signals above — "is the broader institutional/rate
backdrop a tailwind or a headwind right now" — blended into every symbol's signal score at a low
weight (0.15), since it says nothing about the specific company.

1. `tools/nse_fii_dii_tools.py::get_fii_dii_flow()` — NSE's own daily provisional FII/DII net
   equity-flow report (₹ Cr). `tools/macro_context_tools.py::get_macro_context()` — RBI's policy
   repo rate and CPI inflation, scraped from RBI's own "Current Rates" table. Both follow the
   same never-raise, `{"error": ...}`-on-failure convention as every other `tools/*.py` module,
   and never guess a missing field (e.g. a DII row NSE didn't return, or a CPI figure RBI's
   homepage doesn't currently carry) — that field comes back `None`, never invented.
   **Disclosed limitation**: neither scrape target could be verified against a live response in
   this sandbox (no outbound internet — see the repeated disclosure elsewhere in this doc). Both
   parsers are written defensively so a real-world layout/schema drift degrades to an error dict
   rather than crashing the signal engine, but the actual selectors should be spot-checked against
   live NSE/RBI responses before this ships to a real deployment.
2. Unlike every other signal, this one is identical for every stock analysed on a given day, so
   `signals/macro.py` caches both fetches under a fixed pseudo-symbol (`"_MACRO"`) rather than
   fetching fresh per symbol — the same pattern `GET /api/market-picks/history` already uses to
   cache the Nifty benchmark series under a `"NSEI"` pseudo-symbol. `cache.TTL_HOURS` gained
   `fii_dii_flow` (24h — NSE publishes once per trading day) and `macro_context` (24h — RBI's repo
   rate changes at most every MPC meeting and CPI is a monthly release, so daily refresh is purely
   a ceiling, not a real cadence match).
3. `signals/macro.py::macro_signal()` combines both inputs into one `Signal`: net FII+DII flow
   (₹ Cr) hits a raw component capped at ±0.6 at ±3000 Cr thresholds, sub-weighted ×0.6 → up to
   ±0.36 contribution to the signal's own score; CPI above 6% (above RBI's inflation-target upper
   bound) or below 4% hits a raw component of ∓0.4/+0.2, sub-weighted ×0.4 → up to ∓0.16/+0.08 —
   repo rate is carried in `meta` for context but doesn't independently move the score (CPI
   already captures the same tightening/easing direction more directly). `UNKNOWN` (score 0) only
   when every one of the four underlying fields is `None`.
4. `run_signal_engine()` calls `macro_signal()` unconditionally alongside `technical_signal()` —
   both are the signals in this package that do their own I/O, so both are subject to the same
   "callers on an asyncio event loop must invoke this via an executor" rule `api.py`'s
   `/api/analyse/{symbol}` SSE endpoint already satisfies (see the "Technical signal" section
   above — no further change to that call site was needed for `macro`).

### Sector-aware signal weights

Previously the same `.4/.4/.2/.2/.2/.15` weight split applied to every stock regardless of
sector — a capital-intensive bank and an asset-light IT services company got identical
valuation/growth logic. `signals/engine.py::_weights_for_sector()` now layers a documented tilt
on top of `_DEFAULT_WEIGHTS` for three economically-similar sector groups, keyed off yfinance's
own `sector` field (`tools/nse_tools.py::get_stock_quote` → `info.get("sector")`, assumed to be a
GICS-like taxonomy — see the disclosed limitation below):

- **Rate-sensitive** (`Financial Services`, `Real Estate`, `Utilities`): valuation and the macro
  overlay (FII/DII flow + RBI rate/inflation, see above) weighted up; growth weighted down —
  these are typically mature, income-oriented businesses, not high-growth compounders.
- **Growth** (`Technology`, `Communication Services`, `Healthcare`): growth weighted up; the
  macro overlay weighted down — export-oriented, globally-priced businesses are less exposed to
  domestic rate/inflation than the FII/DII-heavy sectors above.
- **Cyclical** (`Basic Materials`, `Energy`, `Industrials`, `Consumer Cyclical`): technical and
  volume weighted up, offset by valuation and growth weighted down — price/volume momentum is
  more informative for a cyclical business than for a steady compounder, and a cyclical's
  steady-state fundamentals matter less than a compounder's.
- Any sector outside those three groups (including `None` when yfinance didn't report one) uses
  the unchanged default weights this engine always used.

Every override reallocates weight from other signals rather than just adding to the total, so
each group's weights sum to the same 1.55 baseline as `_DEFAULT_WEIGHTS` — a pure add-without-
offset would otherwise inflate that sector's `final_score` magnitude against the shared,
sector-independent verdict thresholds.

**Explicitly not a back-tested calibration** — three grouped buckets rather than one override
per individual GICS sector, since with only six signals and no realized-return backtest behind
any of this, splitting further would read as more empirical precision than the underlying
judgment actually has. This closes the "identical weights regardless of sector" gap; it does not
claim the specific override numbers are empirically optimal.

**Disclosed limitation**: whether yfinance actually reports GICS-like sector names (e.g.
`"Technology"`, `"Financial Services"`) for NSE/BSE symbols, rather than a different taxonomy,
was not verified against a live response in this sandbox (no outbound internet — same disclosure
as the FII/DII and macro-context scrapers above). There's real in-repo counter-evidence worth
noting: other pre-existing test fixtures in this codebase (`tests/test_signal_engine.py`'s own
`ExtractFeaturesTest`, `tests/test_market_picks_scoring.py`) use short Indian-market-style labels
like `"IT"`/`"Banking"` for this same field, rather than GICS names. If the real taxonomy differs,
every sector silently falls through to `_DEFAULT_WEIGHTS` — safe (identical to this engine's
pre-existing behavior) but a no-op for NSE/BSE stocks in production. `_log_unmatched_sector_once()`
logs a one-time-per-process debug event (`sector_weight_override_unmatched`) for each distinct
non-matching sector value seen, so this can be validated against real production traffic
post-merge without adding a new metrics dependency.

### Peer comparison flow (`GET /api/peers/{symbol}`)

Answers "is this ratio actually cheap/expensive for its sector" — something the
analyst prompt explicitly won't do (`config/analyst.json`: "Never invent
benchmarks or sector averages that are not in the data"). Real peer data closes
that gap without touching the analyst step at all:

1. `tools/screener_tools.py::get_peer_comparison()` scrapes Screener.in's own
   Peer comparison table (`section#peers`) — the company's row, up to 5 sector
   peers, and Screener's own sector-median row when present. Column parsing is
   driven entirely by the table's own headers (`_parse_peer_table()`), not a
   hardcoded schema, since the ratio set varies by sector (a bank's peer table
   looks nothing like an IT company's).
2. `api.py`'s `_compute_peer_percentiles()` ranks the company against its peers
   for every column both sides report (mean-rank percentile, 0-100). A ratio
   Screener doesn't expose for that sector (or that no peer reports) is simply
   absent from `percentiles` — never guessed or backfilled.
3. Cached like the six data slices (24 h TTL) but intentionally outside
   `ALL_DATA_TASKS` — a standalone, on-demand comparison fetched by the frontend
   after the main report loads, same pattern as `price_history` for sparklines.
4. `results-dashboard.tsx`'s `usePeerComparison()` hook fetches once and feeds
   both the dedicated "Peer Comparison" table and small percentile badges next
   to matching rows in the existing "Fundamentals" card — `normalizeRatioKey()`
   bridges the two independent label sets (the research task's own ratio names
   vs. Screener's peer-table column headers, e.g. "ROCE" vs "ROCE %").

**Absolute valuation anchor**: peer percentile only ever answers "cheap/expensive
*vs. peers*" — it says nothing about whether the stock is cheap/expensive *vs. its
own history*, which was the analyst-lens gap this closes. `_compute_valuation_anchor()`
(`api.py`) is folded into the same `/api/peers/{symbol}` response as a sibling
`absolute_anchor` field, not a new endpoint:

1. `tools/screener_tools.py::_extract_valuation_band()` parses Screener's own
   yearly Ratios table (`section#ratios`) for a "Price to Earning" row — the
   same company page `get_peer_comparison()` already fetches, so this is free
   (no extra network round trip), same pattern as `_extract_quarterly_trend`.
   Returns `{}` (never guessed) when the row is absent or fewer than 3 years
   are available — too thin a sample for a meaningful band. **Disclosed
   limitation**: whether Screener actually renders a yearly "Price to Earning"
   row under `section#ratios` (vs. only exposing historical P/E through its
   separate interactive chart, which this scraper does not call) was not
   verified against a live response in this sandbox — same disclosure as the
   FII/DII/macro scrapers and the sector-taxonomy assumption above. If the row
   isn't there under this id/label, this just returns `{}`, same as "Screener
   doesn't have this data" elsewhere in this module.
2. `_compute_valuation_anchor()` finds the current P/E in `self`'s own peer-row
   values (whichever column key contains "P/E", case-insensitive) and ranks it
   against `valuation_band.pe` using the same mean-rank percentile formula as
   `_compute_peer_percentiles` — but, unlike that function (which folds `self`
   into the ranked population), `current_pe` is ranked against `pe_values`
   alone: it's today's live snapshot, not itself one of the historical yearly
   observations. Returns `None` (not a guessed number) when there's no
   parseable current P/E or fewer than 3 years of band history.
3. `results-dashboard.tsx`'s `ValuationAnchorBadge` renders inside the existing
   "Peer Comparison" card, right below the table — buy/hold/sell-toned by
   percentile (≤33rd cheap, ≥67th expensive vs. its own range), showing the
   current P/E, its percentile, and the raw low/median/high band. Renders
   nothing when `absolute_anchor` is `null`.

### Insider & institutional activity flow (`GET /api/insider-activity/{symbol}`)

`tools/nse_insider_trades.py` and `tools/nse_bulk_block_deals.py` already scrape NSE's
promoter/director PIT disclosures and bulk/block deal feeds — but only as input to the
Market Picks discovery pipeline, where each qualifying trade is formatted as a
plain-language "article" for LLM extraction and then discarded. A researcher looking up
one specific stock had no way to see this activity unless that stock happened to make the
weekly picks list. This endpoint surfaces the same underlying data directly, per symbol:

1. Both tool modules gained a `_parse_pit_row()`/`_parse_deal_row()` shared parse step
   (returning a plain dict, not an LLM article) that the existing market-wide
   `_trade_to_article()`/`_deal_to_article()` functions now build on top of — so the
   market-wide and per-symbol paths can't drift on what counts as a "real" trade (same
   category/mode/value-threshold filters either way). `fetch_insider_trades_for_symbol()`
   and `fetch_bulk_block_deals_for_symbol()` are the new per-symbol entry points, returning
   `{"symbol", "trades": [...]}` / `{"symbol", "deals": [...]}` — structured records, not
   article text. Both sort on a separately-parsed `date_iso` field, never NSE's own raw date
   string (month abbreviations like "Jan"/"Apr" don't sort lexically in calendar order).
2. `fetch_insider_trades_for_symbol()` requests a 90-day window from NSE's PIT endpoint
   (vs. the market-wide scraper's 14-day window) — a single stock's insider activity is
   comparatively sparse, so a short window would too often show nothing. Bulk/block deals
   have no equivalent widening: NSE's `bulk-deals`/`block-deals` endpoints only ever return
   "recent trading days" with no date-range parameter to request more.
3. `GET /api/insider-activity/{symbol}` fetches both sources concurrently
   (`asyncio.gather`, same spirit as `_consolidated_payload`'s parallel lookups), combines
   them, and caches the combined result (24 h TTL) — standalone and on-demand, intentionally
   outside `ALL_DATA_TASKS`, same pattern as `peers`/`price_history`. Absent rather than
   guessed: most stocks simply have no recent insider/bulk activity in the window, which
   returns empty lists (never null), not an error.
4. `results-dashboard.tsx`'s `InsiderActivityCard` (via `useInsiderActivity()`) renders
   nothing when both lists are empty, and otherwise lists each trade/deal with a BUY/SELL
   badge, counterparty name, value, and date — right after the Peer Comparison card.

### Street consensus flow (`GET /api/street-consensus/{symbol}`)

`tools/trendlyne_agent.py::fetch_trendlyne_consensus()` already searches GNews for
Trendlyne-cited analyst commentary — but only market-wide, as Market Picks scoring input.
A researcher looking up one specific stock had no "N analysts rate this BUY" anchor
anywhere in the single-stock report. This surfaces the same search, scoped per symbol —
the same per-stock-endpoint pattern as insider activity above, but with one important
difference in what it can honestly return:

1. `fetch_trendlyne_consensus_for_symbol(symbol)` runs one GNews query ANDing the exact
   ticker, `"Trendlyne"`, and a buy/upgrade/target-price phrase (vs. the market-wide
   function's three broader queries), returning `{"symbol", "articles": [...]}` — real
   article title/summary/url/published_at, deduped by URL the same way
   `fetch_trendlyne_consensus()` already dedupes. The bare ticker is what `get_latest_news`
   already searches by elsewhere in this codebase, but stacked under three more required
   terms here recall is lower still — many tickers (`HDFCBANK`, `M&M`) rarely appear
   literally in prose the way journalists write company names, so this returns real
   coverage when Trendlyne got cited by name, not a guarantee of finding every article a
   human researcher would.
2. **`fetch_trendlyne_consensus_for_symbol()` itself is deliberately never a numeric
   consensus rating or target price.** It has never scraped trendlyne.com's own aggregated
   numbers — only GNews articles that happen to mention Trendlyne — so a "12 analysts rate
   BUY, target ₹X" figure wasn't data this function actually had. Returning one would have
   violated this codebase's "never invent" convention the same way guessing a missing
   scraped field would.
3. `tools/trendlyne_scraper.py::fetch_trendlyne_numeric_consensus(symbol)` closes that gap
   for real, additively — it hits trendlyne.com's own company page directly (`requests` +
   `BeautifulSoup`, never-raise convention like every other `tools/*.py` module) for
   `analyst_count`, `consensus_rating`, `mean_target_price`, and `target_upside_pct`.
   `_resolve_trendlyne_url()` tries a direct `/equity/{symbol}/` URL first (Trendlyne is
   expected to redirect a bare symbol to its full `/equity/<id>/<symbol>/<slug>/` page),
   falling back to Trendlyne's own search page and taking the first company-page-shaped
   result link — a similar "direct URL, then search fallback" *shape* to
   `screener_tools.py::_resolve_screener_slug`, but not equivalent in safety: Screener's
   resolver only ever extracts a `slug` string and reconstructs the fetch URL itself against
   the hardcoded `screener.in` domain, whereas this module's fallback parses and could
   otherwise trust an arbitrary `href` out of returned HTML. `_is_trendlyne_host()`
   host-checks every candidate URL (the redirected direct-URL result *and* every parsed
   search-page anchor) against `trendlyne.com` before it's accepted or followed — a
   cross-domain redirect or a stray ad/tracking/"similar stocks" anchor that happens to
   contain `/equity/` is treated as unresolved (`None`), never fetched. Without this check a
   crafted or accidental off-domain link would have been an SSRF vector: this module fetches
   whatever URL it resolves to with a real browser User-Agent. Parsing is
   regex-over-the-page's-own-text (`Consensus Recommendation: BUY`, `Mean Target Price ₹X`,
   `Y% Upside`) rather than narrow CSS selectors, since textual labels are more likely to
   survive a markup change than any specific selector guess. Every field is independently
   `None` (never guessed) when the page can't be resolved or a value isn't cleanly present —
   a page with a rating but no target price yields a partial result, not a discarded one.
   **Disclosed limitations**: (a) neither Trendlyne's symbol-to-company-page resolution path
   nor its exact DOM/label text for these numbers were verified against a live response in
   this sandbox (no outbound internet to non-allowlisted hosts — same disclosure pattern as
   the FII/DII/RBI scrapers, the NIFTY 500 constituent list, and every other unverified
   scraper already documented in this file); a real-world mismatch degrades every field to
   `None`, never a wrong number. (b) `_ANALYST_COUNT_RE` searches the whole page's flattened
   text for the first "N Analyst(s)" phrase rather than a section scoped to the consensus
   widget specifically — less label-specific than the other three regexes, so an unrelated
   "N Analysts" phrase earlier in the DOM (marketing copy, a "similar stocks" sidebar) could
   in principle produce a plausible-but-wrong count rather than `None`. Both are worth
   spot-checking against a live Trendlyne response before this ships to a real deployment.
4. `GET /api/street-consensus/{symbol}` fetches both sources concurrently
   (`asyncio.gather`, same spirit as `_consolidated_payload`'s and
   `GET /api/insider-activity/{symbol}`'s parallel lookups) and returns them as sibling
   fields on one response — `{"symbol", "articles": [...], "numeric_consensus": {...} |
   null}`. Both sub-fetches are isolated in their own try/except, symmetric per-section
   isolation matching insider activity's two independent sources — either a
   `fetch_trendlyne_consensus_for_symbol` or a `fetch_trendlyne_numeric_consensus` failure
   degrades only its own field (`articles` to `[]`, `numeric_consensus` to `null`) rather
   than 500ing the whole request via `asyncio.gather`'s first-exception-wins behavior. The
   numeric fetch additionally strips any `error` key off `fetch_trendlyne_numeric_consensus`'s
   result before it's returned or cached — that function's own never-raise convention (see
   point 3) attaches a raw exception string under `error` on an internal failure, and
   `cache._is_failed_payload()` only inspects a *top-level* `error` key, so a nested one here
   would otherwise both leak internal exception text to callers and get cached under the full
   24h TTL rather than being retried on the next request. Cached together (24 h TTL) but
   intentionally outside `ALL_DATA_TASKS` — standalone and on-demand, same pattern as
   `peers`/`insider_activity`. An empty `articles` list / null `numeric_consensus` fields
   (never an error) is the expected common case for most stocks on most days — both because
   most companies simply don't have recent Trendlyne-cited coverage or a resolvable Trendlyne
   page, and because of the GNews query's own recall limits noted in point 1.
5. `results-dashboard.tsx`'s `StreetConsensusCard` (via `useStreetConsensus()`) renders
   nothing when there's neither `articles` nor a resolvable `numeric_consensus`. When
   `numeric_consensus` has at least one non-null field (including `target_upside_pct` on its
   own, not just rating/count/target), a `NumericConsensusRow` renders above the article
   list — a rating badge (buy/hold/sell-toned by whether the rating string contains
   "BUY"/"SELL"), analyst count, and mean target price with its upside/downside % (or the
   upside/downside % standalone when there's no mean target to attach it to), linking out to
   the Trendlyne page it was scraped from — the component re-validates that link starts with
   `https://trendlyne.com/` before rendering it as defense in depth, independent of the
   backend's own host check in point 3. The article list below it is unchanged — up to 6
   recent titles/dates as external links, placed after
   `InsiderActivityCard` in the card grid.

### Multi-year financial statements + DCF valuation flow (`GET /api/financials/{symbol}`)

Closes the biggest single gap a competitive design review found versus Screener.in itself:
this app previously only ever surfaced current-year ratios (`research.ratios`) and a short
quarterly Sales/EPS/OPM window (`_extract_quarterly_trend`) — never a real multi-year
Income Statement / Balance Sheet / Cash Flow view.

1. `tools/screener_tools.py::_extract_yearly_statement(soup, section_id, max_years=10)` is a
   generic yearly-table extractor reused for Screener's `section#profit-loss`,
   `section#balance-sheet`, and `section#cash-flow` tables (the same company page
   `get_fundamentals()`/`get_peer_comparison()` already fetch — no extra request). Deliberately
   not a hardcoded row schema (a bank's balance sheet looks nothing like an FMCG company's) —
   whatever rows Screener renders come back as `{"years": [...], "rows": [{"label", "values"}]}`,
   same "whatever the table has is what's returned" instinct as `_parse_peer_table`. Unlike
   `_extract_quarterly_trend`'s strict "every cell must parse or the row is dropped" rule, a
   row here keeps `None` for any single year it can't parse — across up to a decade of history
   a gap in one year (e.g. a line item that didn't exist pre-IPO) is expected, not a
   misalignment to guard against. **Disclosed limitation**: neither the exact section ids nor
   row labels were verified against a live Screener response in this sandbox (no outbound
   internet — same disclosure pattern as every other Screener/NSE scraper in this file); a
   mismatch degrades to `{}` for that statement, never a fabricated table.
2. `get_financial_statements(symbol)` combines all three (each independently optional) into one
   payload. `GET /api/financials/{symbol}` caches it (24h TTL, `"financials"` cache task) but
   intentionally outside `ALL_DATA_TASKS` — standalone and on-demand like `peers`/
   `insider_activity`. A scrape failure is **not** cached (same as `GET /api/peers/{symbol}`) —
   a transient failure gets retried on the next request rather than locking in "no financials"
   for the full TTL.
3. `dcf_valuation.py::compute_dcf_estimate()` is a deterministic two-stage DCF off the cash-flow
   table's Operating Activity row — never LLM-generated, same "computed, not model-generated"
   convention as Market Picks' entry/target/stop-loss levels. A genuinely different valuation
   lens from the two this app already had (`_compute_peer_percentiles`'s peer-relative
   percentile, and `_compute_valuation_anchor`'s own-P/E-history anchor): both of those answer
   "cheap vs. what" (peers, or its own trading history), this answers "cheap vs. what its cash
   flows are worth". **Disclosed simplifications** (in the module's own docstring, not hidden):
   Operating Cash Flow is used as the Free-Cash-Flow proxy since Screener's cash-flow table has
   no cleanly-labelled, sector-independent Capex row to net against it; the discount rate (12%)
   and terminal growth (5%) are fixed market-wide assumptions, not per-company; historical OCF
   growth is clamped to [-20%, 25%] before being used to project forward, since a couple of
   noisy years can otherwise imply an absurd CAGR. Returns `None` (never a guessed number) when
   there's fewer than 3 years of OCF history, the latest OCF isn't positive, or a share count
   can't be derived from `market_cap_cr` + `current_price`. Wired into the same
   `GET /api/financials/{symbol}` response as a sibling `dcf` field.
4. `results-dashboard.tsx`'s `FinancialStatementsCard` renders each statement as a collapsible
   `<details>` table (`StatementTable`) — collapsed by default since a full 10-year table is
   dense. The DCF result renders inside the existing Valuation card, below the LLM's own
   Undervalued/Overvalued verdict, tone-colored by its own verdict and with an `InfoTooltip`
   disclosing the assumptions above — deliberately not a second competing "verdict" presented
   without context.
5. **Deliberately not attempted in this pass**: a numeric analyst consensus rating/target price
   was considered and explicitly rejected here as "would require fabricating data" — before the
   real Trendlyne-backed scraper above (`tools/trendlyne_scraper.py`) shipped and closed that
   gap properly. Left as a pointer in case the two efforts ever need reconciling: this section's
   DCF is a from-scratch model estimate; the Street Consensus section's numeric fields are
   scraped real numbers — they can legitimately disagree and neither should be read as
   correcting the other.
6. **Concalls** — `tools/screener_tools.py::_extract_concalls()` parses Screener's own
   "Concalls" section (`section#concalls`) on the same company page fetch as the three
   statements above, into one entry per quarterly earnings call with whichever of
   Transcript/PPT/Notes/REC links Screener has published for it. This closes a gap the design
   review called out directly: primary-source management commentary — what the company actually
   said on its own earnings calls — was entirely absent from this app; only third-party news
   coverage (Street Consensus) and Screener's own numeric ratios were ever surfaced. Wired into
   the same `GET /api/financials/{symbol}` response as a sibling `concalls` field (`[]`, never
   null, when Screener has none on record) rather than a new endpoint, since it's free off the
   same fetch. An entry missing one of the four link types simply omits that key; a call whose
   date this parser can't confidently read from the row's text is dropped rather than kept with
   a `None` date. **Disclosed limitation**: Screener's exact section id/label/markup for this
   feature was not verified against a live response in this sandbox — same disclosure pattern as
   every other Screener extractor in this section; a real-world mismatch degrades to `[]`, never
   a fabricated call date or link. `results-dashboard.tsx`'s `ConcallsCard` renders each entry as
   a date with a row of small "Transcript ↗ / PPT ↗ / Notes ↗ / REC ↗" link pills, right after
   `FinancialStatementsCard` — renders nothing when Screener has no calls on record.
7. **IPO grey-market premium (GMP) — explicitly out of scope for now.** The design review that
   produced points 1–6 above also flagged IPO GMP as a candidate metric. It's deliberately not
   implemented: GMP isn't exchange-published or vendor data like everything else this app
   sources (NSE, BSE, Screener, Trendlyne, RBI) — it's an informal, unregulated indicator that
   only exists on grey-market-tracking portals, SEBI has repeatedly warned it doesn't reflect a
   security's real value, and scraping those portals carries materially different reliability
   and ToS risk than the regulator/vendor sources this codebase otherwise limits itself to. This
   mirrors point 5's "declined to fabricate/source data without a solid footing" precedent
   rather than a "haven't gotten to it yet" gap — revisit only if a specific, reliable,
   ToS-compatible source is identified and the tradeoff above is explicitly re-examined.

### NSE session consolidation + Screener.in fallback resilience

Seven `tools/*.py` modules independently talk to NSE, and each one had to "prime" its own
`requests.Session` (a GET to nseindia.com's homepage for cookies — NSE rejects a cold request
with none) before its real API/CSV call. Three of the seven (`nse_insider_trades.py`,
`nse_bulk_block_deals.py`, `nse_fii_dii_tools.py`) had ended up byte-identical; the other four
(`nse_tools.py`, `nse_filings_tools.py`, `sme_tools.py`, `nifty500_tools.py`) had each drifted —
different helper names (`_nse_session` vs. `_get_session`), different priming timeouts (5/6/8/10s),
and inconsistent handling of a priming failure (two modules let it propagate uncaught into the
caller's own broad `except`, one folded a priming failure and a real data-fetch failure into the
same log line).

1. `tools/_nse_session.py::get_nse_session(timeout, accept, extra_headers, sleep_after_prime)` is
   the one place this logic now lives — builds a `requests.Session`, sets NSE-friendly headers via
   `session.headers.update()`, and primes it with a swallow-and-continue `try/except` GET plus a
   short sleep on success. Every one of the seven modules keeps its own thin local wrapper (same
   function name, same call signature it already had — `nse_tools.py` still defines `_nse_session()`,
   `nse_filings_tools.py` still defines `_get_session()`) that delegates here with its own
   timeout/header needs, specifically so every existing test's `patch("tools.<module>._nse_session",
   ...)` target keeps working unchanged rather than needing a rewrite across seven test files.
   **Still not fully collapsed**: a deep gap analysis noted each of the seven wrappers is still a
   real, if thin, function definition rather than inheriting a shared default — an eighth NSE-
   touching module would mean hand-writing a ninth near-duplicate one-liner. Not changed here since
   the test-patch-compatibility constraint above is the actual reason these per-module wrappers
   exist at all, not an oversight; collapsing further would need a different test-patching
   convention across seven existing test files, a larger change than this note's own scope.
2. **Resilience is standardized, not just deduplicated** — every priming attempt across all seven
   modules now uniformly swallows a failure and sleeps 0.5s on success (previously two modules
   let a priming exception propagate, and the sleep/swallow behavior varied module to module).
   `sme_tools.py` and `nifty500_tools.py` previously inlined this logic directly in their one
   call site with no named helper at all — both now have a local `_nse_session()` too, so all
   seven modules follow the identical pattern. `tests/test_nifty500_tools.py` was the one test
   file that had to change its patch target (`requests.Session` → `_nse_session`), since it was
   the only module with no pre-existing named helper to patch.
3. `tools/nse_tools.py::get_nse_basic_ratios(symbol)` is a new best-effort fallback — not a
   `@tool`, not one of the six `ALL_DATA_TASKS` — that `tools/screener_tools.py::get_fundamentals()`
   calls (lazy import, no module-level dependency) **only when Screener's own `ratios` dict came
   back completely empty** (e.g. a recent IPO Screener hasn't indexed a ratios table for yet, but
   NSE already has a results filing). It hits NSE's `corporate-announcements` endpoint with
   `reqXbrl=true`, finds the most recent "Financial Results" filing's XBRL attachment, and parses
   it (same localname-matching `lxml.etree` approach `get_mf_holdings()` already uses successfully
   for shareholding XBRL) for a basic EPS fact. Returns `{}` (never invented) on any failure —
   missing filing, missing XBRL attachment, or an unrecognized tag.
4. **Deliberately EPS-only, not "EPS, sales, profit"**: EPS is self-scaled (always "rupees and
   paise per share"), so there's no unit ambiguity. Sales/profit are aggregate rupee figures XBRL
   reports at a `decimals`/`unitRef`-dependent scale (absolute rupees, lakhs, or crore) —
   correctly resolving that requires parsing a real filing's unit metadata to confirm which
   convention it actually uses, which could not be verified in this sandbox (no outbound internet
   — see point 5). Guessing a scale and getting it wrong would inject a confidently-incorrect
   figure (e.g. off by 100×) — worse than the missing-data case this fallback exists to improve
   on — so those two fields are intentionally out of this first pass rather than shipped as a guess.
5. **Disclosed limitation**: neither NSE's `corporate-announcements` response shape under
   `reqXbrl=true` (which field, if any, carries the XBRL attachment URL — several plausible field
   names are tried) nor the exact Ind-AS XBRL tag names a real filing uses were verified against a
   live response in this sandbox — same disclosure pattern as every other NSE/BSE scraper in this
   codebase. Worth spot-checking before this is relied on in production; until then a wrong guess
   here degrades to `{}` exactly like an unreachable endpoint would, never a wrong number.
6. `schemas.py`'s `research` contract carries `nse_fallback_ratios` as an independently-optional
   field (present only when this fallback found something), and
   `results-dashboard.tsx`'s Fundamentals card renders a small "Screener.in had no ratios — showing
   EPS from NSE's own filings instead" note with just the EPS row when `ratios` is empty but
   `nse_fallback_ratios` is present — the ordinary ratios table renders as before in every other case.

### Filings classification (`signals/filings_classifier.py`)

The `filings` task's raw title/desc/category text was already fetched (and, since an earlier
phase, already fed into the analyst prompt) but never structured — a rating downgrade or an
upcoming results date sat unread in free text alongside routine newspaper-publication notices.
This is pure text classification over the already-fetched `filings` list — no new scrape.

1. `signals/filings_classifier.py` exports three independently-optional classifiers, all
   keyword/regex-based over each filing's `title`/`desc`/`category`: `classify_corporate_actions()`
   (dividend / split / bonus / buyback, one entry per matching filing, newest first),
   `classify_rating_action()` (the single most recent credit-rating filing — known agency name +
   upgrade/downgrade/reaffirmed, with best-effort `from_rating`/`to_rating` only when a clean
   "from X to Y" phrase is present in the text), and `extract_next_results_date()` (a future date
   parsed out of the most recent "board meeting to consider financial results" filing's own text
   — rejected if it parses to before the filing's own date, since that's more likely an unrelated
   date mentioned in the same text than the actual meeting date). `classify_filings()` combines all
   three into one dict, used by the frontend-facing consumer below; the signal-engine consumer
   calls `classify_rating_action()` directly (it only needs the rating piece) — both run the exact
   same function over the exact same filings list within one request, so the two can never disagree
   even though `classify_filings()` isn't literally shared between them.
2. **Disclosed limitation**: the exact category/title vocabulary NSE uses for these filing types
   was not verified against a live response in this sandbox (no outbound internet — same
   disclosure pattern as every other NSE/BSE scraper in this codebase). Every field is `None`/`[]`
   (never guessed) when nothing matches a known keyword — free-text classification over whatever
   house style NSE wrote that day is expected to miss real instances, not just rare ones.
3. **Signal engine**: `signals/filings.py::filings_signal()` calls `classify_rating_action()` on
   the same filings list it already receives via `features["filings"]`, and folds the result in as
   a small confirmation nudge (±0.15) on top of the existing keyword-hit score — upgrade nudges up,
   downgrade nudges down, `reaffirmed` is neutral (no new directional information). Same
   "confirmation signal layered on top, not a fourth primary component" pattern as the valuation
   percentile nudge in `market_picks_pipeline.py::_compute_confidence()`.
4. **Frontend**: `main._build_report()` (shared by both the CLI and `api.py`'s SSE endpoint) calls
   `classify_filings()` once on the same `filings` list the report already returns, and adds the
   result as a new sibling `filings_summary` field on the `Report` — `results-dashboard.tsx`'s
   existing Corporate Filings card renders it as a row of small badges (each corporate action,
   the rating action color-coded buy/sell/hold by direction, the next results date) above the
   existing filing list, and renders nothing extra when `filings_summary` has nothing to show.

### MF holdings trend (`mf_holdings_history.py`)

The `mf_holdings` task's shareholding disclosure was already fetched every ~7 days (its own
`shareholding`-tier cache TTL) but only ever shown as a single live snapshot — there was no way
to see whether a fund was building or trimming its stake quarter over quarter.

1. `mf_holdings_history` (new Postgres table, `db/models.py` + `db/schema.sql`, same
   `CREATE TABLE IF NOT EXISTS` pattern as `verdict_history`/`screener_stocks` — a wholly new
   table needs no separate `ALTER TABLE` migration guard, unlike a new column on an existing
   table) stores one row per `(symbol, as_of_date, fund)`.
2. `mf_holdings_history.save_snapshot(symbol, mf_holdings)` is wired into `main._fetch_task()` —
   the same choke point `schema_drift.log_drift_if_any()` already uses — guarded to only the
   `mf_holdings` task, on both its raw-dict and parsed-JSON-text success paths. Since
   `_fetch_task()` is only ever called for a task api.py's cache-freshness check marked stale, a
   cache hit never reaches this at all — the table's write cadence naturally follows however
   often `mf_holdings` actually gets re-fetched, no separate poller. No-ops (never invented) if
   `DATABASE_URL` isn't set, the fetch was an error payload, or there's no `as_of_date`/no funds
   to record. A same-quarter re-fetch upserts existing rows rather than duplicating them.
3. `mf_holdings_history.compute_stake_deltas(symbol)` ranks the most recent stored snapshot's
   funds by stake, each with `delta_pct` — the change vs. that same fund's stake in the
   second-most-recent stored snapshot. `delta_pct` is `None` (never guessed) when there's no
   prior snapshot at all, or the fund is a new entrant absent from it.
4. **Kept out of `main._build_report()` itself, unlike `filings_summary`**: `compute_stake_deltas()`
   is a DB query, not pure in-memory computation — `_build_report()` runs directly on api.py's
   event loop (unlike `save_verdict_snapshot`, which is deliberately fire-and-forget through
   `run_in_executor` since the client isn't waiting on it), so a DB call inside it would violate
   the "never block the event loop" rule the SSE bridge pattern documents. Instead
   `mf_holdings_trend` is computed by each caller — `api.py` via an *awaited*
   `run_in_executor` call (the frontend does need this in the response, unlike verdict history)
   and `main.py`'s CLI path directly (no event loop to protect there) — and passed into
   `_build_report()` as a parameter, the same pattern the pre-existing `signals` parameter
   already established for keeping DB/compute-heavy work outside the report-assembly function.
5. `results-dashboard.tsx`'s existing "Mutual Fund Holdings" card renders a small ▲/▼ delta next
   to each fund's live holding % (green up, red down, muted for exactly 0%) by matching fund name
   against `mf_holdings_trend` — funds with no computable delta (no prior snapshot, or a new
   entrant) simply show no delta badge, the live percentage still renders as before. **Undisclosed
   risk worth flagging**: this match is exact-string on `fund` between the live-scraped
   `holdings.mutual_funds` and the DB-stored `mf_holdings_trend` (both ultimately sourced from the
   same Screener/NSE fund-name text, but fetched independently) — a fund renaming itself, or
   Screener changing its formatting of the same fund's name between quarters, would silently show
   no delta badge rather than a wrong one, same fail-open-to-"no data" instinct as everywhere else
   in this doc, but unlike those cases this specific assumption wasn't previously written down.

### Detailed shareholding flow (`GET /api/shareholding-detail/{symbol}`)

`holdings.shareholding_pattern` (aggregate Promoters/FIIs/DIIs/Public percentages, from
Screener.in) and `holdings.mutual_funds` (mutual funds only, from the six-task `mf_holdings`
slice) answer "how much of which broad category," never "who specifically." This closes that
gap: every individually-named shareholder NSE's own quarterly shareholding XBRL filing discloses
— named promoters with their own holding %, plus every other named-shareholder category the
filing actually tags (mutual funds, foreign portfolio investors, insurance companies, whatever's
really there) — surfaced as a standalone, on-demand endpoint, the same pattern as
peers/financials/insider-activity/street-consensus.

1. `tools/nse_tools.py::_fetch_shareholding_xbrl(symbol)` is the shared first half — locates the
   most recent shareholding XBRL filing via NSE's `corporate-share-holdings-master` endpoint and
   fetches+parses it (same SSRF host-check pre- and post-redirect, same `resolve_entities=False`
   XXE hardening, as `get_mf_holdings` already established) — extracted so `get_mf_holdings` and
   the new `get_shareholding_detail` don't each independently fetch the same document. Refactored
   out of `get_mf_holdings` itself with its external behavior unchanged (verified by its own
   existing test suite passing unmodified).
2. `get_shareholding_detail(symbol)` reuses `get_mf_holdings`' own proven extraction mechanism
   (`NameOfTheShareholder` + `ShareholdingAsAPercentageOfTotalNumberOfShares` XBRL facts, keyed
   off the `typedMember` context an XBRL shareholder record lives under) but **generalized to
   every category the filing has**, not filtered to contexts whose `typedMember` child tag
   contains `"MutualFunds"`. This means it doesn't need to guess NSE's exact category tag
   spellings for "Insurance Companies"/"Alternate Investment Funds"/etc. up front — it groups
   named shareholders by whatever category label the filing actually uses. The only tag-name
   assumption layered on top of the already-working MF extraction is that a promoter/promoter-
   group category's XBRL tag contains the substring `"Promoter"` (case-insensitive) — the same
   kind of substring match the MF filter already relies on for `"MutualFunds"`, not a new class
   of guess. `_humanize_category()` turns a raw PascalCase XBRL localname (e.g.
   `"ForeignPortfolioInvestorsMember"`) into a readable label (`"Foreign Portfolio Investors"`) —
   stripping a trailing `"Member"` is a generic XBRL dimensional-modeling convention (explicit/
   typed dimension member concepts are conventionally suffixed `"Member"` per the XBRL spec
   itself), not a guess specific to NSE's own taxonomy.
3. A promoter/promoter-group entity can plausibly hold up to (in principle) 100% of a closely-
   held company; any other single named institutional/individual holder above ~30% would be
   extraordinary — `get_mf_holdings`' own 30%-ceiling "drop rather than trust a wrong format
   guess" reasoning (see `_percent_from_ambiguous_value`'s docstring) is applied to every
   non-promoter category, with the ceiling raised to 100% only for entries already bucketed as
   promoters.
4. **Disclosed limitation**: like every other NSE/Screener/Trendlyne/RBI scraper in this
   codebase, the exact XBRL category tag names NSE's real shareholding filings use beyond
   `"MutualFunds"` (already proven correct by the existing `get_mf_holdings` code) were not
   verified against a live filing in this sandbox (no outbound internet — same disclosure as
   every other scraper here). If a real filing's promoter category tag doesn't contain
   `"Promoter"`, those records fall through into `shareholder_categories` under their own raw
   label instead of the dedicated `promoters` field — a degraded-but-not-wrong result (still a
   real, named holder, just not specially flagged as a promoter), never a fabricated one.
5. `GET /api/shareholding-detail/{symbol}` follows the exact caching/error convention `GET
   /api/insider-activity/{symbol}`/`GET /api/street-consensus/{symbol}` established: a genuine
   `{"error": ...}` result sets `unavailable: true` and is **not cached** (retried on the next
   request rather than locking a transient NSE failure in for the full TTL), while a legitimately
   thin filing (few/no individually-named holders above the plausibility threshold) is cached
   normally with `unavailable: false`. Cached like `mf_holdings`/`shareholding` (168h / 7-day TTL
   — the same quarterly regulatory filing cadence), not 24h like peers/financials, since this is
   the same underlying filing type as `mf_holdings`. `scraper_error_counters.record_scraper_error`
   fires only on the genuine-failure path, same "don't manufacture noise from the expected common
   case" instinct as every other standalone endpoint's error-counter wiring.
6. `results-dashboard.tsx`'s `ShareholdingDetailCard` renders "Promoters" and each other category
   as its own labeled list of name/holding-% rows, placed right after the existing "Shareholding
   Pattern"/"Mutual Fund Holdings" card pair — a complementary, more granular view, not a
   replacement for either. Renders nothing when there's genuinely nothing to show (no error, no
   named holders); renders a "temporarily unavailable" notice, not silence, when `unavailable` is
   true — same distinction `InsiderActivityCard`/`StreetConsensusCard` already draw.
7. **Adversarial-review-caught bug, fixed**: the first version of `get_shareholding_detail()`
   collected every category's `NameOfTheShareholder` facts into one flat dict keyed only by the
   `"D_"`-stripped context id, document-wide. This is safe in `get_mf_holdings()` (whose own
   equivalent dict only ever collects `MutualFunds`-tagged contexts, so a different category's
   context id can never land in the same dict) but not once generalized to every category — two
   *different* shareholder records in *different* categories whose context ids happen to reduce
   to the same base id after stripping `"D_"` (e.g. a context literally named `"D_5"` for one
   category and a separate context literally named `"5"` for another) would silently overwrite
   each other with no error, misattributing a name/category or dropping one entirely. Fixed by
   collecting every `(name, category)` candidate seen per base id rather than overwriting, and
   dropping (never guessing) any base id where more than one distinct candidate collided —
   covered by `test_colliding_context_ids_across_categories_are_dropped_not_misattributed`, which
   constructs exactly this scenario and confirms both colliding entries are absent from the
   result while an unrelated, non-colliding entry still comes through cleanly. The same review
   pass also caught `_humanize_category()` splitting short acronym categories NSE commonly uses
   verbatim (FII, NRI, HUF, IEPF) into single spaced-out letters — fixed with a regex that splits
   at a lowercase→uppercase boundary and at an acronym-run→Titlecase-word boundary, but not inside
   a run of consecutive capitals.

### Symbol validation flow (`GET /api/validate/{symbol}`)

Handles three input forms:
1. **ISIN** (e.g. `INE009A01021`) — resolved via NSE equity master CSV first, then yfinance as fallback
2. **BSE-forced** (exchange query param = `BSE`) — resolves Screener.in slug → proper ticker via `_screener_company_page_sync`
3. **Ticker / name** — NSE autocomplete + BSE autocomplete (via Screener) run in parallel; BSE ISIN lookup enriches the NSE result; Screener.in fallback if both miss

### Market picks flow

1. Browser opens `EventSource` → `GET /api/market-picks` (optional `?force=true` bypasses cache)
2. `api.py` checks `output/_market_picks/picks.json` (192 h / 7-day TTL — sized to the weekly cron
   cadence below plus a day of slack, not the old "no scheduled job" 6 h bound); serves cached `done`
   event immediately if fresh
3. On cache miss: wraps `MarketPicksPipeline.run()` in `run_in_executor`; bridges events via `asyncio.Queue`
4. Pipeline calls `on_event(payload)` → `loop.call_soon_threadsafe(q.put_nowait, payload)` → SSE stream
5. The six pipeline phases run synchronously inside the executor thread; final result saved to cache
   via `market_picks_pipeline.save_picks_cache()` (also re-exported into `api.py` as `_save_picks_cache`
   for the existing call sites/test patches)

**Weekly auto-refresh**: `.github/workflows/market-picks-cron.yml` fires every Monday at 01:30 UTC
(07:00 IST, ahead of NSE's 9:15 IST open) — weekly, not daily like `sme-cron.yml`, to match the
product's own "Top Indian Stocks This Week" framing. Unlike SME (which persists to Postgres, reachable
from anywhere), the picks cache is a local file on whatever host runs the backend — a GitHub Actions
runner can't compute picks and expect them to reach the live site. So this workflow instead calls
`GET {MARKET_PICKS_API_URL}/api/market-picks?force=true` on the already-deployed backend (same effect
as a user clicking "Fresh scan," just on a timer) and requires a `MARKET_PICKS_API_URL` repository
secret pointing at that backend's public address. `market_picks_pipeline.py` also has a `main()` CLI
entrypoint for a self-hosted crontab that runs on the *same* host as the backend (mirrors the
crontab alternative documented for `sme_ema_pipeline.py` below) — GitHub's own workflow does not call it.

**`GET /api/market-picks/status`** is cache metadata only (no pipeline run): `last_run_at` (present even
once the cache has gone stale — unlike the picks-serving path, "stale" and "absent" must be
distinguishable here), `cache_fresh`, and `next_scheduled_at` (computed in `api.py` from constants that
mirror the cron schedule above — kept in sync by hand, there's no way to share one source of truth
between a GitHub Actions cron expression and this Python computation). Powers the idle `/market-picks`
hero's true "Last scan" / "Next scheduled scan" line, replacing an unverifiable "every week" claim.

**Browsing a specific day's picks**: `GET /api/market-picks/history` normally aggregates every
stored daily snapshot into a per-symbol first/last-seen roll-up (see "Market picks
track record" below) — it never surfaces one day's actual full list. `?date=YYYY-MM-DD` is a second
code path on the same handler that skips aggregation entirely and returns that single day's snapshot
verbatim (`{"date": ..., "picks": [...]}`, the same shape `_save_history()` wrote it in — just the six
fields persisted there, not the full live `MarketPick` shape); 404 if no snapshot exists for that date
(weekend, holiday, or before this feature existed), 422 if `date` isn't `YYYY-MM-DD`. The no-`date`
aggregated response also grew an `available_dates` field (every date with a stored snapshot) so the
frontend's date picker (`/market-picks/history`) can bound its `<input type="date">` and step
Prev/Next through actual snapshot days without a second round trip.

**Positions ("I bought this")**: originally purely client-side (no backend endpoint, no DB table) —
now backed by a `positions` Postgres table with the exact same ownership shape as `watchlist_items`
(see "Watchlist flow" above): an anonymous per-browser `client_id` until the user signs in, then the
account's `user_id`, resolved by the same `routes.watchlist.resolve_owner()`/`owner_column()` helpers
(nothing about that resolution logic is watchlist-specific). This closed the one gap the frontend
design review called out directly: Watchlist, auth, and API keys had all moved toward real accounts
while Positions stayed the one feature standing still, still tied to a single browser with no way to
follow a signed-in user across devices. Signing in does **not** automatically migrate an existing
`client_id`'s positions onto the account — same deliberate scope call as `watchlist_items` — though
an explicit opt-in "claim my data" flow now exists for both; see "Watchlist flow"'s point 10 below.

`GET /api/positions?client_id=`, `POST /api/positions` (`{client_id, symbol, company, exchange,
entry_price, target_price, stop_loss}` — upserts via `ON CONFLICT ... DO UPDATE`, refreshing the
market levels captured at mark-time but leaving `shares`/`bought_at` untouched on a re-mark, since
the normal UI flow removes a position before re-adding it and this is mostly a safety net), and
`PATCH /api/positions/{symbol}` (`{client_id, shares}` — the one field filled in after the fact, see
below) / `DELETE /api/positions/{symbol}?client_id=` all live in `api.py` alongside the Watchlist
endpoints, same rate-limiting/cap/advisory-lock conventions (`_MAX_POSITIONS_PER_CLIENT`, 200, same
number as the watchlist cap). `frontend/lib/positions.ts`'s `usePositions()` hook is now a thin
network-backed hook — same module-level shared-cache-plus-generation-counter pattern as
`useWatchlist()`, reusing that hook's own `getClientId()` directly rather than duplicating it — and
`refreshPositions()` is wired into `/auth/verify`'s success path and `useAuth()`'s `logout()`
alongside `refreshWatchlist()`, for the same reason: neither hook's module-level cache otherwise has
any way to know the caller's identity just changed.

`PositionButton` (next to `TradeBox`'s entry/target/stop-loss in each pick's expanded row) toggles a
pick in/out of this list; `PositionsStrip` (rendered above the phase content on `/market-picks`, so it
shows regardless of whether a fresh scan has run) polls the *existing* `GET /api/prices` endpoint every
30 s for the tracked symbols' live price — no new backend work needed for that part — and computes P&L
client-side against each position's stored entry, flagging "At target" / "At stop-loss" when the live
price clears either level.

**Portfolio summary** (`/portfolio`): an aggregate view over every tracked position, addressing the
Product-lens gap "positions aren't aggregated into a portfolio" — `PositionsStrip` only ever showed
one card per position, with no roll-up. Reuses the exact same `GET /api/prices` poll `PositionsStrip`
already makes (30 s interval), just computed over the full `positions` array instead of rendered
per-card. Every position also carries an optional, user-entered `shares` field (never scraped/guessed
— there's no source of truth to derive it from, so it's `null`, not `0` or an assumed `1`, until the
user fills it in via an editable "Shares" input in the Portfolio table, which calls
`updateShares()` → `PATCH /api/positions/{symbol}`). Filling it in is deliberately **not** asked for
at "I bought this" click-time — that's meant to stay a frictionless one-click action while browsing
Market Picks. A "Capital-Weighted Value" stats block (₹ invested / ₹ current / ₹ P&L) is computed
only from positions that actually have a share count, labeled with how many of the total positions
contribute to it — never silently assuming "1 share per position," which would violate this
codebase's "never invent" convention the same way guessing a missing scraped field would. The
existing equal-weighted stats are unchanged and still cover every priced position regardless of share
count: win rate (share of priced positions currently above entry — the adjacent "W/L" breakdown also
surfaces a "flat" count for exactly-0%-P&L positions, so the two numbers always reconcile), average
P&L% (a plain mean of each position's own % move, not a capital-weighted return), best/worst
performer, and counts at target/stop-loss. `PositionsStrip` gained a "View full portfolio →" link;
`/market-picks`'s nav bar gained a "Portfolio" link alongside "Watchlist"; `/portfolio`'s own nav bar
links to every sibling section (same full set every other page's nav bar carries), even though no
*other* page links back to it — positions are only ever created from the Market Picks flow, so that
one entry point (plus `PositionsStrip`'s link) is enough for discoverability without adding a seventh
item to every other page's already-long nav bar.

**Sector-concentration badge**: `GET /api/portfolio/concentration` answers "am I already
overweight this sector" using only the lightweight `positions` table above — not a full
portfolio/holdings system, which this app doesn't have. `routes/positions.py::compute_sector_concentration()`
is a pure, capital-weighted aggregation (`shares × live price` per position, summed by sector,
as a % of total tracked value) — same "only over what's actually known, never guessed" instinct
as `/portfolio`'s own capital-weighted stats: a position missing `shares`, a live price (`GET
/api/prices`), or a sector is excluded from the calculation entirely rather than assumed. Sector
comes only from an already-fresh 1h `stock_info` cache entry (`cache.load(symbol, "stock_info")`)
— this is a read-only overlay computed at request time, so it never triggers a fresh scrape and
never touches Market Picks' own scoring/cache. A sector at or above `_CONCENTRATION_THRESHOLD_PCT`
(25%, matching the sector-balance cap's own spirit elsewhere in this pipeline) is "concentrated."
`market-picks-dashboard.tsx` fetches this once per completed scan (not polled, unlike live
prices — a user's position mix rarely changes mid-session) and renders a small "Concentrated"
badge next to `SignalBadge`/`HorizonBadge` on any pick whose `sector` is in the concentrated
list — reusing the `accent` design token rather than inventing a new one, since this codebase
has no dedicated "warning" token (same gap SME Signals' own illiquid badge already works around).
**Disclosed limitation**: inherits the same `stock_info.sector` GICS-vs-Indian-market-taxonomy
caveat already documented under "Sector-aware signal weights" above — an unmatched sector value
simply can't contribute to this calculation either.

### Portfolio aggregator (`/portfolio-aggregator`)

A **separate** personal net-worth tracker (profiles → accounts → assets, with a valuation
history per asset) — genuinely unrelated to `/portfolio` above, which is an aggregate P&L view
over Market Picks "I bought this" positions. The two features happen to share the word
"portfolio" and the `/api/portfolio/*` URL prefix (the sector-concentration endpoint above lives
at `/api/portfolio/concentration`; this feature's endpoints are the sibling sub-paths
`/api/portfolio/profiles`, `/accounts`, `/assets`, `/networth`) but have no other connection —
different tables, different router, different frontend page (`/portfolio-aggregator`, distinct
nav-bar label "Net Worth" so the two aren't confused for the same feature).

1. **Schema** (`db/models.py`) — `profiles` (bare name, no credentials — see point 4), `accounts`
   (`bank`/`broker`/`amc`/`epfo`/`other`), `assets` (`mf`/`stock`/`fd`/`epf`/`ppf`/`cash`/
   `manual`/`loan`, with a per-type free-form `meta` JSON column — e.g. an FD's rate/maturity
   date), `holdings` (units/avg_cost, only ever populated for `mf`/`stock` assets — modeled as
   its own table rather than nullable columns on `assets`, so every other asset type's row isn't
   NULL-heavy for fields that never apply to it), `valuations` (one row per `(asset_id, as_of)`,
   history from day one — editing an asset's value inserts a new day's row rather than
   overwriting the last one, unless it's the same day, which upserts), and `transactions`
   (schema-only in this increment — reserved for the not-yet-built valuation-engine/CAS-import
   sub-projects, no API or UI reads/writes it yet). `assets.meta`/`transactions.meta` use
   SQLAlchemy's generic `JSON` type, not Postgres's `JSONB` — this codebase's tests run these
   tables against SQLite (`create_engine("sqlite://")`, house rule: no live DB in tests), which
   has no JSONB equivalent; `JSON` behaves identically on both backends and nothing here needs a
   JSONB-specific operator (containment, path queries) anyway. Same reasoning drove
   `profiles`/`accounts`/`assets.created_at` to use `server_default=text("CURRENT_TIMESTAMP")`
   rather than this codebase's usual `NOW()` (Postgres-only) — every other table here is only
   ever tested through a mocked engine/connection, never a real `metadata.create_all()` against
   SQLite, so `NOW()` never had to be portable before this feature.
2. **API** (`routes/portfolio_aggregator.py`, mounted at `/api/portfolio` alongside the
   concentration endpoint) — CRUD for profiles/accounts/assets, a valuation-upsert endpoint
   (rejects a future `as_of` — 422, never silently clamped), and `GET /api/portfolio/networth`
   (sums each account's assets' latest valuation, loans subtracted since they're stored positive
   — a pure function, `compute_networth()`, unit-tested with fabricated rows rather than a live
   DB). Reuses `routes/_shared.py::run_owned_db_call()` for the rate-limit → DB-configured-check →
   run_in_executor → sanitize-error wrapper every other route module already shares, even though
   this feature has no ownership concept to speak of (see point 4) — the wrapper's shape doesn't
   actually require one. **Fixed a latent gap in that shared wrapper while wiring this up**: it
   had no `except HTTPException: raise` before its generic `except Exception` catch, so a 404
   raised from inside a route's own `sync_fn` (e.g. "asset not found") would have been silently
   swallowed into an opaque 503 — invisible until now because `routes/watchlist.py`/
   `routes/positions.py` both happen to route their own "not found" cases around the wrapper
   entirely (via a `ValueError`/pre-check, never a raw `HTTPException` inside `sync_fn`). Fixed at
   the shared-wrapper level rather than avoided in this module, since any future caller would hit
   the same silent-swallow trap; zero behavior change for the two existing callers.
   Two of the read queries (`list_assets`, `get_networth`) need each asset's *latest* valuation —
   written as a correlated scalar subquery, not a `LEFT JOIN LATERAL`, since SQLite (this
   codebase's test backend for these tables) doesn't parse the `LATERAL` keyword at all. At this
   feature's real scale (a personal, single-household tool — see point 4) the extra correlated
   lookup per asset is not a meaningful cost next to Postgres's own query planner.
3. **Frontend** (`frontend/app/portfolio-aggregator/page.tsx`) — a profile picker (selection
   persisted in `localStorage`, distinct key from anything the positions/watchlist features use)
   gates a per-profile view: a net-worth header card (total + per-type breakdown), and an
   account/asset list with inline add-account/add-asset forms and an inline "edit value" control
   per asset (posts a new valuation row, never edits history in place). Proxy route
   (`frontend/app/api/portfolio/[...path]/route.ts`) is a catch-all forwarding every sub-path to
   the backend — Next.js resolves the sibling static route
   `frontend/app/api/portfolio/concentration/route.ts` first for its own exact path, so the two
   coexist without conflict.
4. **No auth, by design** — inherited unchanged from this feature's original design intent: a
   personal, localhost/Tailscale-only tool for a household or small circle, not a multi-tenant
   product. `profiles` is a bare picker (no credentials, no password), not this app's real
   account system (`users`/`sessions`/magic-link auth, see "Account & magic-link auth flow"
   above) — the two are unconnected on purpose. This is a disclosed, deliberate scope call, the
   same instinct as this codebase's other "explicitly out of scope for now" notes elsewhere in
   this doc, not an oversight: adding real auth here is a bigger, separate decision (who are the
   users, what does a signed-in session even mean for a household-shared net-worth view) that
   this increment doesn't make on its own.
5. **Explicitly out of scope for this increment** (tracked as later, dependent work): charts and
   reports. Automatic pricing/valuation refresh and XIRR are no longer deferred — see "Portfolio
   valuation engine" below — and neither is `transactions` writers: CAS PDF import and broker
   CSV/XLSX import (further below) both write into it now, so XIRR returns real numbers once
   either import path has run for an asset.

### Portfolio valuation engine (`portfolio_valuation.py`)

Closes two of the five gaps point 5 above used to list: the Portfolio Aggregator's `valuations`
were entirely manual, and `transactions`/XIRR had no reader at all. This sub-project auto-values
`mf`/`stock` holdings from the EOD price store (above) and adds an XIRR engine that returns real
numbers as soon as something writes into `transactions` — not yet true today (CAS/broker import,
the actual writer, remains the one deferred piece from point 5).

1. `refresh_valuations(engine)` — for every non-archived `mf`/`stock` asset with a `holdings`
   row: a `stock` asset is valued off `prices_daily.close` for its `symbol` (the same EOD price
   store the "EOD price store + corporate actions flow" section above populates), falling back to
   a direct live yfinance quote (`.NS` then `.BO`) when the symbol has no `prices_daily` row yet
   (a brand-new listing the nightly bhavcopy hasn't ingested, or a symbol typo'd against the
   equity master); a `mf` asset is valued off `mf_nav_daily.nav` for its AMFI scheme code. `value
   = units × price`, upserted into the existing `valuations` table (`asset_id`, `as_of=today`) —
   no new table, same "history from day one, same-day upsert" semantics the foundation section
   above already established. A per-asset miss (no price anywhere) is skipped with a logged
   warning; the asset's prior valuation stands untouched rather than being zeroed or guessed.
   Returns `{"valued": n, "skipped": n, "details": [...]}`.
2. Wired into `eod_prices_pipeline.py::run()` as a fourth, isolated final step — after the
   bhavcopy ingestion, NAV ingestion, and corporate-actions steps — so a fresh EOD close/NAV
   feeds same-day valuations on the very next scheduled run. Same isolation convention as the NAV
   and corporate-actions steps already use: its own `try/except` with its own `log_event()` call,
   so a valuation-engine failure can never affect the pipeline's own exit code. Also runnable
   standalone (`python portfolio_valuation.py`) or on demand via the endpoint below.
3. `xirr(cashflows: list[tuple[date, float]]) -> float | None` — Newton's method with a bisection
   fallback, rate bounded to `[-0.99, 10.0]`; `None` (never a guessed/zero rate) for fewer than 2
   flows, all-same-sign flows, or non-convergence. A pure function with no I/O, so it's fully
   unit-testable without a DB. `xirr_report(engine, profile_id)` builds each asset's cashflows from
   `transactions` (`buy` → −amount, `sell`/`dividend` → +amount, everything else ignored — matching
   the sign convention the not-yet-built CAS/CSV importer will write), appends the asset's latest
   `valuations` row as the terminal flow, and returns both a per-asset and a pooled portfolio-level
   XIRR. An asset with no transactions is `null` and excluded from the pooled calculation — it
   contributes nothing to a rate-of-return figure it has no return history for.
4. `POST /api/portfolio/refresh-valuations` and `GET /api/portfolio/xirr?profile_id=` in
   `routes/portfolio_aggregator.py`, following that module's existing conventions exactly (same
   `run_owned_db_call()` wrapper, same rate-limit tiers as its sibling read/write endpoints).
   `frontend/app/portfolio-aggregator/page.tsx` gained a "Refresh valuations" button next to
   "Switch profile" that POSTs to the refresh endpoint and shows "Valued n, skipped n." before
   reloading the page's own account/asset/net-worth data. No XIRR display and no chart yet —
   deliberately deferred to a future dashboard increment, the same "don't build a UI for numbers
   that are still null for everyone" instinct as not building a CAS importer before this engine
   existed to consume its output.

### CAS PDF import (`cas_import.py`)

The first real writer into `transactions` — closes the gap the valuation engine section above
flags: XIRR had a formula but nothing to compute it from. Imports a CAMS/KFintech **detailed**
CAS PDF (every MF transaction since inception, plus folios and closing balances) — NSDL/CDSL
depository CAS and summary CAS are explicitly out of scope, since neither carries the
transaction-level history XIRR needs.

1. `parse_cas(pdf_bytes, password)` wraps the `casparser` library (new `requirements.txt`
   dependency, pulls `pdfminer.six`) — never raises, returns `{"error": ...}` on a wrong password,
   an unparseable PDF, or a summary-only statement (rejected with a message telling the user to
   request the detailed statement instead). The password lives in memory only for the duration of
   this call — never logged, never stored.
2. `import_cas(engine, parsed, account_id)` does all writes in **one transaction** — reconciles
   each CAS scheme against existing `mf` assets by AMFI scheme code, then ISIN fallback; unmatched
   schemes become new `mf` assets under the given account (`meta = {isin, folio, rta}`). A closed
   folio (zero closing balance) with real transaction history is still created — XIRR needs the
   full history — but `archived=true` so it's excluded from net worth; a closed folio with *no*
   transactions is skipped entirely (nothing worth keeping). `holdings.units` is upserted to the
   CAS closing balance for every open scheme. Transaction mapping (casparser type →
   `transactions.type`): `PURCHASE`/`PURCHASE_SIP`/`SWITCH_IN(_MERGER)` → `buy`;
   `REDEMPTION`/`SWITCH_OUT(_MERGER)` → `sell`; `DIVIDEND_PAYOUT` → `dividend`;
   `DIVIDEND_REINVEST` → `dividend_reinvest` (stored, but `portfolio_valuation.xirr_report`'s
   `_FLOW_SIGNS` map doesn't recognize it, so it carries no cashflow — a reinvested dividend isn't
   money leaving or entering the portfolio); `STT_TAX`/`STAMP_DUTY_TAX`/`TDS_TAX`/`MISC` and any
   unmapped type are skipped and counted, not silently dropped. Every row gets
   `meta = {"source": "cas", "folio": ...}`.
3. **Re-import is idempotent by replacement, not by dedup**: unlike the CSV importer below (which
   appends and dedupes because tradebooks are date-ranged partials), a fresh CAS PDF is always the
   *complete* statement for that folio, so `_write_transactions()` deletes every existing
   `meta.source='cas'` row for the matched/created asset first, then inserts the statement's rows
   fresh. A manual transaction or a CSV-sourced one for the same asset is untouched — the delete is
   scoped to `meta.source='cas'` specifically, filtered via SQLAlchemy's JSON comparator
   (`transactions_t.c.meta["source"].as_string()`, portable across the Postgres/SQLite backends
   this codebase's tests and production both use).
4. `archive_parsed()` writes the parsed JSON, scrubbed of PAN/KYC/investor identity, to
   the `cas_archive` namespace in `state_store.py`, keyed `YYYY-MM-DD-HHMMSS` — same replay-archive intent as `output/_bhavcopy/`
   the EOD price store already established, so an import can be replayed for debugging without
   re-uploading the PDF: `python cas_import.py --replay <file> --account-id N`. The PDF bytes
   themselves are never written to disk.
5. `POST /api/portfolio/import-cas` (multipart: `file`, `password`, `account_id`) in
   `routes/portfolio_aggregator.py` — 422 on a parse error, 404 on an unknown account. On success
   it archives the parse and calls `refresh_valuations()` (from `portfolio_valuation.py`) before
   returning, so newly-imported/matched schemes with an existing NAV get an immediate valuation
   rather than waiting for the next nightly pipeline run. Frontend: an "Import CAS" button on
   `/portfolio-aggregator` (file/password/account-select inline form) shows a one-line summary
   plus any warnings.
6. **Multipart passthrough**: `frontend/app/api/portfolio/[...path]/route.ts`'s catch-all proxy
   previously always forwarded a JSON-text body with a hardcoded `Content-Type: application/json`
   — the right behavior for every other portfolio-aggregator endpoint, but wrong for a file
   upload. It now detects a `multipart/form-data` request (this import endpoint and both CSV
   endpoints below) and forwards the raw body plus the original `Content-Type` (which carries the
   multipart boundary FastAPI needs to parse it) instead.

### Broker CSV/XLSX import (`csv_import.py`)

CAS covers mutual funds only — demat stock buy/sell transactions live in broker exports, and
export formats vary with no reliable per-broker sample set available, so this is a **generic
column-mapping importer** (Zerodha's well-known tradebook auto-detected) rather than one hardcoded
parser per broker.

1. `parse_broker_file(file_bytes, filename)` — `.xlsx` via `pandas.read_excel` (new
   `requirements.txt` dependency `openpyxl`, the engine `read_excel` needs; `pandas` itself was
   already a dependency), everything else via stdlib `csv.reader` with `csv.Sniffer` delimiter
   detection (comma/semicolon/tab, falling back to comma). Never raises; `{"error": ...}` on an
   empty file or a genuinely unreadable spreadsheet.
2. `suggest_mapping(headers)` — detects a Zerodha tradebook by its exact header signature
   (`isin`/`trade_date`/`trade_type`/`quantity`/`price` plus `symbol` or `tradingsymbol`, all
   case-insensitive) and returns the full mapping with `detected: "zerodha"`; otherwise guesses
   each of the 5 required fields (`date`/`symbol`/`side`/`quantity`/`price`) plus 2 optional ones
   (`amount`/`isin`) by normalized header-name containment (e.g. any header containing "qty" or
   "quantity" → quantity).
3. `import_rows(engine, rows, headers, mapping, account_id, broker)` — normalizes each row (date:
   first match among 5 accepted formats; side: `buy`/`b`/`bought`/`purchase` or
   `sell`/`s`/`sold`, case-insensitive; quantity/price/amount: strips commas and ₹/Rs. before
   `float()`) and skips + warns (with the 1-indexed row number) on anything unparseable rather than
   aborting the whole file. **Appends, never deletes** — a broker tradebook export is a
   date-ranged partial, not a full restatement like a CAS PDF, so a row is counted as a duplicate
   (not written) only when an existing `meta.source='csv'` transaction for the same asset already
   has an identical `(date, type, units, amount)` — re-uploading the same file, or an overlapping
   date range from a fresh export, is safe to re-run.
4. **New-asset resolution wired through `tools.securities_master.resolve_symbol()`** (built, but
   unwired, by the securities-master-resolver task — this is its intended integration point): when
   a row's symbol doesn't match an existing `stock` asset by symbol or ISIN, the broker's raw code
   is resolved against the merged NSE-main-board/BSE-main-board/SME securities master before a new
   asset is created. An `"isin"` or `"exact"` confidence tier substitutes the verified NSE/BSE
   symbol (storing `{isin, broker, resolved_exchange}` in `meta`); `"fuzzy"` or `"unresolved"`
   keeps the broker's raw code as-is — never a silent guess — and adds a warning naming the
   symbol and, for a fuzzy hit, the closest-match company name for a human to eyeball. The
   securities master is loaded once per `import_rows()` call (not once per row) and threaded
   through every `resolve_symbol()` call via its `master=` parameter, since a full securities-table
   scan plus fuzzy-candidate rebuild per row would be wasteful for a multi-row tradebook.
5. **Derived units, not stated units**: broker tradebooks show individual trades, not a running
   position, so after every import `holdings.units` is recomputed as `Σ buy units − Σ sell units`
   across *all* of that asset's transactions (any source, not just this upload) — a negative
   result (an incomplete tradebook missing earlier buys) is floored to 0 with a warning rather than
   stored negative. A standing warning on every import notes that derived units exclude
   bonus/split shares (tradebooks never show them) and should be verified against the broker's own
   app — correctable via the existing asset `PATCH` endpoint.
6. `POST /api/portfolio/import-csv/preview` (multipart: `file`) returns headers, the first 5 rows,
   the suggested mapping, and the detected broker (if any) — no DB write. `POST
   /api/portfolio/import-csv` (multipart: `file`, `mapping` as a JSON string, `account_id`,
   `broker`) validates the 5 required mapping fields are present (422 otherwise), imports, then
   calls `refresh_valuations()` on success, same as the CAS endpoint above.
7. Frontend: an "Import CSV" button on `/portfolio-aggregator` — picking a file previews it
   immediately (headers/suggested mapping/Zerodha detection), rendering a mapping grid (one
   `<select>` of the file's own headers per target field). A confirmed mapping is cached in
   `localStorage` keyed by the lowercased-headers-joined-by-`|` signature and auto-applied on the
   next upload with the same header shape — the same "remember what the user confirmed" instinct
   as this codebase's other `localStorage`-cached preferences (e.g. `watchlist.ts`'s `client_id`).
   Import posts and shows a one-line summary (`imported n, duplicates n, skipped n`) plus any
   warnings.

### Shared-state rate limiting (`rate_limiter.py`)

Three pieces of backend guard state were previously **single-process, in-memory, by design** —
each backend worker/replica held its own counter, so the documented per-IP rate limits, the LLM
concurrency ceiling, and the SME refresh guard all silently became *per-worker* the moment the
backend ran with more than one worker (see `docs/deployment.md`'s "Scaling" section, which
flagged this as the blocker to scaling past a single process).

1. `rate_limiter.py` (repo root, alongside `cache.py`) is a small shared module with three
   primitives — `is_allowed(key, max_calls, window_seconds)` (sliding-window rate limit),
   `try_acquire_slot(name, limit)` / `release_slot(name)` (named concurrency ceiling), and
   `try_acquire_lock(name, ttl_seconds)` / `release_lock(name)` / `is_locked(name)` (single-run
   lock) — each backed by a small Lua script (rate limit, slot) or `SET NX EX` (lock) for atomic
   check-and-set against Redis, so two workers hitting the same key at the same instant can't
   both succeed.
2. **Graceful degradation, not a hard dependency**: every primitive falls back to the exact same
   in-memory implementation this app had before Redis support existed whenever `REDIS_URL` is
   unset, or a Redis call raises (network blip, Redis down) — logged as a warning
   (`redis_rate_limit_failed` etc.) and swallowed, the same "missing optional infra degrades
   rather than breaks" convention as `DATABASE_URL`/`SMTP_HOST` elsewhere in this codebase. A
   single-process deployment behaves identically with or without `REDIS_URL` set.
3. `api.py`'s `_check_rate_limit()`, `_acquire_llm_slot()`/`_release_llm_slot()`, and the
   `/api/sme-signals/refresh` endpoint's run-guard are now thin wrappers over these three
   primitives — same call sites, same `429`/capacity-rejection/`409` response shapes as before,
   just backed by shared state instead of a module-level dict/counter/bool. The SME refresh
   endpoint's lock-then-rate-limit ordering was preserved exactly (a `try_acquire_lock()` success
   followed by a rate-limit rejection releases the lock before returning 429), matching the
   original code's "409 takes priority over 429 when both would apply" behavior.
4. **Crash recovery**: a Redis-held slot or lock carries a TTL (`_SLOT_TTL_SECONDS` = 600s for
   slots; the SME refresh lock uses its own 3600s, matching how long one pipeline run can
   reasonably take) so a worker that crashes mid-hold — skipping its `release_*()` call — doesn't
   permanently strand that slot/lock. The in-memory fallback has no TTL, since a process crash
   there already resets all in-memory state, making one redundant.
5. Docker Compose gained a `redis` service (`redis:7-alpine`, persisted via a named volume) and
   wires `REDIS_URL` into the `backend` service automatically — a manual/non-Compose deployment
   only needs to set `REDIS_URL` once it scales past one worker (see `docs/deployment.md`).

### Redis-backed cache for multi-host deployments (`cache.py`)

The rate-limiter work above fixed *guard state* being per-worker — but `cache.py`'s local-disk
JSON cache is this app's documented **persistent shared state** for every scraped data slice
(`stock_info`, `research`, `news`, `peers`, `financials`, ...), and a CTO-lens review flagged it
directly as the real ceiling on horizontal scale: it works today because there's one host, but
past a second host/replica without a shared disk volume, every cache silently forks per instance,
multiplying scraper load on the exact fragile third-party sources (Screener.in, NSE, Trendlyne,
RBI) this codebase already treats cautiously everywhere else in this doc.

1. `cache.py` gained the same opt-in `REDIS_URL`-gated Redis backing as `rate_limiter.py`, with
   its own independent lazily-constructed client (duplicated, not imported, from
   `rate_limiter.py`'s client-getter — `cache.py` is imported by nearly every other module in
   this codebase, so it deliberately doesn't take on a dependency on a sibling module for
   something this small). `save()` writes through to Redis (`SET ... EX <TTL_HOURS-derived
   seconds>`) *in addition to* local disk, never instead of it — disk stays the persistent store
   on a single-host deployment and becomes a fast local mirror/fallback once Redis is configured.
2. **The core invariant**: once Redis has *any* opinion on a key — fresh, stale, or a cached
   failure — every host must agree with it. `load()`/`is_fresh()` check Redis first and only
   fall back to this host's own disk copy when Redis has **no entry at all** for that key (a
   Redis outage, an eviction, or a key predating `REDIS_URL` being set). A stale/failed Redis
   entry is trusted as-is and never overridden by a possibly-fresher local disk copy — falling
   through in that case would silently reintroduce the exact per-host fork this feature exists
   to close (host A's disk might have a fresher `research` blob than what Redis/host B last
   wrote, and serving it would mean the two hosts disagree on freshness again).
3. Freshness is always re-derived from each entry's own `_meta.fetched_at` against the current
   `TTL_HOURS` map — never trusted from a store's own expiry mechanism (Redis's `EX`, a file's
   mtime) — so a `TTL_HOURS` tuning change takes effect immediately for entries already written,
   on both backends, without needing them to be rewritten. Same graceful-degradation convention
   as everywhere else: a Redis read/write failure logs a warning (`cache_redis_read_failed`,
   `cache_redis_write_failed`) and falls back to disk for that one call; a single-host deployment
   behaves identically with or without `REDIS_URL` set.
4. Verified in this sandbox against a real local Redis instance (not just the mocked unit tests in
   `tests/test_cache_redis.py`): two separate Python processes, each pointed at its own,
   completely separate local disk directory (simulating two hosts with no shared volume), with
   `cache.save()` in process A immediately visible to `cache.load()` in process B — process B
   never touches its own (empty) disk directory at all on that read, confirming the fork this
   feature exists to close is actually closed, not just plausible on paper.
5. See `docs/deployment.md`'s "Scaling" section for the operator-facing version of this same
   story, including the updated guard-state table.

### Trusted client IP for per-IP rate limiting

The Redis-shared limiter above fixed rate-limit state being *per-worker* — but every one of
`api.py`'s per-IP buckets was still being keyed off `request.client.host`, and every request
reaches this backend via the Next.js proxy routes, server-to-server (see "Proxy routes" below).
That means `request.client.host` is always the Next.js server's own IP, never the real visitor's
— collapsing every per-IP limiter (`/api/analyse`'s 20/5min, `/api/auth/request-link`'s 5/15min,
etc.) into one shared bucket for the entire site regardless of how many distinct visitors are
actually calling it, the opposite of what a *per-IP* limit is for.

1. `api.py::_client_ip(request)` only trusts a caller-supplied client IP when the request also
   presents a matching shared secret — `TRUSTED_PROXY_SECRET` (env var, unset by default) —  via
   the `X-Internal-Proxy-Secret` header. When it matches, the first address in `X-Forwarded-For`
   is used as the client IP; otherwise (no secret configured, or a mismatch) it falls straight
   back to `request.client.host`, i.e. today's behavior. `_rate_limit()` now calls this instead of
   reading `request.client.host` directly — its only call site.
2. `frontend/lib/proxy-headers.ts::clientIpHeaders(req)` is the frontend half: every one of the
   ~25 Next.js proxy routes under `frontend/app/api/*` now merges this into the headers on its
   `fetch()` call to the backend. It reads the real client IP off whatever's in front of the
   Next.js server in production (a reverse proxy/CDN/load balancer — see `docs/deployment.md`)
   via the standard `X-Forwarded-For` header the request arrived with, and forwards it — plus
   `TRUSTED_PROXY_SECRET` from `process.env` — to the backend. Both env var and header are
   optional; a route with neither set sends no extra headers and the backend behaves exactly as
   before.
3. **Why a shared secret, not just trusting `X-Forwarded-For` outright**: the backend's port isn't
   inherently unreachable except through the Next.js proxy — without the secret check, any direct
   caller could set an arbitrary `X-Forwarded-For` value to dodge its own rate limit, or to frame
   an innocent IP into being blocked. The secret proves the forwarded value really came from this
   deployment's own Next.js server, which is the only thing that knows it.
4. Deliberately scoped to rate limiting only — this does *not* change what IP address ends up in
   any log line or stored record; `observability.log_event()` call sites are unaffected.
5. Local Docker Compose exposes the frontend container directly (no reverse proxy in front of it),
   so `TRUSTED_PROXY_SECRET` is a documented no-op there by default — both the `backend` and
   `frontend` services pass it through from the host's `.env` (`${TRUSTED_PROXY_SECRET:-}`) so a
   self-hosted deployment that does add a reverse proxy in front of the frontend container can set
   one value in `.env` and have it reach both services unchanged.

### Error tracking / APM hook (`error_tracking.py`)

Every error-level `observability.log_event()` call already carries a structured JSON payload
(`event`, and whatever `**fields` the call site attached — `symbol`, `run_id`, `error`, etc.), but
until now it only ever reached stdout/the process log. There was no way to get paged, deduped,
or grouped-by-stack-trace on a production error without grepping logs after the fact.

1. `error_tracking.py` (repo root, alongside `cache.py`/`rate_limiter.py`) is a small pluggable
   hook gated behind the optional `SENTRY_DSN` env var — unset by default, so `log_event()`
   behaves exactly as before with zero behavior change out of the box. "Pluggable" here means
   swappable ingest endpoint (real Sentry, self-hosted Sentry, GlitchTip — anything that speaks
   the same DSN/`init()` protocol), not a plugin registry of multiple simultaneous backends; this
   codebase has exactly one thing that consumes errors today (`log_event`'s error-level path), so
   a heavier abstraction on top of that would be speculative.
2. `init_error_tracking()` is called once per process at every entry point that can emit an
   error-level `log_event()` — `api.py` (module-level, right after `LOGGER = get_logger("api")`,
   so it runs once per worker process) and the CLI `main()` of `main.py`, `sme_ema_pipeline.py`,
   `market_picks_pipeline.py`, `watchlist_alerts.py`, and `screener_pipeline.py`. It's idempotent
   (a second call is a harmless no-op, guarded by a module-level `_initialized` flag) since
   `sentry_sdk.init()` itself isn't safe to call twice with different configs — this matters
   because e.g. `watchlist_alerts.py` imports `main.py` (for `_fetch_task`), and `api.py`'s
   background SME/screener refresh endpoints run those pipelines' `run()` functions in-process
   inside the already-initialized API server, not through their CLI `main()` at all.
3. **Same graceful-degradation convention as `DATABASE_URL`/`SMTP_HOST`/`REDIS_URL`** elsewhere in
   this codebase: unset `SENTRY_DSN`, a missing `sentry-sdk` package (logged once via stdlib
   `logging`, not `observability.log_event` — this module is a dependency *of* observability.py,
   so routing its own diagnostics back through `log_event` would be circular), or a failed
   `sentry_sdk.init()`/capture call all degrade to a silent no-op rather than breaking the
   request/batch job that triggered the error in the first place. `sentry-sdk` is still a hard
   `requirements.txt` dependency (same pattern as `redis` — always installed, behavior gated by
   the env var) rather than conditionally installed, so there's no separate install step once a
   deployment is ready to set `SENTRY_DSN`.
4. `observability.log_event()`'s error-level path (`level="error"`, the existing convention every
   call site already uses) forwards `(event, fields, exc)` to `error_tracking.capture_error()`,
   wrapped in its own try/except so a broken/unreachable Sentry backend can never break the
   primary structured-logging path `log_event` exists for. `log_event()` gained a new optional
   `exc: BaseException | None` keyword — existing call sites are unchanged (still passing
   `error=str(exc)` as a field, which is what the log line itself shows); passing the actual
   exception object too is opt-in and only worth doing at a handful of the most valuable
   top-level `except Exception as exc:` sites, since it's what gives Sentry a real grouped stack
   trace instead of just a message string.
5. `capture_error()` tags the Sentry event with the `event` name, attaches every other field as
   Sentry "extra" context (skipping `error` itself — that string just duplicates what
   `capture_exception`'s own stack trace already conveys), and calls `capture_exception(exc)` when
   an exception object was passed, or `capture_message(event, level="error")` otherwise.
6. `sentry_sdk.init()` is called with an explicit `integrations=[LoggingIntegration(event_level=None)]`
   override — without it, the SDK's own default `LoggingIntegration` auto-captures *any*
   `logger.error()`/`.critical()` call as its own event, including the plain log line
   `log_event()` already emits immediately before it calls `capture_error()` — so every
   error would otherwise ship as two separate, differently-shaped Sentry events (one
   well-tagged, one a raw JSON-message duplicate with no `event` tag or exception attached).
   `capture_error()` also uses `sentry_sdk.new_scope()`, not the older `push_scope()` — the
   latter is deprecated as of `sentry-sdk` 2.x and logs a `DeprecationWarning` on every call.
   Both were caught (and are regression-tested) by actually initializing the real, installed
   `sentry-sdk` package against a custom in-memory `Transport` subclass and asserting exactly
   one envelope is captured per error — most of `tests/test_error_tracking.py` mocks
   `sentry_sdk` at the `sys.modules` level (the same crewai-mocking pattern `tests/conftest.py`
   already documents), which verifies this module's own call shapes but can't catch a real SDK
   behavior mismatch like these two; `RealSdkRegressionTest` exists specifically to close that
   gap by running against the real package instead.
7. **Disclosed limitation**: `sentry_sdk.init()`'s actual behavior against a live Sentry
   project — DSN parsing, event delivery, what a captured event looks like once ingested —
   was not verified against a real Sentry account in this sandbox (no outbound internet to
   sentry.io; same disclosure as the FII/DII/RBI scrapers and the sector-taxonomy assumption
   elsewhere in this doc). `RealSdkRegressionTest` (point 6 above) verifies the real SDK's
   *client-side* behavior — what gets handed to its transport layer — not that a live ingest
   endpoint actually accepts and stores it.

### Schema-drift detection (`schema_drift.py`)

The six data slices (`stock_info`, `research`, `news`, `shareholding`, `mf_holdings`,
`filings`) are all scraped, and tools never raise (see "Important Rules for Claude" below) —
a scraped source restructuring its HTML/JSON (Screener.in renaming a table, NSE changing a
field) doesn't crash the fetch, it just silently returns something under the expected key
that's no longer the expected *shape*. `schemas.CONTRACTS`'s existing `"required"` list only
checks presence, and most other fields are legitimately absent per-symbol by this codebase's
own "never invent" convention — so a naive "did the key set change" check would be constant
false-positive noise on exactly the symbols/fields this convention already expects to be thin.

1. `schemas.CONTRACTS` gained an optional `"types"` entry per task — a `{field: type}` map for
   *container-shaped* fields only (`dict`/`list`), e.g. `research: {"ratios": dict,
   "quarterly_trend": dict}`. This is the single source of truth `schema_drift.py` reads from —
   no second hand-maintained field list to drift out of sync with `schemas.py` itself.
2. `schema_drift.check_drift(task_name, raw_data)` is a pure function: for each field in that
   task's `"types"` map that's *present* in `raw_data`, checks its Python type matches. A field
   that's simply absent (the common, legitimate "never invent" case) is skipped, not flagged —
   this only fires when a field is present but has changed shape (e.g. `ratios` coming back as a
   `list` instead of a `dict`), which is never a legitimate per-symbol variation and is exactly
   the case that breaks every downstream `.get()`/iteration call written for the declared shape,
   often silently (many call sites are themselves defensively wrapped, so a shape flip can
   degrade a section to "missing" several layers away from where the drift actually happened).
3. `schema_drift.log_drift_if_any(task_name, raw_data, **context)` wraps `check_drift()` in a
   try/except that never raises — matching the "tools must not raise" convention even though
   this isn't a tool itself, since it's called from the same fetch loop a real tool failure
   already can't be allowed to break. When drift is found it calls `observability.log_event()`
   at `level="warning"` (not `"error"` — this needs a human to look at the scraper, not an
   on-call page through the Phase 15 Sentry hook) with the field-level problem descriptions plus
   whatever `run_id`/`symbol` context the caller passed through.
4. Wired into `main._fetch_task()` — the single choke point all six data-slice fetches already
   go through for both the CLI and `api.py`'s SSE endpoint (`main.py`'s own module docstring:
   "also contains `_fetch_task`... shared with `api.py`") — right after a successful
   `tool_attempt_succeeded` log, on both the raw-dict and parsed-JSON-text success paths. No
   other call site needed changing to get coverage across every entry point that fetches these
   six slices.
5. Deliberately scoped to only these six "data slices" (the term CLAUDE.md's own "Project
   Overview" section already uses) — not the growing set of standalone scrapers outside
   `ALL_DATA_TASKS` (peers, insider activity, street consensus, FII/DII flow, macro context,
   valuation band, NIFTY 500 constituents, SME stock lists). Those already carry their own
   disclosed-limitation notes elsewhere in this doc about being unverified against live
   responses in this sandbox; extending drift detection to them is future work, not silently
   assumed to already be covered by this pass.

### Live scraper contract checks (`tests_live/`)

`schema_drift.py`/`source_health.py` above only ever learn a scraper broke from *production*
traffic, after the fact — there was previously no earlier, narrower signal, and this repo's own
docs disclose roughly a dozen scraper assumptions (Screener's section ids, Trendlyne's DOM
labels, NSE's XBRL field names, RBI's table layout, the NIFTY 500 CSV shape, the sector-taxonomy
guess in `signals/engine.py`) that were never actually checked against a live response in this
sandbox (no outbound internet to non-allowlisted hosts, repeated throughout this file).

1. `tests_live/test_scraper_contracts.py` is a **second, independent test root** — deliberately
   not inside `tests/`, since `python -m pytest tests/` (the command this repo's CI and this
   file both document) must never make a live network call, matching every other test in this
   codebase. It covers the four highest-blast-radius scrapers: Screener.in's peer table (feeds
   fundamentals, peers, DCF, and the analyst prompt simultaneously), Trendlyne's symbol
   resolution, NSE's FII/DII flow, and RBI's rate/inflation table.
2. Opt-in via `RUN_LIVE_TESTS=1` (checked in each test's `setUp`) — running `pytest tests_live/`
   without it is a clean, immediate skip, so this can never accidentally fire from a local
   `pytest` invocation or an unrelated CI job.
3. **Connectivity is checked before the scraper, not inferred from its result.** Every tool
   function in this codebase follows the "tools never raise" convention (see "Important Rules
   for Claude" below) — a connectivity failure and a genuine site-layout change both surface the
   same way, as a returned `{"error": ...}` dict, so pattern-matching the tool's own output to
   tell them apart would be unreliable. Each test instead makes its own minimal, direct
   `requests.head()` probe to the target host first; only once that succeeds does a
   still-returned `"error"` (or a missing expected field) count as a real contract failure.
   Confirmed while building this: this sandbox's own outbound proxy 403s the CONNECT tunnel for
   all four target hosts, so every test correctly skips here rather than reporting a false
   pass or a false failure — the exact failure mode a naive live test would have hit.
4. `.github/workflows/live-contract-check.yml` runs this weekly (`workflow_dispatch` also
   available for an on-demand run) — low frequency deliberately, since this is an early-warning
   signal for the scrapers' *shape*, not a data-collection job. A genuine contract break fails
   the Actions job and fires GitHub's own run-failure notification, the same "let a bad run fail
   loudly" convention `sme_ema_pipeline.py`'s own health gate already established.
5. **Explicitly does not close the gap for every disclosed-but-unverified assumption** — four
   scrapers, not all ~10 standalone ones outside `ALL_DATA_TASKS`. A starting point at the
   highest-blast-radius sources, not full coverage.

### Source freshness/volume monitoring (`source_health.py`)

`schema_drift.py` above only catches *type* drift on the six `ALL_DATA_TASKS` fields — it has
nothing to say about a source that's still returning well-shaped data but has silently gone
quiet (0 results every run), since an empty result isn't a shape mismatch. That failure mode was
otherwise invisible for the 20 Market Picks `SOURCES` and the two market-wide macro-overlay
fetches: `_SOURCE_CREDIBILITY` weights every source into confidence scoring, so a dead source
doesn't error, it just quietly stops contributing to every future pick's score.

1. `source_health.record_and_check(source_name, ok, **context)` records this run's boolean
   ok/not-ok result, under today's UTC calendar day, to one record per source under the
   `source_health` namespace in `state_store.py` (this used to be a per-source JSON file
   under `output/_source_health/`), then warns via
   `observability.log_event(level="warning")` once a source that had an established healthy
   baseline (≥5 prior days, at least one of which succeeded) has now failed 3 consecutive
   *days* in a row. Never raises — a broken health-tracking file must not break the
   scrape/pipeline run it's trying to observe.
2. **Time-normalized, not raw-call-count**: several calls for the same source on the same UTC
   calendar day collapse into a single entry for that day (keeping the latest result) rather
   than each counting as its own data point — otherwise a burst of same-hour `?force=true`
   retries could trip the "3 consecutive failures" threshold in minutes, while the intended
   weekly-cron cadence would need 3 *weeks* to ever flag a genuinely dead source. `date` is an
   optional keyword-only override (defaults to `None` → today) that exists purely so tests can
   simulate distinct calendar days without sleeping in real time — deliberately an explicit
   per-call argument rather than a patchable module-level "now" function, since a process-wide
   monkeypatch of a shared function is itself racy across the concurrent-caller tests this
   module needs (one thread's patched value can leak into another's call).
3. **Concurrency-safe**: the whole read-modify-write cycle for one source is guarded by
   `state_store.mutate()`'s row lock, keyed per source. Without it, two callers racing to
   update the same source at once — e.g. several `market_picks_pipeline.py` `_phase_scrape`
   workers, or the same `signals/macro.py` cache-miss race across worker threads CLAUDE.md's
   "Shared state and queues" section already documents for `fii_dii_flow`/`macro_context` — is a
   classic lost-update race: both read the same prior history, both write their own updated
   version, and the second write silently clobbers the first caller's update, including
   resetting the rolling baseline. This was previously an `fcntl.flock` advisory lock over a
   JSON file; the row lock also holds across separate *hosts*, which `flock` never did.
4. Deliberately **not** wired into the three genuinely per-symbol standalone endpoints (peers,
   insider activity, street consensus) — most individual stocks legitimately have zero insider
   trades or zero Trendlyne-cited coverage on a given day, which is this codebase's own
   documented "expected common case" everywhere else in this doc, not a source-health anomaly.
   Applying the same volume-anomaly heuristic there would just be noise, not a signal.
5. A new source (fewer than 5 prior days, or no successful day yet) never alerts — there's no
   established baseline yet to regress from, and a source that's simply always been empty (e.g.
   genuinely thin coverage) shouldn't page anyone either.

### Source-quality telemetry (`source_quality.py`)

`source_health.py` (day-level freshness/volume) and `scraper_error_counters.py` (error counting
for the 4 standalone per-symbol endpoints) both answer "is this source broken" — neither gives a
per-*run* view of "how many articles did source X yield this run, how many did the LLM extract a
pick from, how many of those survived NSE-symbol validation into a real consolidated pick." A
source that's technically "healthy" (returns articles every run) but whose picks never survive
validation is invisible to both of those modules.

1. `source_quality.record_run(run_id, source_stats)` writes one JSON file per pipeline run to
   the `source_quality` namespace in `state_store.py`, keyed by run id (same convention
   as `cache.py`/`source_health.py` — no lock needed since each run writes its own uniquely-named
   file, unlike those modules' shared per-source file). Never raises — a telemetry write failure
   must never affect a real pipeline run.
2. `market_picks_pipeline.py::_aggregate_source_stats(raw_sources, raw_picks, consolidated)`
   tallies three counts per source, keyed off `tools.market_picks_tools.SOURCES` (every registered
   source always present, even at zero activity — a source name that doesn't match the registry,
   e.g. stale data, is ignored rather than creating an untracked key): `articles_fetched` (phase 1
   output), `picks_extracted` (how many of `raw_picks` cite that source), `picks_validated` (how
   many of `consolidated`'s surviving groups cite that source — every item in `consolidated`
   passed NSE-symbol validation by definition, so this is "did this source's extraction actually
   produce a real, tradeable pick," not just "did the LLM produce *something*").
3. `MarketPicksPipeline.run()` calls `_aggregate_source_stats()` and `source_quality.record_run()`
   right after `_phase_score` completes — same placement as `source_health.record_and_check()`'s
   own call site inside `_phase_scrape`, but here it needs the fully-scored pipeline output to
   know which picks actually survived validation, so it can't fire any earlier. Skipped on the
   empty-pipeline early-return path, same as `source_health`'s own per-phase calls.
4. `source_quality_report.py` is a standalone aggregation CLI (`python source_quality_report.py
   --days 14`) — sums the three counts across every run file within the lookback window, computes
   a yield rate (extracted/fetched) and survival rate (validated/extracted) per source, and prints
   a table sorted worst-survival-first (a source with no extractions yet sorts last, not first,
   since there's nothing to be worst *at*). Same "grep-able counter files plus a report script,
   not a metrics dashboard" scope as `scraper_error_counters.py`'s own disclosed scope.

### Standalone scraper error counters (`scraper_error_counters.py`)

Point 4 above deliberately excludes `peers`/`insider-activity`/`street-consensus` from
`source_health.py`'s volume-anomaly heuristic, since an empty result is their expected common
case — but that left those endpoints with genuinely no signal at all when something actually
broke. `fetch_insider_trades_for_symbol(sym).get("trades", [])`-shaped call sites silently
mapped both "NSE returned nothing today" (normal) and "NSE request failed" (a real, silent
degradation — these tool functions never raise, they return `{"error": ...}` instead) to the
exact same empty list, with no log line to grep for either. An engineering-lens review flagged
this directly: "the ~10 standalone scrapers outside [the six-task] path have no structured
logging of their own — a silent layout change there degrades with no log line to grep for."

1. `scraper_error_counters.record_scraper_error(scraper_name, **context)` increments a small
   persisted counter (the `scraper_errors` namespace in `state_store.py`, same
   `os.replace` atomic-write convention as `cache.py`/`source_health.py`) and immediately logs
   a `level="warning"` event — no "N bad days in a row" threshold like `source_health.py`,
   since a single error at one of these on-demand, per-request endpoints already means one
   real user's request degraded, unlike a scheduled batch job where a single bad run is
   expected background noise. Never raises. `get_error_count(scraper_name)` is a non-mutating
   read for tests and a future ops surface.
2. Wired into 6 call sites across the 4 standalone endpoints named in the review — each now
   distinguishes a genuine `{"error": ...}` tool-function result from a legitimate empty one
   before deciding whether to count/log: `GET /api/peers/{symbol}` (`"peers"`),
   `GET /api/financials/{symbol}` (`"financials"`), `GET /api/insider-activity/{symbol}`'s two
   independent sub-fetches (`"insider_trades"`, `"bulk_block_deals"`), and
   `GET /api/street-consensus/{symbol}`'s two independent sub-fetches
   (`"trendlyne_articles"`, `"trendlyne_numeric_consensus"`). A legitimate empty result (no
   `"error"` key) never touches this module — same "don't manufacture noise from the expected
   common case" instinct `source_health.py` already applies.
3. **Deliberately not a full observability platform** — this is a grep-able counter file plus
   a log line, not a metrics dashboard, alerting integration, or a new `/api/*` status
   endpoint. Consistent with this codebase's other disclosed "first increment" scope calls
   (`tests_live/`'s own coverage note, the two-domain `routes/` split above) rather than a
   claim that scraper observability is now fully solved.
4. `signals/engine.py::_log_unmatched_sector_once()` was also promoted from `level="debug"` to
   `level="warning"` in this same pass — a debug-level line is invisible in this codebase's
   default INFO-level deployments, so the sector-taxonomy validation this log line exists to
   enable (see "Sector-aware signal weights" above) could never actually happen against real
   production traffic without someone first turning debug logging on.

### SME golden cross flow

`sme_ema_pipeline.py` is a standalone batch job (PostgreSQL, `DATABASE_URL` env var):

1. Fetches all NSE Emerge + BSE SME stocks (`tools/sme_tools.py`, 24 h list cache)
2. Downloads 1 year of daily OHLCV per stock via yfinance
3. Computes EMA 20/50 over the full year; flags **golden crosses** (EMA20 crosses above
   EMA50) and **death crosses** (crosses below); stores only the last ~3 months of rows.
   Also computes **RSI(14)** (`_compute_rsi()`, Wilder's smoothing) and a **volume-spike**
   flag (`_compute_volume_spike()`: today's volume > 2x its trailing 20-day average) per
   day, stored alongside `ema20`/`ema50` on `ema_signals` — momentum-screener confirmation
   signals a bare EMA cross doesn't provide on its own. Also computes avg daily
   volume/turnover over the last 20 trading days (`_compute_liquidity()`) and market cap
   via yfinance `fast_info` (`_safe_market_cap_cr()`, one extra lightweight request per
   stock — trailing P/E deliberately isn't fetched, since it needs the much heavier full
   `.info` scrape, which across potentially hundreds of SME stocks per run would meaningfully
   add to this pipeline's already rate-limit-sensitive runtime for one inline column) — no
   OHLCV network calls beyond that. Both stored on `sme_stocks` (plain `UPDATE`s via
   `_upsert_liquidity()`/`_upsert_market_cap()`, run after `_upsert_signals()` since neither
   is known until this phase, unlike the stock-list metadata `_upsert_stocks()` writes
   before OHLCV is even fetched)
4. `GET /api/sme-signals` serves cross events + current regime (`ema20 > ema50` on the
   latest row) + each stock's `avg_volume_20d`/`avg_turnover_20d`/`market_cap_cr`/`rsi14`/
   `volume_spike` + a 90-day golden-cross follow-through hit rate; `POST /api/sme-signals/refresh`
   runs the pipeline in the background (409 if already running; `refreshing` flag in the
   GET response)

CLI: `--setup-db` (create tables), `--reset-db` (drop + recreate — required after schema
changes; data is fully regenerable), `--force` (bypass list cache), `--lookback N`.

**`--reset-db` is scoped to this pipeline's own two tables** — `ema_signals.drop()` then
`sme_stocks.drop()` (child before parent, per the FK), then recreate in reverse, then
`stamp_alembic_head()`. It deliberately does **not** call `metadata.drop_all(engine)`.

This used to be the opposite, and was documented here as a disclosed limitation: the single
shared `MetaData()` in `db/models.py` carries every table in the app, so `drop_all` wiped
accounts, sessions, watchlist rows and everything else just to reset two SME tables.
`screener_pipeline.py --reset-db` was scoped to its own table to avoid that; this script has now
been brought in line. The blast radius mattered more once the Portfolio Aggregator landed —
`profiles`/`accounts`/`assets`/`holdings`/`valuations`/`transactions` hold real personal
financial data that is **not** regenerable, unlike this pipeline's own scraped tables.

**Rule for any new pipeline:** scope `--reset-db` to the tables that pipeline owns. Never
`metadata.drop_all()`. See `docs/database.md` for the full table-ownership map.

The DB column for the cross is named `cross_type` (`'golden'`/`'death'`/`NULL`) because
`CROSS` is a reserved SQL keyword; the API/TS field is `cross`.

**Liquidity + illiquid badge**: `avg_volume_20d`/`avg_turnover_20d` on `sme_stocks` are NULL
until the first pipeline run after this feature shipped (never invented for older data). The
`_ILLIQUID_TURNOVER_THRESHOLD` (₹5L avg daily turnover) is a frontend-only constant in
`frontend/app/sme-signals/page.tsx` — a stock below it gets an amber "⚠ Illiquid" badge next
to its Turnover cell (reusing the `hold` design token; there's no separate `warning` token in
this codebase). The threshold decision lives client-side rather than as a stored/computed
backend field, matching how other purely-presentational thresholds (e.g. market-picks'
large/mid/small cap buckets) are handled in this repo.

**Cross outcome (forward returns)**: `GET /api/sme-signals/{symbol}/history` also returns
`cross_events` — every cross in the stored ~3-month window, most recent first, with `ret_10d_pct`/
`ret_20d_pct` (close price N trading days after the cross, as a % change from the close at the
cross). Computed in Python post-fetch by `_compute_cross_events()` in `api.py`, not stored — the
series it operates on is already small (≤ `_STORE_DAYS`) and already fetched for the EMA chart, so
no new query or schema change was needed. A return is `null` (not a guess) if fewer than N trading
days have elapsed since the cross within the stored window — this also means "last 3 golden
crosses" can genuinely return fewer than 3 (or zero) for an infrequently-crossing stock, since
`ema_signals` only retains ~100 calendar days (`_RETENTION_DAYS`) — forward-return history is
bounded by the same retention window as everything else in this table, not a separate archive.
`frontend/app/sme-signals/page.tsx`'s expanded row renders this as "Last N golden/death crosses
(20d): +12%, −4%, +22%" above the EMA chart, using the same fetch the chart already makes.

**Aggregate golden hit-rate**: the single strongest trust-building number a raw technical
screener can show — "golden crosses in the last 90d: X% follow-through" — is computed as
part of `GET /api/sme-signals` in one SQL pass: a `LEAD(close_price, 20) OVER (PARTITION BY
symbol ORDER BY trade_date)` window function finds each golden cross's close price 20 trading
days later, aggregated across every stock at once (the same trading-day-offset approach
`_compute_cross_events` uses per-symbol, just as one query instead of N). Returned as
`golden_hit_rate_90d: {sample_size, win_rate, lookback_days, forward_days}` — `win_rate` is
`null` when `sample_size` is 0 (never guessed at); a cross too recent to have resolved yet
(`LEAD` returns `NULL`) is excluded from the sample rather than counted as a loss. Surfaced as
a 5th stat tile on `/sme-signals`.

**RSI(14) + volume spike**: standard momentum-screener confirmation signals alongside the EMA
cross — a cross with no volume confirmation behind it is a weak signal on its own. Both are
per-day columns on `ema_signals` (`rsi14`, `volume_spike`), computed once per pipeline run
from the same OHLCV fetch (see step 3 above), and filtered **client-side** in
`frontend/app/sme-signals/page.tsx` (RSI oversold ≤30 / overbought ≥70 chips, a
"Volume-confirmed only" toggle) — the API already returns every row for the selected
period/direction/view, so no new query params were needed for this, matching how the
existing Exchange filter already works.

**Regime view**: `GET /api/sme-signals?view=regime` (default `view=crosses`) drops the
`cross_type IS NOT NULL` filter and returns the latest stored row for **every** monitored
stock via `DISTINCT ON (s.symbol) ... ORDER BY s.symbol, e.trade_date DESC` — the "golden-now"
stat in the default view has no way to say which specific stocks make up that number without
this. `lookback`/`direction` are accepted but ignored in this view (no cross-event window to
filter by). Since most stocks' latest row isn't a cross day, `cross` is `null` for most rows
in this view — `SmeSignal.cross` and `CrossBadge` both accept `null` (rendered as "—") to
support this; in the default crosses view `cross` is never null (guaranteed by the SQL's own
`WHERE e.cross_type IS NOT NULL`). The frontend's Period/Direction filter chips are hidden in
regime view since they don't apply.

**BSE deep-link resolution**: an NSE row's `symbol` is already a directly analyzable ticker,
so it deep-links straight to `/?symbol=<symbol>`. A BSE SME row's `symbol` is BSE's own numeric
scrip code, which isn't — `/api/analyse/{symbol}` passes its input straight through to
yfinance/Screener.in/NSE-API calls with no resolution step, so it needs the same ISIN-based
resolution `/api/validate/{symbol}` already does for a user-typed ISIN (see "Symbol validation
flow" above). `GET /api/sme-signals` now selects `s.isin` in both views (`null` for NSE rows —
`tools/sme_tools.py`'s NSE fetch never populates it; present for BSE rows when BSE's own list
API reported one). The frontend deep-links a BSE row via `/?symbol=<isin>` when `isin` is set
(plain, unclickable text otherwise), and the home page's deep-link handler
(`frontend/app/page.tsx`) detects an ISIN-shaped `?symbol=` value and resolves it through
`GET /api/validate/{isin}` first — same resolution `ticker-search.tsx` already does for
user-typed ISINs — before starting the actual analysis SSE stream, showing a brief "Resolving
listing…" state and a dedicated error message if resolution fails (never silently retrying the
raw ISIN as if it were a ticker). This only applies to genuinely ISIN-shaped deep links — every
other existing deep link (NSE rows, market-picks, consolidated card) is already a resolved
ticker and skips this extra round trip entirely, unchanged.

Daily auto-run: `.github/workflows/sme-cron.yml` runs the pipeline on GitHub Actions at
13:00 UTC (18:30 IST) on weekdays — NSE closes 15:30 IST, so this leaves a ~3h buffer for
end-of-day data to settle. Requires a `DATABASE_URL` repository secret pointing at a
network-reachable Postgres instance (Settings > Secrets and variables > Actions); the
workflow fails fast with a clear message if it's missing rather than a raw Python
traceback. Trigger a one-off run manually via the Actions tab's "Run workflow" button
(`workflow_dispatch`). `sme_ema_pipeline.run()` returns `False` (and the CLI exits non-zero)
when the run was substantially unsuccessful — an empty stock list, or an OHLCV fetch error
rate above `_MAX_ACCEPTABLE_ERROR_RATE` (50%, almost always NSE/yfinance rate-limiting rather
than genuinely bad symbols) — so a bad run fails the GitHub Actions job instead of silently
"succeeding" with mostly-empty data, and GitHub's built-in run-failure notification fires.
For a local/self-hosted alternative, a crontab entry works too:

    30 18 * * 1-5 cd /path/to/stock-research/backend && ../.venv/bin/python sme_ema_pipeline.py >> output/sme_cron.log 2>&1

### Custom screener flow

Generalizes SME Signals' filter-chip pattern to the main NSE/BSE market (the gap the
Product-lens gap analysis called out: "no custom screener" for the primary large/mid-cap
flow this product is centered on) — a stored-metrics batch pipeline, `screener_pipeline.py`,
mirroring `sme_ema_pipeline.py`'s shape, served at `/screener` via `GET /api/screener`.

1. **Universe**: NIFTY 500 (NSE's own published index membership,
   `tools/nifty500_tools.py::get_nifty500_constituents()`, 24 h cache) rather than the full
   NSE equity master (`_nse_master.txt`, ~2000 symbols) — a daily per-stock yfinance `.info`
   scrape (this codebase's heaviest documented per-symbol call; see `sme_ema_pipeline.py`'s
   own note on why it deliberately avoids that call for "hundreds of SME stocks") is only
   reasonable at a bounded, curated scale, and NIFTY 500 already covers the vast majority of
   stocks anyone would realistically screen for. **Disclosed limitation**: the exact NSE
   archive URL and CSV column layout for the NIFTY 500 list was not verified against a live
   response in this sandbox (no outbound internet — same disclosure pattern as the other
   NSE/BSE scrapers in this codebase) — defensive parsing degrades to an empty list (never a
   partial/guessed universe) rather than raising.
2. **No new scraping/OHLCV logic** — `screener_pipeline.py` reuses the exact same
   already-cached fetch functions the rest of this codebase already has for each stock:
   `tools.nse_tools.get_stock_quote` (price, P/E, market cap, sector/industry — one yfinance
   `.info` call, same as the main analysis flow) and `signals.technical.technical_signal`
   (RSI14 + EMA20/EMA50 trend posture, off the already-6h-cached `price_history` series — see
   "Technical signal" above). Both results are upserted into a new stored-metrics table,
   `screener_stocks`, so `GET /api/screener` never needs a live fetch per request — the same
   "fetch once, filter/sort many" shape `sme_stocks`/`ema_signals` already established. The two
   fetches are isolated in their own try/except inside `_fetch_one()` — a `technical_signal`
   failure (a transient pandas/price-history hiccup) must not discard an otherwise-good quote
   (price/P/E/market cap/sector); `rsi14`/`ema_trend` simply stay `null` in that case, same as
   when `technical_signal` legitimately returns `UNKNOWN` for too little price history.
3. **Industry vs. sector**: `nse_industry` (from the NIFTY 500 list itself, a real
   NSE-published classification) is the primary filter-chip dimension, in preference to
   `sector` (yfinance's own field, kept on the table for reference) — `sector`'s GICS-vs-
   Indian-market taxonomy for NSE/BSE symbols is an explicitly disclosed unverified assumption
   elsewhere in this codebase (see "Sector-aware signal weights" above), so this screener
   doesn't lean on it as the primary, user-facing filter.
4. `GET /api/screener` (rate-limited 60/min) accepts `industry`, `ema_trend`
   (`all`/`bullish`/`bearish`), `pe_max`, `market_cap_min`, `rsi_min`/`rsi_max`, `sort`
   (whitelisted column set — interpolated into `ORDER BY` since column names can't be bind
   parameters, validated against the whitelist first, same "closed enum, not raw user text"
   safety as `/api/sme-signals`'s `direction`/`view`), `order`, and `limit`/`offset`. Every
   numeric filter is optional and AND-ed together; a `NULL` value for a stock (yfinance/
   Screener didn't have it) excludes that stock from that filter rather than guessing a value
   for it. The response's `industries` field is the real, currently-populated set of
   `nse_industry` values in the table — the frontend's filter chips are built from this, not a
   hardcoded/guessed list, the same "no static list, ask the data" instinct as
   `GET /api/market-picks/history`'s `available_dates`.
5. `POST /api/screener/refresh` runs the pipeline in the background — same
   lock-then-rate-limit pattern (409 takes priority over 429) as `/api/sme-signals/refresh`.
6. `frontend/app/screener/page.tsx` renders a filterable/sortable table (trend/RSI/industry
   filter chips, P/E and market-cap numeric inputs, click-to-sort column headers) — a "Screener"
   nav link was added alongside "SME Signals" across every page's nav bar. Each row carries a
   `WatchlistButton`, tying this mode into the same cross-mode watchlist as the other three.
7. **Daily auto-run**: `.github/workflows/screener-cron.yml` runs at 14:00 UTC (19:30 IST) on
   weekdays — after `sme-cron.yml` (13:00 UTC) so that pipeline's own writes have settled, after
   NSE's 15:30 IST close, and 30 minutes after `watchlist-alerts-cron.yml` (also 13:30 UTC) so
   the two independent jobs don't hit the same DB connection pool / Actions minute at once. Same
   `DATABASE_URL`-secret-required, fail-fast-with-a-clear-message pattern as `sme-cron.yml`.
   `screener_pipeline.py` also has a `--setup-db`/`--reset-db`/`--force` CLI, same shape as
   `sme_ema_pipeline.py` — except `--reset-db` here is scoped to dropping/recreating only the
   `screener_stocks` table (`screener_stocks.drop()`/`.create()`, not `metadata.drop_all()`),
   unlike `sme_ema_pipeline.py --reset-db`, which operates on the shared `MetaData()` and so
   drops every table in the app — see that command's own disclosed limitation above.

### EOD price store + corporate actions flow

The platform's six data slices and the SME/screener pipelines all fetch price data live
(yfinance, NSE API) at request/run time — there's no persistent, PostgreSQL-backed daily
price history anywhere in this codebase. `eod_prices_pipeline.py` + `corporate_actions_pipeline.py`
are the first step of a longer-term "PostgreSQL as source of truth, scrapers demoted to
ingestion jobs" direction: a standalone, NSE-bhavcopy-fed daily price store, plus split/bonus
price adjustment on top of it. Nothing else in this codebase reads from it yet — this is
ingestion-only, the same "ships standalone, no consumer wiring yet" scope call this repo
already makes elsewhere (e.g. `schema_drift.py`'s six-task-only scope, disclosed in its own
section above).

1. **Sources**: `tools/eod_sources.py` fetches NSE's full daily bhavcopy
   (`sec_bhavdata_full_DDMMYYYY.csv` from `nsearchives.nseindia.com` — OHLC, previous close,
   average price, volume, turnover, trades, delivery quantity/%), the `EQUITY_L.csv` equity
   master (company name/ISIN/listing date/face value), and AMFI's `NAVAll.txt` (daily NAV for
   ~40k schemes) + `api.mfapi.in`'s per-scheme history mirror (for backfilling a newly-tracked
   scheme's history). All follow the same never-raise, `{"status": "ok"|"missing"|"error", ...}`
   convention as every other `tools/*.py` module — a 404 on the bhavcopy URL means holiday/not-
   yet-published (`"missing"`, not an error); a 200 response whose body doesn't look like a real
   bhavcopy (HTML bot-block page, or a degenerate non-CSV body) is detected via content-sniffing
   (`_has_bhavcopy_header`/`_looks_like_html`) and retried once with a fresh session before being
   treated as a genuine error — never silently ingested as if it were real price data.
2. **Schema** (`db/models.py`): `securities` (symbol PK, isin, company_name, series,
   listing_date, face_value, `last_seen` — the last date this symbol appeared in a bhavcopy,
   detecting a delisting/suspension without a separate status field), `prices_daily` (symbol +
   trade_date composite PK; only `EQ`/`BE`/`BZ`/`SM`/`ST` series are stored, debt/rights series
   filtered out at parse time), `mf_nav_daily` (scheme_code + nav_date composite PK — only
   schemes actually held in a portfolio `assets` table are stored, since ~40k schemes is too
   many to store wholesale; this codebase doesn't have a portfolio system yet, so NAV ingestion
   is a documented no-op — see point 5 below), and `corporate_actions` (id PK, symbol, ex_date,
   `type` — split/bonus/dividend/rights/buyback/other, `purpose_raw` — NSE's verbatim PURPOSE
   string, `price_factor` — the multiplier applied to prices strictly before ex_date, NULL for
   non-adjusting types, `amount` — dividend Rs/share, NULL otherwise). Added via Alembic revision
   `684c8a31e7e0` (see "Schema migrations (Alembic)" above) — not a hand-edited `db/schema.sql`
   guard, since that hand-synced convention was already superseded by Alembic before this shipped.
3. **`prices_daily.adj_close`** is a stored, precomputed column (approach: one writer, dumb
   readers — not an on-the-fly join at every read site) — split/bonus-adjusted only; dividends,
   rights, and buybacks are recorded as data but never affect it (no total-return series). Seeded
   to `close` on insert; `corporate_actions_pipeline.py`'s recompute job is the only writer after
   that — `_upsert_prices`'s `ON CONFLICT` clause deliberately never touches `adj_close`, so
   re-ingesting a day (an archive replay, a `--date` repair) can't clobber an already-adjusted
   value back to raw.
4. **Corporate-action parsing never guesses a ratio**: `tools/corporate_actions.py::parse_purpose()`
   classifies NSE's free-text PURPOSE string via regex (bonus `A:B` → factor `B/(A+B)`; split
   `old_fv→new_fv` → factor `new_fv/old_fv`) — anything that doesn't cleanly match becomes
   `type="other"` with a NULL `price_factor`, never a wrong-but-plausible-looking number. A
   `type="other"` row whose text still contains a bonus/split keyword
   (`_missed_factor_suspects()`) gets its own distinct warning log
   (`ca_missed_factor_suspect`) rather than drowning in ordinary AGM/EGM noise, so a genuinely
   missed ratio parse is actually visible to whoever reads the cron log. NSE occasionally revises
   an action's purpose text, creating a near-duplicate row under the same
   `(symbol, ex_date, purpose_raw)` unique key with a different factor — `adjusting_actions()`
   groups by `(ex_date, type)` and, when a group has more than one distinct factor, applies only
   the highest-id (most recently ingested) row's factor, logging `ca_factor_conflict` — a symbol
   cannot legitimately have two splits or two bonuses on one ex-date, so this is always a
   revision, never two real actions to multiply together.
5. **MF NAV ingestion is a documented no-op today**: `_held_scheme_codes()` queries a portfolio
   `assets` table (`type='mf'`) this codebase doesn't have yet — the query fails, is caught, and
   degrades to an empty set (`eod_nav_skipped` logged), never raising. The moment a portfolio
   system adds that table, NAV ingestion activates automatically with no code change here — this
   mirrors the "never invent, degrade to the documented no-op" convention used everywhere else in
   this codebase for an optional dependency that doesn't exist yet (e.g. `DATABASE_URL`/
   `SMTP_HOST` unset elsewhere in this doc).
6. **Self-healing default run**: `eod_prices_pipeline.py`'s default mode (no `--date`/`--backfill`)
   ingests any of the last 5 weekdays missing a `prices_daily` row — not just today — so a cron
   run that fired before NSE published the file, or a transient failure, is caught by the very
   next run without manual intervention. `--date YYYY-MM-DD` (one specific day) and
   `--backfill YYYY-MM-DD` (every weekday from that date to today) exist for manual repair/backfill.
   The NAV step and the corporate-actions step (`run_ca_step`, called as `eod_prices_pipeline.run()`'s
   final step) are both isolated in their own try/except — a failure in either never affects the
   equity-ingestion exit code, the same "one bad optional step doesn't fail the whole run"
   convention `main._build_report()`'s own signal/verdict-snapshot writes already follow.
7. **`--setup-db`/`--reset-db`**: each pipeline owns only its own tables — `eod_prices_pipeline.py
   --setup-db`/`--reset-db` creates/resets `securities`+`prices_daily`+`mf_nav_daily`;
   `corporate_actions_pipeline.py --setup-db`/`--reset-db` creates/resets `corporate_actions`
   alone — same scoped-table convention `screener_pipeline.py --reset-db` already established
   (see "SME golden cross flow" above for the `sme_ema_pipeline.py --reset-db` mistake this
   convention exists to avoid repeating).
8. **Daily auto-run**: `.github/workflows/eod-prices-cron.yml` runs at 14:15 UTC (19:45 IST) on
   weekdays — the bhavcopy with delivery data is published around 19:00 IST, and this runs after
   `sme-cron.yml` (13:00 UTC) with no overlap. Same `DATABASE_URL`-secret-required, fail-fast
   pattern as every other cron workflow in this repo. `corporate_actions_pipeline.py` has no
   separate cron entry — `eod_prices_pipeline.run()` already calls its daily step
   (`run_ca_step`) as part of the same run; the module stays independently runnable via its own
   CLI (`--backfill`/`--recompute SYMBOL`/`--recompute-all`) for manual repair.
9. **Disclosed limitation**: like every other NSE/BSE/AMFI scraper in this codebase, the exact
   bhavcopy CSV column layout, `EQUITY_L.csv` column layout, AMFI `NAVAll.txt` line format, and
   NSE corporate-actions API response shape were not verified against a live response in this
   sandbox (no outbound internet — same disclosure pattern as every other scraper in this file).
   A real-world mismatch degrades to a logged error/skip for that day's ingestion, never a
   fabricated row.
10. **Out of scope for this pass**: BSE bhavcopy, real-time/delayed intraday quotes, rights/buyback
    price adjustment, total-return (dividend-adjusted) series, adjusted volume, any API endpoint
    or frontend surface reading from this store, and migrating `sme_ema_pipeline.py`'s own
    yfinance-sourced OHLCV to read from `prices_daily` instead — all deliberately deferred rather
    than silently assumed done, the same disclosed-scope-boundary convention this document uses
    throughout (e.g. the Market Picks pipeline's own "deliberately not decomposed" note above).

### Securities master + symbol resolver (`tools/securities_master.py`)

A broker's internal stock code (e.g. `BAJAJHFLEQ`, `ORICAREQ`) routinely doesn't match the
canonical NSE/BSE trading symbol (`BAJAJHFL`, `OCCL`) — verifying holdings imported from a
brokerage statement by hand means repeated manual NSE-master/Screener.in/BSE lookups.
`tools/securities_master.py` closes that gap by combining every stock registry this codebase
already has (or now has, via the EOD price store above) into one resolver. **Not yet wired into
anything** — this ships the module and its tests only; the intended consumer is a future
broker-statement/CSV import, tracked separately.

1. **Four registries, one merge.** `load_nse_main_board(engine)` queries the `securities` table
   (populated nightly by `eod_prices_pipeline.py` from NSE's `EQUITY_L.csv` — no separate fetch
   needed, this is a free read off data the EOD store above already maintains), filtered to rows
   with a real `company_name` (an unenriched row is a pre-`EQUITY_L.csv`-join miss, not useful for
   name-fuzzy matching). `fetch_bse_main_board(force=False)` is a new fetch — BSE's own
   `ListofScripData` API (the same endpoint `tools/sme_tools.py` already uses for BSE SME Groups
   M/MS), looped over the main-board groups (`A`, `B`, `T`, `Z`, `X`, `XT`, `P`, `MT`, `TS`),
   deduped by `SCRIP_CD` across groups, cached 24h under `output/_bse_main_master.json`
   (atomic tempfile+`os.replace` write, same convention as `cache.py`/`tools/sme_tools.py`), never
   raising — a failing group is skipped, not fatal to the others, and a total fetch failure falls
   back to a stale cache, then to `[]`. `tools/sme_tools.py::get_all_sme_stocks()` (NSE Emerge +
   BSE SME, existing, untouched) is the fourth. `get_full_securities_master(engine, force=False)`
   merges all four, deduped by ISIN with NSE main-board preferred on collision (a DB failure on the
   NSE side degrades to that source contributing nothing, not a raised exception — the other three
   still merge).
2. **`resolve_symbol(engine, code, company_name=None, isin=None, master=None)`** resolves a
   broker's code to `{"symbol", "exchange", "confidence": "isin"|"exact"|"fuzzy"|"unresolved",
   "candidate_name"}`, in that tier order: an ISIN match (highest confidence — broker ISINs are
   authoritative) beats an exact code match (tried both as-is and with one trailing suffix
   stripped — `EQ`/`SM`/`ST`/`BE`/`BZ`/`IV` — a broker's own series suffix on an otherwise-correct
   symbol), which beats a fuzzy company-name match (`rapidfuzz.process.extractOne`,
   `token_set_ratio`, `processor=rapidfuzz.utils.default_process` for case/punctuation-insensitive
   scoring, `score_cutoff=85`). Below that threshold — or with no `company_name` at all — the
   result is `"unresolved"`, never a guessed symbol; `candidate_name` still carries the best fuzzy
   hit if any, so a caller can log/display "closest guess: X" without treating it as verified. A
   caller resolving many codes in a loop should build `master` once via
   `get_full_securities_master(engine)` and pass it in — each self-load re-scans the full merged
   registry, which is wasteful per-row.
3. **Disclosed limitation**: like every other NSE/BSE scraper in this codebase, BSE's exact field
   names for the main-board `ListofScripData` response (`scrip_id` for the alpha ticker, falling
   back to the numeric `SCRIP_CD` when blank) were not verified against a live response in this
   sandbox — same disclosure pattern as the BSE SME fetch this module's BSE fetch mirrors. A
   real-world field-name mismatch degrades that group's rows to a numeric-only symbol (still
   usable, just less readable) rather than a fabricated one.

### Route module extraction (`routes/`)

`api.py` had grown to ~2900 lines with every endpoint defined inline — a maintainability
gap a deep engineering/CTO-lens review called out directly. Watchlist and Positions were
the first (and, as of this pass, only) two domains split out, chosen because they're the
most duplicated: 8 endpoints total, each repeating the exact same rate-limit → 503-if-no-
`DATABASE_URL` → `run_in_executor` → sanitize-error wrapper.

1. `routes/watchlist.py` and `routes/positions.py` are `APIRouter` modules, registered via
   `app.include_router(...)` in `api.py` (placed after the shared helpers they depend on —
   `_get_db_engine`, `_rate_limit`, `_bearer_token_from_request`, `LOGGER`, `log_event` —
   are already defined). Each still owns its own routes exactly as before the split — this
   is a file reorganization, not a behavior or URL change.
2. Both modules import `api` itself (`import api`), not `from api import X` — reaching
   shared state via dotted access (`api._get_db_engine()`) rather than a copied reference.
   This avoids a circular-import ordering problem (`api.py` imports these routers, so they
   can't import `api.py`'s names at their own top-level before those names exist yet) and
   preserves this app's existing `unittest.mock.patch("api._get_db_engine", ...)` test
   convention — a patch only takes effect on code that looks the name up through the module
   object at call time, not on a name a `from api import X` already copied at import time.
3. `routes/_shared.py::run_owned_db_call(request, rate_limit_name, max_calls, sync_fn,
   event_prefix)` is the extracted wrapper itself — the repeated rate-limit/DATABASE_URL-
   check/executor/sanitize-error shape both domains' 6 CRUD endpoints (of 8 total; the two
   list-shaped calendar/read-only paths that don't fit this exact shape stay inline) now
   call instead of re-implementing.
4. `routes/watchlist.py` owns the ownership-resolution primitives (`resolve_owner()`,
   `owner_column()`, `WatchlistOwner`, `_VALID_EXCHANGES` — renamed from `api.py`'s original
   `_resolve_watchlist_owner`/`_owner_column`) since positions.py imports and reuses them
   directly rather than duplicating — nothing about "which column owns this request's rows"
   is watchlist-specific, but watchlist was the first domain to need it.
5. `api.py` re-exports `_MAX_WATCHLIST_ITEMS_PER_CLIENT` and `_MAX_POSITIONS_PER_CLIENT`
   (`from routes.watchlist import _MAX_WATCHLIST_ITEMS_PER_CLIENT`, etc.) purely for backward
   compatibility — `tests/test_api.py` reads both as plain values (e.g.
   `count_result.scalar.return_value = api._MAX_POSITIONS_PER_CLIENT`), not just as patch
   targets, so moving the constants without a re-export would have silently broken that
   existing test code.
6. **Deliberately scoped to these two domains only** — splitting the remaining ~25 endpoints
   (SME signals, screener, market picks, auth, API keys, financials, etc.) into their own
   `routes/*.py` modules is future work, the same disclosed "first increment, not the full
   file" scope call this codebase already makes elsewhere (e.g. `tests_live/`'s own coverage
   note). `api.py` is smaller after this pass, not fully decomposed.

### Dashboard component extraction

`results-dashboard.tsx` had grown to 1566 lines with ~35 top-level functions (fetch hooks,
card components, and small formatting helpers all defined inline) — the same "one file keeps
absorbing every new feature" pattern the `routes/` split above fixed on the backend, flagged by
the same engineering-lens review. Every card added since this component was first written
(Peer Comparison, Financials, Concalls, Insider Activity, Street Consensus, Valuation Summary,
Verdict Timeline, Quarterly Trend...) became another inline function here rather than its own
file.

1. Extracted into standalone files under `frontend/components/`, one per card/domain, mirroring
   this repo's existing flat `components/` convention (no new subdirectory):
   `financial-statements-card.tsx` (`useFinancials`, `StatementTable`, `FinancialStatementsCard`,
   `ConcallsCard`), `peer-comparison-card.tsx` (`usePeerComparison`, `PercentileBadge`,
   `ValuationAnchorBadge`, `PeerTable`, `SimilarStocksRail`), `insider-activity-card.tsx`,
   `street-consensus-card.tsx`, `verdict-timeline.tsx`, `valuation-summary-strip.tsx`,
   `quarterly-trend-card.tsx`, and `price-sparkline.tsx` (the hero's price-history-fetching
   wrapper — distinct from the pre-existing `sparkline.tsx`, the raw chart primitive it renders).
2. `dashboard-format.ts` (plain `.ts`, no JSX) holds only the formatting helpers actually shared
   across more than one card (`fmt`, `fmtCr`, `fmtVolume`, `fmtInr`, `normalizeRatioKey`,
   `formatAge`, `humanizeMetaKey`, `formatMetaValue`, `fmtRatio`) — a helper used by exactly one
   card (e.g. `fmtActivityDate`, `fmtConsensusDate`) stayed co-located with that card instead.
   `dashboard-primitives.tsx` holds the small generic UI atoms reused across several cards
   (`Card`, `MetricRow`, `ExchangeTable`, `RangeBar`).
3. Each fetch hook (`usePeerComparison`, `useFinancials`, `useInsiderActivity`,
   `useStreetConsensus`, `useVerdictHistory`) stayed co-located with the one card that calls it,
   matching how they already read in the original file (defined immediately above their single
   consumer) rather than moving into a separate hooks directory.
4. `results-dashboard.tsx` itself is now 613 lines — the main `ResultsDashboard` component plus
   the handful of helpers genuinely specific to its own JSX (`formatScalar`/`formatFactor` for
   the bull/bear factor lists, `formatNewsHighlights`, `summaryBullets`, and the
   `REC_CONFIG`/`CONF_COLOR`/`SENT_COLOR` tone tables) that no other card needs.
5. Pure reorganization — same props, same JSX, same behavior; verified with both `npx tsc
   --noEmit` and `npm run build` (this repo's documented verification bar for anything touching
   `globals.css`-adjacent styling or requiring the production minifier to catch what `tsc` alone
   won't).
6. Every other reference to a specific card by name elsewhere in this document (e.g.
   "`results-dashboard.tsx`'s `InsiderActivityCard`") still describes the same component and
   behavior — it just now lives in its own file rather than inline in `results-dashboard.tsx`.

### Schema migrations (Alembic)

11 tables across `db/models.py`, kept in sync with `db/schema.sql`'s hand-written
`CREATE TABLE IF NOT EXISTS`/`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` guards purely by
convention (`tests/test_schema_sql_migrations.py` only checks 2 of the 11 tables for a missing
guard) — a CTO/engineering-lens review flagged this directly: two hand-synced schema sources
and no real migration tool is exactly the setup that produced `sme_ema_pipeline.py --reset-db`'s
own documented mistake (dropping every table via the shared `MetaData()` object, not just its
own, because nothing enforced a narrower blast radius).

1. `alembic.ini` + `migrations/` live in `backend/`, and every alembic command runs from there —
   `migrations/env.py`
   imports `db.models.metadata` directly as `target_metadata` (the same SQLAlchemy Core
   `MetaData()` object every table already declares against), so `alembic revision
   --autogenerate` diffs a live database against the exact same table definitions this app's
   code already uses — no second, Alembic-specific model layer to keep in sync. `env.py` reads
   `DATABASE_URL` from the environment (same env var every other DB-backed module in this app
   already reads, via `db/models.py::get_engine`) rather than `alembic.ini`'s own
   `sqlalchemy.url`, which is deliberately left blank — one place to configure a connection
   string, not two.
2. `migrations/versions/0001_baseline_schema.py` was the first migration — autogenerated against
   a genuinely empty database and verified (against a real local Postgres instance) to both
   `alembic upgrade head` cleanly onto nothing and `alembic downgrade base` cleanly back to
   nothing, producing exactly the same 11 tables, indexes, and constraints
   `db/schema.sql`/`metadata.create_all()` already produce. **There are 4 revisions today** —
   `0001_baseline_schema`, then `684c8a31e7e0_add_eod_price_store_and_corporate_` (the
   `securities`/`prices_daily`/`mf_nav_daily`/`corporate_actions` tables) and
   `8613aafc2d9d_add_portfolio_aggregator_foundation_` (`profiles`/`accounts`/`assets`/
   `holdings`/`valuations`/`transactions`), bringing the schema to 21 tables; a fourth, `a7f2c1d09b34`, adds `app_state` for 22. The first two
   later revisions were autogenerated and round-trip-verified (upgrade → `alembic check` clean →
   downgrade → upgrade) against an isolated scratch Postgres, the same way `0001` was.
3. **A deployment predating Alembic must `alembic stamp 0001` and THEN
   `alembic upgrade head` — not a bare `alembic stamp head`.** Such a deployment already has the
   original 11 tables (created by hand via `db/schema.sql`, or by a pipeline's `--setup-db`
   calling `metadata.create_all()`), so replaying `0001`'s `CREATE TABLE` statements against it
   would fail on the very first one — hence the stamp. But stamping straight to `head` would
   also mark the two *later* revisions as applied when they aren't, silently skipping creation
   of the 10 tables they add (the EOD price store and Portfolio Aggregator), leaving those
   features broken at runtime with no error at migration time. Stamp the baseline specifically,
   then upgrade forward:

   ```bash
   cd backend
   alembic stamp 0001      # "the original 11 tables already exist"
   alembic upgrade head    # actually creates the 10 newer tables
   ```

   The revision id is `0001`, **not** the filename stem `0001_baseline_schema` — Alembic resolves
   by the `revision` string inside the file, and `alembic stamp 0001_baseline_schema` fails with
   `Can't locate revision identified by '0001_baseline_schema'`.

   A genuinely fresh database needs only `alembic upgrade head`. (When only `0001` existed, a
   bare `stamp head` was correct and was verified that way — creating the schema via
   `metadata.create_all()`, stamping, then confirming `alembic check` reported no drift. That
   instruction became wrong the moment a second revision landed.)
4. **From here on, schema changes should be authored as new Alembic revisions** —
   `alembic revision --autogenerate -m "..."` after editing `db/models.py`, then `alembic
   upgrade head` to apply. This is the replacement for the old workflow (hand-edit
   `db/models.py`, hand-edit a matching `ALTER TABLE ADD COLUMN IF NOT EXISTS` into
   `db/schema.sql`, hope `test_schema_sql_migrations.py` catches a missed guard on the 2 tables
   it covers) — a generated revision has an explicit up AND down path, a real ordering (each
   revision's `down_revision` chains to the last), and autogenerate diffs against the *actual*
   live schema rather than trusting a hand-written guard was remembered.
5. **`db/schema.sql` is kept, not deleted** — frozen as a reference for what the schema looked
   like before Alembic, and because `tests/test_schema_sql_migrations.py` still exercises its
   existing guard convention for the two tables it already covers. It is not expected to gain
   any *new* `ALTER TABLE` guards going forward; new columns get a new Alembic revision instead.
   Whether to eventually retire `schema.sql`/its test entirely once every current deployment has
   migrated onto Alembic is a future decision, not made here.
6. **Disclosed scope**: this establishes the tool and a verified, working baseline — it does not
   itself add any new schema change (no new column, no new table). The two hand-synced-schema
   and no-rollback-path gaps the review flagged are what's fixed; retroactively backfilling
   individual historical Alembic revisions for every column `db/schema.sql` already added over
   this app's history (e.g. `users.tier`, `watchlist_items.user_id`) was not attempted — `0001`
   captured the schema as it stood when Alembic was adopted, in one shot, which was sufficient
   for both a fresh install and an existing deployment's stamp path. Schema changes since then
   are their own revisions, as point 4 below describes.

### Watchlist flow

The `watchlist_items` table (PostgreSQL, `DATABASE_URL`) is the one piece of shared state
connecting the three otherwise-independent modes. Each row is owned by exactly one
identity — the anonymous per-browser `client_id`, or, once signed in, the account's
`user_id` — enforced by `ck_watchlist_exactly_one_owner` (`CHECK ((client_id IS NULL) <>
(user_id IS NULL))`) plus two separate `UNIQUE` constraints (`(client_id, symbol)` and
`(user_id, symbol)` — a single combined constraint wouldn't work, since Postgres treats
every row's `NULL` as distinct from every other `NULL`, so it wouldn't actually cap either
identity to one row per symbol).

1. `GET /api/watchlist?client_id=`, `POST /api/watchlist` (`{client_id, symbol, company, exchange}`,
   `client_id` optional), `DELETE /api/watchlist/{symbol}?client_id=` — all in
   `routes/watchlist.py` (see "Route module extraction" above), using the same cached engine
   (`_get_db_engine()`) as the SME endpoints
2. **Identity resolution** (`routes.watchlist.resolve_owner()`): a valid session (the
   `Authorization: Bearer <token>` header — see "Account & magic-link auth flow" below) always
   wins over `client_id` when both are present in a request, since the whole point of an
   account is that it doesn't depend on which browser sent the request. An expired/invalid
   token isn't a 401 here — this endpoint doesn't require being signed in, so it just falls
   through to the `client_id` path, same as no token at all. A request with neither a valid
   session nor a well-formed `client_id` gets 422.
3. **No *automatic* migration on sign-in**: an anonymous `client_id`'s existing rows are never
   silently claimed/merged onto an account when a user signs in — a freshly-signed-in user
   simply starts seeing whatever rows their account already owns (possibly none), and their old
   anonymous rows remain reachable only by that same browser's `client_id` while logged out.
   This mirrors the same deliberate scope call `db/models.py`'s `users` table comment
   documents for the auth system as a whole. An explicit, user-initiated opt-in escape hatch
   exists for this — see point 10 below — but nothing ever triggers it automatically.
4. `client_id` is a UUID generated client-side (`crypto.randomUUID()`) and persisted in
   `localStorage` — it groups one browser's anonymous rows, nothing more
5. `frontend/lib/watchlist.ts`'s `useWatchlist()` hook holds a module-level shared cache +
   subscriber list so every mounted `WatchlistButton` (stock analysis, Market Picks rows,
   SME Signals rows) reads/writes the same in-memory state without each firing its own
   fetch or needing React Context. It always sends `client_id` regardless of auth state — the
   backend transparently decides which identity actually owns the request, so the hook itself
   doesn't need to know. `refreshWatchlist()` clears that cache and re-fetches; it's called
   from `/auth/verify`'s success path and from `useAuth()`'s `logout()`, since neither a
   sign-in nor a sign-out otherwise gives the watchlist's independent module-level cache any
   signal that the caller's identity just changed.
6. The Next.js proxy routes (`app/api/watchlist/route.ts`, `app/api/watchlist/[symbol]/route.ts`)
   forward the session cookie as `Authorization: Bearer <token>` alongside the existing
   `client_id` passthrough — same pattern as the `/api/auth/*` proxy routes — so `api.py`
   never sees a cookie, only that header.
7. `/watchlist` fans out to `GET /api/prices` for live quotes on whatever's starred
8. Same defensive conventions as SME endpoints: 503 if `DATABASE_URL` unset/DB unreachable
   (sanitized — no raw exception text in the response), 422 on invalid `client_id`/`symbol`/
   missing identity, rate-limited via `_rate_limit()`, capped at 200 items per identity
   (`_MAX_WATCHLIST_ITEMS_PER_CLIENT`, same cap for both client_id- and user_id-owned rows)
9. **Corporate-action calendar** (`GET /api/watchlist/calendar?symbols=...`) — a "what's coming
   up across everything I'm watching" roll-up, closing a gap a watchlist otherwise exists to
   solve. Deliberately **not** a `watchlist_items` query at all — the frontend already has its
   own symbol list from `GET /api/watchlist`, so this endpoint just takes that list directly and
   runs `signals/filings_classifier.py::classify_filings()` (the same classifier
   `main._build_report()` already runs per-symbol for the single-stock report's own "Corporate
   Filings" card) over each symbol's already-cached `filings`. No new scrape, no `DATABASE_URL`
   dependency. A symbol with no cached filings (or nothing classifiable in them) contributes
   nothing — best-effort over whatever's already in cache, not an error. `frontend/app/
   watchlist/page.tsx`'s `CalendarStrip` renders one row per symbol with something to show
   (next-results date, a rating action, up to 2 corporate actions), sorted next-results-date
   first.
10. **Opt-in "claim my data" flow** (`POST /api/watchlist/claim` + `POST /api/positions/claim`,
    `routes/_shared.py::claim_anonymous_rows_sync()`) — a product-lens review flagged that the
    no-migration default in point 3 above was "actively suppressing conversion": a visitor who
    built up a watchlist anonymously, then signs in, previously just saw an empty account with
    no path back to their anonymous rows short of staying logged out. This closes that gap
    without reversing the underlying safety-conscious default — migration still never happens
    automatically, only when a signed-in user explicitly asks for it.
    - Both endpoints require a valid session (`Authorization: Bearer <token>`) — a missing or
      expired one is a real `401`, not a silent fall-through to `client_id`, since each
      endpoint's only caller (below) already knows a session exists by the time it fires.
    - `claim_anonymous_rows_sync(engine, table, order_column, client_id, user_id,
      max_per_owner, lock_prefix)` is shared by both tables (`watchlist_items`/`added_at`,
      `positions`/`bought_at`) since they share the exact same ownership shape. A symbol the
      account already owns keeps the account's row; the anonymous duplicate is discarded
      (it could never be claimed anyway — `uq_{table}_user_symbol` forbids two rows for the
      same `(user_id, symbol)`). Rows are claimed oldest-first up to the account's remaining
      room under the existing per-owner cap (`_MAX_WATCHLIST_ITEMS_PER_CLIENT`/
      `_MAX_POSITIONS_PER_CLIENT`) — anything beyond that is left owned by `client_id` rather
      than silently exceeding the cap, and reported back as `skipped_over_cap` rather than
      dropped. Guarded by the exact same per-account advisory-lock **key** the add endpoint
      already takes (`pg_advisory_xact_lock(hashtext("watchlist:user:<id>"))`, not a separate
      `"watchlist_claim:<id>"` namespace) — an own-adversarial-review pass caught that an
      earlier version of this function used a distinct lock-key prefix that *looked* like a
      deliberate "own namespace per operation" choice but actually meant a concurrent claim and
      add for the same account took two different locks and never serialized against each
      other at all, letting both read the same pre-write row count and both commit, silently
      exceeding `max_per_owner`. Fixed by matching the lock key exactly; re-verified end-to-end
      against a real local Postgres instance with two real concurrent transactions racing (a
      199-row account, cap 200, claiming 1 row while an add fires at the same instant) —
      confirmed the account lands at exactly 200, never over, matching the invariant the docs
      already claimed before the fix actually delivered it.
    - **Disclosed residual risk**: `client_id` was never a secret — any request that knows it
      can already read/add/delete that browser's rows (see "Identity resolution" in "Watchlist
      flow" below), the same "grouping key, not a security boundary" design this table has
      always had. Claiming is more severe than an ordinary read/write, though: it *exclusively*
      reassigns those rows, permanently cutting off the original anonymous browser's access — a
      signed-in attacker who obtains someone else's `client_id` (already visible in plaintext
      query strings on every other endpoint here, so it can leak via a shared screenshot,
      browser history, or a server access log) could claim their watchlist for themselves. Both
      claim endpoints are rate-limited far tighter than this table's ordinary writes (5/hour,
      not the 60/minute every other write here gets — same per-address-not-just-per-IP
      precedent as the magic-link request-link endpoint's 5/hour) and log a `watchlist_claimed`/
      `positions_claimed` audit event on every success, which meaningfully bounds
      automated/brute-force abuse but does not eliminate a single targeted guess of one
      specific leaked ID — that would need proof of possession (e.g. a signed token minted into
      the anonymous browser itself), a larger change not attempted in this pass.
      Verified end-to-end against a real local Postgres instance in this sandbox (both the
      plain-claim and over-cap-partial-claim paths), not just the mocked unit tests
      `tests/test_api.py` also carries.
    - **The one caller**: `frontend/app/auth/verify/page.tsx`, right before it calls
      `GET /api/auth/verify` (i.e. while the browser still has no session cookie, so
      `GET /api/watchlist`/`GET /api/positions` still resolve via `client_id`, not an
      account), fetches both endpoints' current item counts for this browser's `client_id`.
      If either is non-zero, once sign-in itself succeeds the page shows a "You have N
      watchlist items / M positions from browsing anonymously — claim them?" prompt with
      explicit **Claim** / **Skip** buttons, instead of immediately redirecting home. Skipping
      leaves the anonymous rows exactly where point 3 above already says they'd stay — reachable
      by that same browser's `client_id` while logged out, nothing lost. `frontend/lib/
      watchlist.ts::claimWatchlist()` / `frontend/lib/positions.ts::claimPositions()` are thin
      wrappers that also refresh their module's own shared cache on success, same pattern as
      `refreshWatchlist()`/`refreshPositions()`.

### Watchlist alert emails

A standalone daily batch job, `watchlist_alerts.py` (repo root) — same standalone-script shape
as `sme_ema_pipeline.py` (PostgreSQL, a `run()`/`main()` split, `--force` CLI flag, a
`_MAX_ACCEPTABLE_ERROR_RATE`-style health gate so a bad run fails its GitHub Actions job loudly
instead of "succeeding" silently) — but wired to the existing single-stock analysis pipeline
(`main._fetch_task` + `signals.engine` + `crew.run_analysis_with_fallback`) instead of the SME
OHLCV fetch. Only **account-owned** (`user_id`) watchlist rows are ever considered — an
anonymous `client_id` row has no email to notify and is excluded at the query level.

1. `_get_watched_symbols()` runs one query joining `watchlist_items` to `users` (`WHERE
   user_id IS NOT NULL`) and groups the rows by symbol, since several users can watch the same
   stock and each should only trigger one re-analysis of it, not one per watcher.
2. `_analyze_symbol(symbol, run_id, force=False)` re-runs the same fetch → signal-engine →
   analyst flow `main.py`'s CLI path runs for one symbol — respecting the existing per-task
   cache TTLs (so a symbol some other visitor already refreshed today via the website isn't
   double-fetched or double-billed) — and calls `verdict_history.save_snapshot()` on every path,
   including the "everything was already fresh" cache-hit path, mirroring `main.py`'s own
   early-return branch so a day is never silently missing a snapshot just because nobody
   re-triggered the LLM that day. Any exception is caught and logged per-symbol (returns `None`)
   so one bad fetch can't sink the whole run, the same isolation convention
   `_consolidated_payload()` and `get_insider_activity()` use for their independent sub-fetches.
3. `_detect_change(symbol)` compares `verdict_history.load_history(symbol, limit=2)`'s two most
   recent rows — today's just-saved snapshot against the one immediately before it — and returns
   `{"kind": "recommendation_change", "symbol", "old_recommendation", "new_recommendation",
   "confidence"}` only when the recommendation actually differs and both rows exist (a symbol
   analysed for the first time today, like `VerdictTimeline`'s own 2-day minimum, has nothing to
   compare against yet). `_detect_price_move(symbol)` runs the same two-row comparison
   independently and returns `{"kind": "price_move", "symbol", "old_price", "new_price",
   "change_pct"}` when the live price moved at least `_PRICE_MOVE_THRESHOLD_PCT` (10%) since the
   prior snapshot — a stock can move double digits in a day and still close as a HOLD, which the
   recommendation-change check alone would never catch. This is a deliberately scoped-down,
   email-digest version of "real-time price alerts" — a genuine push channel (device tokens,
   APNs/FCM/web-push infra, a subscription UI) is new product infrastructure this repo doesn't
   have anywhere yet, so this widens the *existing* once-daily digest's trigger set instead of
   adding a new delivery mechanism. Both detectors run per symbol; a symbol can contribute both
   alert kinds to the same day's digest.
4. This job runs the full paid LLM analyst call per distinct watched symbol, so an unbounded
   watchlist fan-in would mean an unbounded daily bill — the same cost-control instinct as
   `market_picks_pipeline.py`'s `_MAX_STOCKS`. `_MAX_ALERT_SYMBOLS` (50) caps how many distinct
   symbols one run analyses; symbols beyond the cap are skipped for that day (logged, not
   silently dropped — no-silent-caps convention) rather than letting the bound grow unbounded.
5. `email_sender.py` gained a second message builder/sender pair —
   `send_watchlist_alert_email(to_email, alerts)` — alongside the existing magic-link one; both
   now share one `_send_via_smtp()` helper (extracted, not duplicated) for the connect/STARTTLS/
   login/send sequence. One digest email per user per run lists every alert (recommendation
   changes and/or price moves) for that user, not one email per symbol, so a user watching
   several stocks that all moved the same day gets a single message; `_format_alert_line()`
   branches on each alert's `kind` to render the right line shape. Same best-effort convention
   as `send_magic_link_email`: returns `True`/`False`, never raises, and a missing `SMTP_HOST`
   just means the email never arrives.
6. **Daily auto-run**: `.github/workflows/watchlist-alerts-cron.yml` runs at 13:30 UTC (19:00
   IST) on weekdays — after `sme-cron.yml` (13:00 UTC) so that pipeline's own writes have
   settled, and well after NSE's 15:30 IST close. Requires the same `DATABASE_URL` secret as
   `sme-cron.yml`, plus whichever LLM provider key and `SMTP_*` secrets the deployment already
   uses for the live site (the batch job is unattended, so it can't fall back to "no key
   configured" the way the interactive CLI does — `run()` returns `False` immediately if neither
   is set, failing the job loudly). `python watchlist_alerts.py --force` is available for a
   manual re-run that bypasses cache freshness entirely.

### Account & magic-link auth flow

Minimal, passwordless auth — no OAuth, no separate signup step. Additive on top of the
anonymous `client_id` identity above: `watchlist_items` rows an anonymous browser already
had stay exactly as they were and keyed by `client_id` — signing in doesn't claim or merge
them onto the account (a deliberate scope call — see the Tier 2 product-queue discussion
this shipped from, and "Watchlist flow" above for how a signed-in request's identity is
resolved). "I bought this" positions tracking has no backend at all yet (see the Market
Picks pipeline docs), so there's nothing for an account to link there.

1. **Request a link** — `POST /api/auth/request-link` (`{email}`), rate-limited both per-IP
   (5/15 min) and per-target-address (5/hour) — the address-keyed limit exists because an
   attacker with rotating IPs would otherwise get a fresh 5/15min budget per IP and could
   email-bomb one victim's inbox indefinitely. `auth.create_magic_link(email)` opportunistically
   prunes expired `magic_links`/`sessions` rows (same "delete stale entries on the next write"
   convention as `_prune_extract_cache()` — these tables only grow from auth traffic, so a
   request-link call is a natural trigger) before storing a single-use token (only its SHA-256
   hash is persisted — the raw token exists only in the outbound email and the process memory
   that generated it) with a 15-minute expiry, then `email_sender.send_magic_link_email()` emails
   a link pointing at `{FRONTEND_URL}/auth/verify?token=...`. The response is always
   `{"sent": true}` regardless of whether SMTP delivery actually succeeded (logged server-side
   as a warning) — this avoids leaking SMTP configuration state, and a link that failed to send
   once still works if the caller re-requests after SMTP is fixed.
2. **Verify** — the browser opens `/auth/verify?token=...` (a Next.js page, not the FastAPI
   endpoint directly — the cookie has to be set on the frontend's own origin), which shows a
   "Complete sign-in" button rather than firing the verify call automatically on page load —
   corporate email "safe link" pre-fetchers (Outlook Safe Links, Proofpoint, etc.) crawl links
   in emails before a human opens them, and an auto-firing `GET` would let the scanner consume
   the single-use token first and lock the real user out. Clicking the button calls
   `GET /api/auth/verify?token=`. `auth.verify_magic_link()` atomically consumes the token
   (`UPDATE ... WHERE used_at IS NULL AND expires_at > NOW() ... RETURNING`, so two
   concurrent clicks of the same link can't both win) and get-or-creates the `users` row for
   its email — there's no separate signup; the first successful link click *is* account
   creation. `auth.create_session()` then issues a session token (30-day expiry, same
   hash-only-storage convention as magic links) tied to that user. The response body never
   echoes the raw session token back to the caller past the one proxy hop that sets the
   cookie (see step 3) — only `{user}` reaches page-level JS, so an XSS on this origin can't
   read a live session token out of a fetch response.
3. **Cookie handoff** — `frontend/app/api/auth/verify/route.ts` is the one proxy route that
   isn't a pure passthrough: on a successful backend response it also sets the raw session
   token as an httpOnly, `SameSite=Lax` cookie (`alphapulse_session`) on the Next.js origin,
   since the browser only ever talks to that origin, never to FastAPI directly. Every other
   authenticated proxy route (`/api/auth/me`, `/api/auth/logout`, and any future
   account-gated endpoint) reads that cookie server-side (`lib/auth-cookie.ts`) and forwards
   it to the backend as `Authorization: Bearer <token>` — `api.py` never sees a cookie, only
   that header.
4. **Session state in the UI** — `frontend/lib/auth.ts`'s `useAuth()` hook holds a
   module-level shared cache + subscriber list (same pattern as `useWatchlist()`): every
   mounted `AuthWidget` (dropped into every page's nav bar next to `HeaderSearch`) reads/
   subscribes to one in-memory fetch of `GET /api/auth/me` instead of each firing its own.
   Shows a "Sign in" link when logged out, or the user's email with a sign-out dropdown when
   logged in. `refreshAuth()` re-fetches after `/auth/verify` succeeds so the nav updates
   without a full page reload.
5. **Logout** — `POST /api/auth/logout` best-effort deletes the session row
   (`auth.delete_session()`, swallow-and-log like `verdict_history.py`'s read path); the
   Next.js route clears the cookie regardless of whether that delete succeeded, so the
   browser is signed out either way.
6. `GET /api/auth/me` is the one endpoint every authenticated page implicitly depends on —
   401 (not 200 with a null user) when there's no session, so `useAuth()`'s `loading` state
   distinguishes "still checking" from "confirmed signed out."

### Programmatic API access flow

A signed-in user can mint long-lived API keys for scripts/integrations, separate from the
session-cookie identity the frontend itself uses. Three independent pieces:

1. **Key management** (session-authenticated, same identity as everything else under "Account
   & magic-link auth flow" above) — `POST /api/api-keys` (`{label?}`, 201, returns the row
   *including the raw key* — the only response that ever does, since `auth.create_api_key()`
   never persists it, only its SHA-256 hash, the same convention as `magic_links`/`sessions`),
   `GET /api/api-keys` (list metadata only — `key_prefix`, not the key or its hash), `DELETE
   /api/api-keys/{id}` (revoke; 404 if the id doesn't exist or isn't owned by the caller — never
   a 403, so the endpoint doesn't confirm/deny another user's key IDs exist). A key has no fixed
   TTL, unlike a session — it's valid until explicitly revoked, since a script can't "re-sign-in"
   through a magic link the way a browser redirects through one. `frontend/app/api-keys/page.tsx`
   (in the primary nav — see point 4 below) is the management UI: the create form shows the raw
   key exactly once, in a copy-to-clipboard box, with an explicit "won't be shown again" warning;
   the list table shows every key including revoked ones (badged), never re-displaying the secret.
2. **The gated surface itself** — `GET /api/v1/consolidated/{symbol}`, deliberately the *only*
   `/api/v1/*` route today: a thin auth/rate-limit wrapper around the exact same
   `_consolidated_payload()` helper `GET /api/consolidated/{symbol}` already calls (extracted out
   of that handler specifically so the two paths can't drift), so "what does AlphaPulse think
   about X" is available to external callers with zero duplicated aggregation logic. Auth here is
   a raw key in the `X-API-Key` header — deliberately **not** `Authorization: Bearer`, which is
   reserved for the internal session-token convention above; reusing that header would let a
   forwarded session token accidentally satisfy this check. `_require_api_key_user()` validates
   the key via `auth.get_user_for_api_key()` (which also opportunistically stamps
   `last_used_at`) and applies a per-*user*, **tier-scaled** rate limit (`api_v1:{user_id}`, a
   sliding one-hour window) rather than per-IP like the internal endpoints — a legitimate
   integration may run from a shared or rotating IP, so IP-keying would be the wrong bucket here.
   More `/api/v1/*` routes can follow the same wrapper-around-an-existing-handler pattern later;
   this PR intentionally ships one real endpoint rather than a speculative surface no caller has
   asked for yet.
3. **Tiers + usage dashboard** — `users.tier` (`'free'` | `'pro'`, `db/models.py`) gates
   `api._TIER_LIMITS` (`{"free": 100, "pro": 1000}` calls/hour). **No real payment processing
   exists** — there is no signup/checkout flow that ever sets a row to `'pro'`; every account is
   `'free'` until an operator updates the column by hand (`server_default 'free'` on the column
   itself, so this is a safe no-op for every pre-existing row, not a breaking migration). An
   unrecognized tier value falls back to `'free'` (`_DEFAULT_TIER`) rather than trusting a stored
   value blindly. `rate_limiter.get_usage_count(key, window_seconds)` is a **non-mutating peek**
   at the same sliding-window state `is_allowed()` already maintains (`ZCARD` after
   `ZREMRANGEBYSCORE`, or the in-memory equivalent) — checking current usage never itself counts
   as a call against the limit it's reporting on, unlike `is_allowed()`. `GET /api/api-keys`
   (already session-authenticated for key management) now also returns `tier` and
   `usage: {calls, limit, window_seconds}` in the same response — a usage dashboard alongside key
   management, not a separate endpoint, since a user managing their keys is exactly who wants to
   see this. `frontend/app/api-keys/page.tsx` renders it as a tier badge + a progress bar (red
   past the limit) above the key-creation form.
4. **Discoverability + pricing** — a product-lens review flagged two gaps directly: "a
   signed-out visitor has no navigational path to discover the API exists at all" (it was only
   ever reachable through the signed-in account dropdown), and "no pricing page, no upgrade
   button, no checkout anywhere." Both closed without fabricating a payment flow that doesn't
   exist:
   - `components/site-nav.tsx`'s `LINKS` gained an `api-keys` entry, visible in the primary nav
     to every visitor regardless of sign-in state — the page itself already has its own
     signed-out prompt, so there's no dead end. `components/auth-widget.tsx`'s account dropdown
     dropped its now-redundant "API keys" menu item since the primary nav covers it.
   - `frontend/app/pricing/page.tsx` is a genuinely informational page, not a marketing page for
     a checkout that doesn't exist: it lists the Free (100 calls/hour) and Pro (1,000 calls/hour)
     tiers and what each unlocks, then states plainly — matching this point's own "no real
     payment processing exists" disclosure above — that there's no self-serve checkout and Pro
     access is granted by whoever operates this deployment. `frontend/app/api-keys/page.tsx`'s
     usage card links to it for free-tier accounts.

### Consolidated view flow

`GET /api/consolidated/{symbol}` answers "what does AlphaPulse think about X" without
visiting three pages. It is pure read-aggregation — no LLM calls, no scraping, no SME
pipeline run:

1. **Analysis** — `cache.load(symbol, "analysis")`, the same 24 h cache the stock analysis
   flow writes to. `None` if never analyzed for this symbol, or the cache has gone stale.
2. **Market pick** — `_load_picks_cache()` (the same 6 h `output/_market_picks/picks.json`
   cache market picks serves from), matched by symbol. `None` if the symbol isn't on the
   current picks list, or the cache itself is stale/missing.
3. **SME regime** — one indexed query against `ema_signals`/`sme_stocks` for the latest
   stored row, via the same cached engine (`_get_db_engine()`) as the SME and watchlist
   endpoints. `None` if `DATABASE_URL` is unset, the symbol isn't an SME/Emerge stock, or
   the query fails — a DB hiccup on this section must not fail the other two, so it's
   caught and logged rather than raising.

The three lookups run concurrently via `asyncio.gather` over `run_in_executor`. The
frontend's `HeaderSearch` component (embedded in every page's nav bar) opens
`ConsolidatedCard` on submit, which fetches this endpoint and renders each section
independently — a `null` section shows "not yet analyzed" / "not on the picks list" /
"no SME data" rather than an error, since that's the expected common case.

### Compare flow (`/compare?symbols=TCS,INFY`)

Two full stock analysis reports side by side. No new backend — each column runs the exact
same `GET /api/analyse/{symbol}` SSE pipeline the home page uses, via a shared
`useStockAnalysis()` hook (`frontend/lib/useStockAnalysis.ts`, extracted from the home page
so both call sites stay in sync) — one independent `EventSource` per symbol, so the two
columns fetch/progress/error independently of each other.

Capped at 2 symbols: `ResultsDashboard`'s internal grid breakpoints (`lg:`, `md:`, `sm:`)
are viewport-relative, not container-relative (no container-query plugin installed), so a
column narrower than the component's own breakpoint would render its internal two-block
layout compressed rather than actually reflowing. `/compare`'s own column layout only
switches from stacked to side-by-side at `2xl:` (1536px) specifically so that by the time
two columns sit side by side, each is wide enough for `ResultsDashboard`'s own layout to
still look right — below that, the two reports stack full-width instead of squeezing.

**Head-to-head diff table** (`frontend/components/compare-diff-table.tsx`): previously this
page was genuinely just two independent reports with no synchronized comparison — a real gap
for a page literally named "Compare". `ComparePageInner` lifts each `CompareColumn`'s finished
`Report` up via a stable (`useCallback`, empty deps) `onReport` callback — each column still
owns its own SSE fetch entirely; the parent only reads the result, never re-fetches. Once both
symbols have a loaded report, `CompareDiffTable` renders above the two columns: a metric-by-
metric table (verdict, confidence, P/E, P/B, EPS, market cap, dividend yield, beta, signal
score, plus whatever `research.ratios` keys both companies share) with the stronger side
highlighted — but **only** for metrics with a documented, unambiguous direction (lower P/E and
P/B read as cheaper; higher EPS/market cap/yield/signal-score/growth-or-margin-ratios read as
stronger). A `research.ratios` key that doesn't match a known direction hint is shown side by
side with no highlight — "better" is never asserted for a ratio this app doesn't recognize,
same "never invent a judgment" instinct as the rest of this codebase.

### Verdict history flow

"How does today's call compare to a past one for this stock?" was previously unanswerable
in the web app — the CLI wrote a dated `report_<date>.json` per run (`main.py`), but that
file never left disk, and `api.py`'s SSE endpoint didn't write anything comparable at all.

1. `verdict_history.py` (repo root, alongside `cache.py`) is a small persistence module with
   two functions: `save_snapshot(symbol, analysis, signal_context, stock_info)` upserts one
   row per `(symbol, verdict_date)` into the `verdict_history` Postgres table (recommendation,
   confidence, current_price, signal_score); `load_history(symbol, limit=60)` reads them back
   oldest-first. Both are best-effort — a missing `DATABASE_URL` or a DB hiccup is logged and
   swallowed, never raised, the same convention `state_store.py` uses for its own audit writes.
2. `save_snapshot()` is called from **both** entry points that produce a report — `main.py`'s
   CLI pipeline (all three exit paths: cache-hit early return and the normal run) and `api.py`'s
   `/api/analyse/{symbol}` SSE stream, right after `_build_report()` — so the timeline reflects
   web usage and CLI usage identically, the same lockstep `main._build_report()` already
   enforces between the two entry points. A same-day re-run (cache hit, force refresh) upserts
   the existing row instead of adding a duplicate, so "one row per day" holds regardless of how
   many times the pipeline actually ran that day.
3. `GET /api/verdict-history/{symbol}` is pure read-aggregation over `load_history()` — no LLM
   calls, no scraping. Degrades to `{"symbol": ..., "history": [], "win_rate": null,
   "scored_count": 0}` (200, not 503) when `DATABASE_URL` is unset or the query fails, matching
   `/api/consolidated`'s "a missing section isn't an error" philosophy, since this is a
   supplementary strip on top of a report that has already loaded successfully — a DB hiccup
   here must not look like the whole analysis failed.
4. **Outcome scoring** — the single-stock analogue of the win-rate `GET /api/market-picks/history`
   already tracks for the weekly picks list: each stored verdict is additionally scored against
   *today's* live price (one extra `yfinance` call via `_fetch_live_price_sync()`, the same
   helper `GET /api/prices` uses, extracted out so both endpoints share it). `_score_verdict_history()`
   computes `return_since_pct` (an observed fact, populated whenever both the stored and live
   price are known) and `outcome` (`'win' | 'loss' | null`) per entry — but only for `BUY`/`SELL`
   calls; a `HOLD` makes no directional claim, so grading it against a price move would be
   inventing a judgment the verdict itself never made, the same "never invent" instinct applied
   to a derived field instead of a scraped one. A live-price fetch failure (including this
   sandbox's lack of outbound internet) degrades `return_since_pct`/`outcome` to `null` on every
   entry rather than failing the whole response. The response also carries a per-symbol
   `win_rate` (% of scored BUY/SELL entries that were a win) and `scored_count`.
5. `ResultsDashboard`'s hero renders a `VerdictTimeline` strip (fetched independently, same
   pattern as `PriceSparkline`/`usePeerComparison`) showing each stored day as a small
   recommendation badge with its date and a ✓/✗ win/loss mark (green/red, independent of the
   badge's own BUY/HOLD/SELL color), chained left-to-right, latest one ring-highlighted, with a
   "`X`% right so far (`N` scored)" summary next to the strip's label when at least one entry has
   been scored. Needs at least 2 stored days to render at all — a symbol analysed for the first
   time today has nothing to compare against yet.

### PWA installability

The frontend is installable (Chrome "Add to Home Screen" / desktop install prompt) and previously-
visited pages/static assets keep working offline. No new npm dependency — everything is built on
Next.js App Router's own metadata file conventions plus `next/og`'s `ImageResponse` (already bundled
with `next`, normally used for Open Graph images):

1. `frontend/app/manifest.ts` — the App Router manifest file convention; Next.js serves it at
   `/manifest.webmanifest` and auto-injects the `<link rel="manifest">` tag, no manual wiring needed.
2. Icons are generated at request time via `ImageResponse` (JSX → PNG), not static files, so there
   was no need to hand-produce or check in binary image assets:
   - `frontend/app/icon.tsx` (32×32) and `frontend/app/apple-icon.tsx` (180×180) are Next's own
     favicon/apple-touch-icon file conventions — Next auto-generates the `<link>` tags for both.
   - `frontend/app/manifest-icons/[size]/route.tsx` is a plain Route Handler (not a Next metadata
     convention file — those only support one fixed size each) serving the 192×192 and 512×512 PNGs
     `manifest.ts`'s `icons` array points at; any other `size` param 404s.
   - All three render the same navy-background/blue-"AP" mark inline via `ImageResponse`'s
     satori-backed CSS subset (flexbox required explicitly) — no external image tooling.
3. `frontend/public/sw.js` is a minimal hand-written service worker (no Workbox/next-pwa) registered
   from `frontend/components/service-worker-registration.tsx` (mounted once in `app/layout.tsx`,
   renders nothing, and **only in production** — registering in `next dev` would install a real,
   persisted service worker in every engineer's dev browser profile that then keeps intercepting
   static assets across future dev sessions): cache-first for same-origin static assets,
   network-first-with-cache-fallback for navigations (so a previously-visited page still loads
   offline, falling back to a plain "You are offline." response if nothing at all is cached yet), and
   **`/api/*` is never intercepted** — this is a live stock-data tool, and serving a cached quote/
   verdict while offline would be actively misleading rather than a helpful fallback, unlike a typical
   content-site PWA. Navigations are also never cached when the URL carries a query string — the app
   has at least one route (`/auth/verify?token=...`) where the query string IS a sensitive, single-use
   credential, and the Cache API keys entries by full URL, so caching it would persist that secret in
   Cache Storage indefinitely; skipping *every* query string (not just that one route) is the safe
   default for a general-purpose service worker that shouldn't need route-specific knowledge of which
   params are sensitive. Only successful (`response.ok`) responses are ever cached, on both the
   navigation and static-asset paths, so a transient 5xx never gets served as the offline fallback.
4. `app/layout.tsx` also exports `viewport.themeColor` (`#0b1120`, matching `bg` in
   `tailwind.config.ts`) and `metadata.appleWebApp` for the iOS status-bar/home-screen title.

### Shared state and queues

- **No shared in-memory state** between requests. Each request runs its own pipeline instance.
- **Inter-phase communication** within the market picks pipeline uses direct function return values (not queues). The `asyncio.Queue` is only used to bridge the blocking thread back to the async SSE loop.
- **Cache** (`output/`) is the persistent shared state for stock analysis and market picks; concurrent writes to different symbols are safe (each symbol has its own subdirectory) — the one exception is the `"_MACRO"` pseudo-symbol (see "Macro overlay signal" above), where several `market_picks_pipeline.py` worker threads researching different real stocks can all miss the same `fii_dii_flow`/`macro_context` cache entry at once and race to fill it; `cache.save()` writes atomically (tempfile + `os.replace`) so a race there produces at most a few redundant NSE/RBI fetches, never a corrupt cache file. SME signals persist to PostgreSQL instead (idempotent upserts keyed on symbol + trade_date).

### SSE bridge pattern (critical)

```python
async def _launch():
    await loop.run_in_executor(None, blocking_fn)

asyncio.create_task(_launch())   # create_task needs a coroutine, not a Future
```

Never pass `loop.run_in_executor(...)` directly to `create_task` — it returns a `Future`, not a coroutine, and will raise `TypeError` at runtime.

---

## Important Rules for Claude

- **Respect the architectural constraints in the root [`CLAUDE.md`](../CLAUDE.md).** This is a
  deliberately boring monolith: PostgreSQL is the only datastore, Redis is optional and never a
  hard dependency, parallelism is `ThreadPoolExecutor`, scheduling is GitHub Actions cron. Do not
  introduce a broker (Celery/Kafka/RabbitMQ/Temporal), a second database, an orchestrator
  (Airflow/Prefect), Kubernetes, or event sourcing — and do not scaffold "for later." If a task
  appears to need one, say so and stop rather than building it. The binding list and the reasoning
  are in that file; `docs/backlog.md` links to it.
- **Scope every pipeline's `--reset-db` to the tables that pipeline owns.** Never
  `metadata.drop_all()` — the shared `MetaData()` carries all 22 tables, six of which hold
  non-regenerable personal financial data. See `docs/database.md` for the ownership map.
- **`output/` is cache only. Durable state goes to PostgreSQL.** Every file under `output/` must
  be regenerable by re-running something; if losing it would lose real information, it belongs in
  the database. Anything shaped like "a JSON blob under a key" goes through `state_store.py`
  (`load`/`save`/`items`/`mutate` over the `app_state` table) under its own namespace, rather than
  a new directory of files or a new near-identical table — that module replaced six such
  directories. Use `mutate()`, never `load()`-then-`save()`, wherever more than one worker can
  touch the same key: it holds a row lock for the whole read-modify-write, which is what the three
  hand-rolled `fcntl.flock` helpers it replaced existed to do (and it works across hosts, which
  they did not). Every entry point degrades to a logged no-op without `DATABASE_URL`, so callers
  stay best-effort.
- **Schema boundary is sacred.** Raw tool output must be normalized through `schemas.normalize()` before being passed to cache, guardrails, signal engine, or analyst prompt. If a tool changes its output shape, only `schemas.py` needs updating.
- **Never add fields to the analyst JSON output schema** without also updating `config/analyst.json` (`output_schema`), `crew._validate_analysis_payload()`, `main._build_report()`, and `frontend/types/index.ts` (`Analysis` interface). These four are in lockstep.
- **Tools must not raise.** All functions in `tools/` must return `{"error": "...", ...}` on failure. The cache layer silently discards error payloads; guardrails detect them and trigger retries.
- **Run `npx tsc --noEmit` in `frontend/`** before marking any frontend task done. This does NOT catch everything — a CSS syntax error, for example, only surfaces under the production minifier (`npm run build`), not `tsc` or `next dev`. CI runs both; when in doubt, especially after touching `globals.css` or raw CSS, run `npm run build` locally too.
- **Cache TTLs are intentional.** `stock_info` and `news` are 1 h; `research` is 24 h; `shareholding`/`mf_holdings` are 168 h (7 days). Do not shorten these without understanding the NSE rate-limit implications.
- **The analyst step is expensive.** It only re-runs when at least one input task was stale. Do not add logic that forces it to re-run unconditionally.
- **Market picks pipeline max stocks = 35** (`_MAX_STOCKS` in `market_picks_pipeline.py`). Raising this significantly increases wall-clock time and LLM costs.
- **4-tier recommendation in market picks**: BUY / WATCHLIST / HOLD / SELL. Do not collapse these to 3-tier. `WATCHLIST` is a distinct lower-conviction tier between BUY and HOLD.
- **Trade levels are deterministic in market picks** (entry/target/stop computed from signal score and 52w range). Do not add LLM-driven price generation — it produces null values when context overflows.
- **Extraction cache** (`output/_extract_cache/`) avoids re-calling the LLM for the same source articles within 6 h. The cache key is content-aware (title + URL + summary hash), so edits or new articles get a fresh key automatically. Expired files aren't just ignored on read — `_prune_extract_cache()` deletes them once per pipeline run, or this directory grows by one file per (source, article-batch) forever.
- **Source credibility weights** in `_SOURCE_CREDIBILITY` determine how much each source contributes to confidence scoring. Adding a new source requires adding a credibility entry; missing sources default to 0.50.
- **HDFC Securities sources** live in `tools/hdfc_sec_agent.py` and are merged into `SOURCES` / `SCRAPER_FNS` at import time in `tools/market_picks_tools.py`. Adding a new brokerage source follows the same pattern: define scrapers in a separate module, export `*_SOURCES` and `*_SCRAPERS`, merge in `market_picks_tools.py`.
- **Rate limiting** is a sliding window (`api.py`'s `_rate_limit()` → `rate_limiter.is_allowed()`), applied only to expensive/abusable routes: `/api/analyse/{symbol}` (20 req / 5 min per IP), `/api/market-picks?force=true` (3 req / hour per IP), `/api/sme-signals/refresh` (3 req / hour per IP, on top of the existing single-run guard). Backed by Redis (shared across workers) when `REDIS_URL` is set, an in-memory per-process counter otherwise — see "Shared-state rate limiting" below. The "per IP" is `api.py::_client_ip()`, not raw `request.client.host` — see "Trusted client IP for per-IP rate limiting" below for why that distinction matters given every request arrives via the Next.js proxy routes.
- **The `market_picks_history` snapshot schema** (`state_store.py`) (`symbol`, `confidence`, `effective_signal`, `mention_count`, `current_price`, `recommendation`) is read by two independent consumers: the in-pipeline `_load_trend()` (confidence trend) and `GET /api/market-picks/history` (price track record, `/market-picks/history` page). Snapshots written before `current_price`/`recommendation` were added won't have them — the history endpoint handles this by returning `change_pct: null` rather than guessing. Keep both consumers in mind if the snapshot shape changes.
- **`GET /api/market-picks/history`** also computes an overall `win_rate` (share of tracked picks with `change_pct > 0`), a `tier_stats` breakdown keyed by `recommendation_then` (count/avg change/win rate per BUY/WATCHLIST/HOLD/SELL), and per-symbol `nifty_change_pct`/`alpha_pct` benchmarked against `^NSEI` over the same `first_seen` → `last_seen` window (`avg_alpha_pct` at the top level). The Nifty series is fetched once per request-range via `yfinance.Ticker("^NSEI").history()` — not once per snapshot date — and cached through `cache.py` using `"NSEI"` as a pseudo-symbol (`index_history`, 24 h TTL, re-fetched whenever a new snapshot date widens the needed range). A closed-market snapshot date (weekend/holiday) falls back to the nearest earlier trading day's close, never a later one. A yfinance outage degrades to `null` alpha fields, not a failed request.
- **CORS** is restricted via `CORSMiddleware` to origins in `ALLOWED_ORIGINS` (comma-separated env var, defaults to `http://localhost:3000`). This is defense in depth, not something normal operation relies on — the Next.js proxy routes talk to the backend server-to-server, which CORS doesn't apply to. Add your production frontend's origin to `ALLOWED_ORIGINS` before deploying, or direct browser calls to the backend will be rejected.

---
