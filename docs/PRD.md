# AlphaPulse — Product Requirements Document

**Status:** Living document. This is the **strategy layer** — vision, goals, principles, priority,
roadmap, and business context. For the "what is already built" inventory, see
[`feature-catalog.md`](feature-catalog.md). For engineering detail, see
[`../backend/CLAUDE.md`](../backend/CLAUDE.md) and [`../frontend/CLAUDE.md`](../frontend/CLAUDE.md)
— those are implementation truth; where this document drifts from them, correct this one.

**A note on candor:** several sections below are marked **DRAFT** or **TBD**. That's deliberate.
A competitive claim, a KPI target, or a roadmap date not backed by real research or a real
decision is a fabrication dressed up as a plan — the same "never invent" discipline the product
itself is built on. Where a number or decision doesn't exist yet, this document says so.

---

## 1. Executive Summary

AlphaPulse is an AI-powered equity research platform for Indian retail investors (NSE/BSE), built
around a strict discipline: run deterministic, disclosed quantitative analysis first, use an LLM
only for synthesis on top of it, and never let either component invent a number that isn't in the
data.

Its near-term goal is to be the highest-trust, fastest research companion for this audience —
trustworthy enough that its own track record, not its confidence, is the pitch. The long-term goal
(§13) is to be the one place an Indian retail investor's research and portfolio tracking happens.

This is a single-engineer project today (§17.1) — real, and disclosed rather than hidden.

---

## 2. Problem & Vision

Individual investors researching Indian equities stitch together half a dozen disconnected
places — a broker's app for quotes, Screener.in for fundamentals, NSE's filings portal for
disclosures, Twitter/news sites for sentiment, and their own memory for "what did I decide about
this stock last month." None of these applies a consistent, repeatable analysis framework, and
none of them tells an investor when a call has already been made and has since changed.

**Vision:** one place that (a) pulls every relevant public data source for a stock, (b) applies
the same disclosed framework every time — quantitative signals first, LLM synthesis second, never
inventing a number — and (c) remembers what it told you, so you can check whether its own calls
have been right.

---

## 3. Product Goals

### Primary Goal
Become the research companion Indian retail investors trust enough to return to daily — trust
earned through a visible, checkable track record, not confident-sounding prose. Speed (minutes,
not hours, to a decision) and daily-use habit are downstream of that trust, not competing with it:
nobody returns daily to a tool they don't trust, however fast it is.

### Secondary Goals
- Reduce research time per stock from a multi-source, multi-hour manual process to a single query.
- Improve decision quality by applying the same framework every time instead of ad hoc gut calls.
- Build reusable research memory — verdict history, stake-delta tracking, track-record scoring —
  so past calls compound into better future ones rather than every session starting from zero.
- Become the daily entry point for "what should I look at / do about my portfolio today,"
  anchored by the shared search box and the Watchlist/Portfolio surfaces already built for it.

### Non-Goals
Explicitly out of scope for the foreseeable future:
- **Live trading / brokerage execution.** No order placement — this is a research and tracking
  tool, not a brokerage.
- **Intraday/real-time trading terminal.** No tick-by-tick charts, order books, or Level 2 data.
  The product is built on a batch-fetch, disciplined-analysis model (see Data Freshness in the
  Feature Catalog), not a streaming feed.
- **Global (non-Indian) markets.** NSE/BSE stays the entire universe.

---

## 4. Product Principles

Decision filters, not aspirations — every one is already a load-bearing engineering constraint:

- **Accuracy > Speed.** A guardrail retry (or a labeled, degraded HOLD fallback) beats a fast,
  wrong answer.
- **Explain > Predict.** Every recommendation is grounded in cited source data.
- **Data > Opinion.** A missing scraped field is `null`, never a guessed plausible-looking value.
- **Memory > Conversation.** There is no chat interface. The product remembers what it told a user
  (verdict history, stake deltas, track record) instead of asking them to re-explain context.
- **Deterministic > Magical.** Trade levels, DCF estimates, and signal scores are computed, never
  LLM-generated — the LLM synthesizes and explains, it doesn't originate numbers.
- **Transparency > Confidence.** A degraded/fallback analysis is visibly labeled as such.
- **Trust > Engagement.** Where the two are in tension, trust wins (§3).

---

## 5. Target Users

| Persona | Goals | Frustrations today | Typical frequency & session | Success looks like |
|---|---|---|---|---|
| **Retail investor researching one stock** | A fast, trustworthy BUY/HOLD/SELL read on a specific ticker, backed by real fundamentals/news/filings — not a black box. | Stitching together a broker app, Screener.in, and news manually; no single disciplined framework. | Ad hoc, triggered by a specific stock; single-session, 5–15 min. | Reaches a confident decision in one session, with sources they can check. |
| **Screener/discovery user** | "What's worth looking at this week?" — a ranked, sourced shortlist rather than reading ten brokerage notes by hand. | No single ranked, cross-source discovery feed. | Weekly, matching Market Picks' refresh cadence. | Finds 1–2 genuinely new ideas per week worth a deeper look. |
| **Momentum/technical trader (SME segment)** | A systematic golden-cross/death-cross screener over NSE Emerge + BSE SME. | Mainstream screeners have thin or no SME/Emerge coverage. | Daily/near-daily during active trading periods. | Catches a real cross signal before it's obvious from price action. |
| **Active portfolio tracker** | A place to star stocks, log actual buys, and see aggregate P&L — without a brokerage integration. | Tracking buys/targets/stop-losses in a spreadsheet or from memory. | Ongoing, whenever the market moves meaningfully. | Sees aggregate portfolio health at a glance without manual bookkeeping. |
| **Power user / builder** | Programmatic access (API keys, `/api/v1/*`) to pull AlphaPulse's aggregated view into their own tooling. | No API-accessible source of the same synthesized view. | Automated/scripted, not session-based. | Integrates the view into their workflow without rebuilding the aggregation. |
| **Personal finance tracker** | A single net-worth view across brokers, mutual funds, FDs, EPF/PPF, and cash — imported, not re-typed. | Net worth lives across 4+ disconnected apps/PDFs. | Monthly/quarterly check-ins, plus one-time import sessions. | Imports once, sees true net worth without manual re-entry. |

*This table is a first draft derived from the product's own feature set, **not** user-research-validated. Treat the frustrations/frequency/success columns as hypotheses to confirm.*

---

## 6. User Journey

Two distinct journeys exist today, matching the two genuinely separate surfaces:

**Research → position-tracking journey:**
```
Discover (search box / Market Picks weekly scan)
        │
        ▼
Stock Analysis report (BUY/HOLD/SELL + full context)
        │
        ▼
Watchlist (star it) ──────────┐
        │                     │
        ▼                     ▼
  Market Picks           Corporate-action
  (ranked ideas)          calendar / alerts
        │
        ▼
"I bought this" → Position tracked (entry/target/stop-loss)
        │
        ▼
Portfolio page (aggregate P&L, win rate)
        │
        ▼
Verdict Timeline / Track Record (did the call hold up?)
```

**Personal net-worth journey (separate, no auth):**
```
Portfolio Aggregator → create profile/accounts
        │
        ▼
Import (CAS PDF or broker CSV) or manual asset entry
        │
        ▼
Nightly auto-valuation (EOD price store) + XIRR
        │
        ▼
Net worth view (by type/account)
```

The two don't cross-reference each other: a stock analysed in the first journey and a stock held
in the second are unconnected. Closing that gap ("you're researching TCS — you also hold it") is a
plausible future integration point, not built.

---

## 7. Trust Framework

Every stage below is a real, checkable behaviour in the shipped product, not aspiration:

```
Raw Data (6 scraped slices, "never invent" convention — missing = null, not guessed)
        │
        ▼
Signals (6-signal deterministic quant engine, sector-aware weighting)
        │
        ▼
Guardrails (structural checks, grounded-claims check, bidirectional directional
            cross-check, numeric-misread check — reject and retry once, then a
            second provider, then a labeled degraded fallback)
        │
        ▼
LLM (synthesis and explanation only — never generates trade levels, DCF numbers,
     or scores)
        │
        ▼
Recommendation (BUY/HOLD/SELL + confidence, cited to source data)
        │
        ▼
Memory (verdict history snapshot — every call is dated and stored)
        │
        ▼
Track Record (win/loss scored against actual subsequent price moves,
              per-tier win rates, alpha vs. Nifty)
```

This pipeline — not "we use AI" — is what external messaging should lead with. See
[`feature-catalog.md`](feature-catalog.md) and [`../backend/CLAUDE.md`](../backend/CLAUDE.md) for
exactly how each stage works.

---

## 8. AI Strategy

Operating principles for every LLM call in the system (already true of shipped code):

- **Never invent numbers.** A dividend yield, P/E, or trade level either comes from real source
  data or is computed deterministically.
- **Always cite.** An unsupported claim fails the grounded-claims guardrail.
- **Quant before LLM.** The six-signal engine produces a bounded score before the LLM sees the
  data — synthesis around a quantitative anchor, not reasoning from a blank slate.
- **Guardrails before output.** No LLM response reaches a user unvalidated.
- **Memory over chat.** The product's memory is structured history, not a chat log.
- **Repeatability over creativity.** Same inputs, same disciplined analysis.

---

## 9. Product Moat

```
Unified data aggregation (6 core scraped slices + a dozen bolt-on sources —
peers, financials, insider activity, street consensus, macro overlay, EOD prices)
        │
        ▼
Deterministic quant engine (6 signals, sector-aware, never LLM-generated)
        │
        ▼
LLM synthesis (guardrailed, never a source of numbers)
        │
        ▼
Guardrails (structural + grounded + directional + numeric-misread)
        │
        ▼
Historical memory (verdict history, MF stake deltas, daily pick snapshots)
        │
        ▼
Performance tracking (win-rate scoring, alpha vs. Nifty, per-tier stats — the
                      product's own track record as a computed artifact)
```

No stage is unique in isolation. The moat is the full stack with a real, computed track record at
the bottom of it.

---

## 10. Competitive Landscape — **DRAFT, unverified**

*Not checked against each competitor's actual current pricing, feature set, or positioning. Treat
every cell as a hypothesis to confirm before using externally.*

| Competitor | Strength | Weakness (unverified) | Why AlphaPulse's pitch differs |
|---|---|---|---|
| Screener.in | Deep, trusted fundamentals data | No synthesis or recommendation layer | Adds a guardrailed synthesis layer on top of (and citing) the same kind of fundamentals data |
| Trendlyne | Broad data + analyst consensus | Pricing/tier structure unconfirmed | Unifies fundamentals + signals + synthesis + track record in one flow |
| Tickertape | Polished UI/UX | Reasoning/synthesis depth unconfirmed | Differentiator is the checkable trust pipeline (§7) and research memory, not UI polish |
| Moneycontrol | News/data breadth, brand trust | Manual synthesis — no repeatable framework | Applies the same structured framework every time instead of requiring the reader to synthesize |

---

## 11. Business Model

### Current state
- **Free / Pro API tiers exist in code** (`users.tier`, 100 calls/hr free, 1,000/hr pro) — but
  **no self-serve checkout exists**; tier is set by an operator by hand. Disclosed on the
  product's own `/pricing` page rather than faked.
- **No pricing (₹ amounts, plan names) has been decided.** Standing up billing is a business
  decision — processor choice, actual price points, India-specific tax/compliance, refund policy —
  that must precede engineering (§17.3). This document will not invent numbers for it.

### Plausible future directions (not committed, not scoped)
- Subscriptions, once self-serve billing exists.
- Paid `/api/v1/*` tiers beyond the current single endpoint, once real external demand is
  confirmed.
- B2B/enterprise access (white-label or bulk API) — directionally plausible given the existing
  tiered-API foundation, not evaluated.

---

## 12. Success Metrics

**Real and computed today** (the one category not marked TBD):
- **Market Picks track record** — per-tier win rate, average change%, alpha vs. Nifty, computed
  and surfaced on the track-record page.
- **Verdict history win/loss scoring** — per-stock, scored against actual subsequent price moves.

**Everything below is a proposed measurement framework, not a dashboard that exists.** There is no
user-behaviour telemetry layer today, so these are what to start instrumenting — not numbers to
report against.

| Category | Candidate metric | Status |
|---|---|---|
| North Star (proposed) | Weekly Active Investors | Not instrumented |
| Input | Analyses run / user | Not instrumented |
| Input | Watchlist additions | Not instrumented (data exists in DB; no rollup) |
| Input | Market Picks opens | Not instrumented |
| Input | Portfolio Aggregator imports (CAS/CSV) | Not instrumented (data exists in DB; no rollup) |
| Output | Recommendation acceptance (did the user act?) | Not instrumented — no concept of "acted on" exists |
| Output | 30-day retention | Not instrumented |
| Output | Paid conversion | N/A — no self-serve payment exists (§11) |
| Output | Prediction accuracy | **Real today** — see track record above |
| Output | Research completion rate (did the SSE stream finish?) | Inferable from structured logs; not surfaced as a metric |

Stand up basic usage analytics before setting numeric targets on any "Not instrumented" row — a
target for a metric nobody can measure is a number nobody can verify.

---

## 13. Long-Term Vision (2–3 years)

An AI investment operating system for Indian retail investors — the one place research, tracking,
and net-worth aggregation happen. Every feature shipped should move toward that: fewer tabs open,
more decisions made in one place, backed by a visible track record.

---

## 14. Feature Priority

| Tier | Features |
|---|---|
| **P0** | Stock Analysis, Market Picks |
| **P1** | Watchlist, Portfolio/Positions, Compare |
| **P2** | Accounts & API access, Portfolio Aggregator, SME Signals, Screener |
| **P3** | Broader developer/programmatic API surface |

Stock Analysis and Market Picks are the trust-building core (§7–§9); Watchlist/Portfolio/Compare
extend daily-use habit around it; Accounts, Portfolio Aggregator, and the specialist screeners
serve real but narrower segments; the broader API surface is deliberately last, per the existing
"ship one real endpoint, not a speculative surface" scope call.

---

## 15. Roadmap

**Now:**
- Push notifications and better screener filters — the two most-cited near-term gaps, and the
  confirmed next focus.

**Next / Later:** not yet decided. Rather than invent a plausible-sounding sequence, this is left
as an open prioritization question; the leading candidates are the items in
[`feature-catalog.md`](feature-catalog.md)'s "Known Gaps & Disclosed Limitations" (a backtest
harness for the signal model, real notification infrastructure, a broader `/api/v1/*` surface,
cross-referencing the Portfolio Aggregator with Watchlist/Positions) — candidates, not a committed
order.

**Explicitly and permanently declined (not "later," but "no," with a stated reason):**
- **IPO grey-market-premium (GMP) data.** GMP is an unregulated, informal indicator that SEBI
  itself has warned doesn't reflect a security's real value, sourced only from grey-market
  portals with materially different reliability/ToS risk than this app's regulator/vendor
  sources. Revisit only if a specific, reliable, ToS-compatible source is identified.

---

## 16. Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| NSE/BSE/Screener.in/Trendlyne blocks or changes scraper access | Medium | High — core data slices go stale or empty | Schema-drift detection, source-health monitoring, per-scraper error counters, weekly live-contract checks; graceful "temporarily unavailable" degradation, not a crash |
| LLM hallucination / fabricated numeric claim | Medium (mitigated) | High if unmitigated | The Trust Framework (§7) exists for this — guardrails, numeric-misread check, degraded-fallback labeling |
| Broad market crash / regime shift | Low-Medium | Medium — signal weights aren't back-tested, so calibration is unproven in a real drawdown | Disclosed gap (Feature Catalog, "Known Gaps"); a backtest harness is the mitigation, not yet built |
| Scraper/API rate-limit or cost spike (LLM or data source) | Medium | Medium — degraded freshness or a blown cost budget | Per-call LLM cost instrumentation with a daily running total; Redis-backed sliding-window rate limiting; a global LLM concurrency ceiling |
| Regulatory status of issuing BUY/SELL calls to Indian retail investors is unassessed | Unknown (unassessed) | High if SEBI registration turns out to be required | See §17.4 — requires qualified counsel, not an engineering fix |
| No legal/compliance review of the scraping surface | Unknown (unassessed) | High if a source objects | See §17.2 — requires a licensed professional |
| Bus factor of one | High (certain, today) | High for anyone relying on this as durable infrastructure | See §17.1 — requires a real second engineer or a written handoff plan |

---

## 17. Explicitly Out of Scope: Organizational, Legal & Business

These are **not engineering problems** — no further code work closes them. Restated here because a
PRD that omitted them would misrepresent the product's readiness for scale.

### 17.1 Bus factor of one
The entire commit history traces to a single human author (with AI pair-programming assistance).
The density of the `CLAUDE.md` files is real engineering discipline, but it is not evidence a team
exists. **Needs:** a second engineer, or at minimum a written handoff plan, before this is treated
as infrastructure a business depends on.

### 17.2 No legal/compliance review of the scraping surface
AlphaPulse scrapes `screener.in`, `nseindia.com`/`nsearchives.nseindia.com`,
`bseindia.com`/`api.bseindia.com`, `trendlyne.com`, `rbi.org.in`, and AMFI, plus GNews-mediated
coverage of several news publishers, on a recurring schedule at a scale beyond casual use — with
no confirmed Terms-of-Service review by qualified counsel. (This is also *why* Watchlist/Positions
ownership is never auto-migrated on sign-in, and why the claim flow is tightly rate-limited and
audit-logged — the default posture throughout is "ask, disclose, bound the blast radius.")
**Needs:** a licensed professional reviewing each source's actual ToS and applicable Indian
data-protection/scraping law before scaling traffic materially.

### 17.3 No real payment processing
`users.tier` and the informational `/pricing` page exist specifically to *stop short* of a
checkout flow — there is no payment processor integration, and `'pro'` is set by an operator by
hand. Disclosed by design: standing up billing is a business decision (processor, pricing,
India-specific tax/compliance, refunds) that must precede engineering. **Needs:** those decisions
first; the engineering that follows is then a normal, scoped task.

### 17.4 Regulatory status of the recommendations themselves — unassessed
The product issues BUY/HOLD/SELL calls with confidence levels, price targets, and stop-losses to
Indian retail investors, and publishes its own track record against them. Whether that constitutes
regulated activity under SEBI's Research Analyst or Investment Adviser regulations — and what
registration, disclosure, or disclaimer obligations would follow — has **not** been assessed by
qualified counsel. Notably, "financial advisory" was *deliberately not* listed as a non-goal in
§3: the product's positioning on this question is genuinely open, which makes getting a real
answer more urgent, not less. **Needs:** a SEBI-competent professional's read on the current
feature set before any material distribution push. Recorded here as an open question, not as an
implied claim in either direction.

---

## Appendix — Documentation Map

| Doc | Purpose |
|---|---|
| `docs/PRD.md` (this doc) | Strategy layer — vision, goals, principles, personas, priority, roadmap, business context. |
| `docs/feature-catalog.md` | Implementation inventory — every shipped feature area, current scale, data freshness, known gaps. |
| `docs/index.md` | Entry point / index for everything under `docs/`. |
| `CLAUDE.md` | Root-level engineering overview + pointers — project summary, repo structure. |
| `backend/CLAUDE.md` | Exhaustive backend reference — every feature's exact behaviour, disclosed limitations, design rationale. |
| `frontend/CLAUDE.md` | Frontend conventions, testing gate, env config, code style. |
| `README.md` | Quickstart — install, run, top-level feature summary. |
| `docs/setup.md` | Environment variable reference, local dev setup, troubleshooting. |
| `docs/deployment.md` | Docker Compose, manual deployment, scaling guidance. |
| `docs/architecture.md` | System-level request flows and module boundaries. |
| `docs/tools.md` | Reference for every data-fetching tool/scraper and its output shape. |
| `docs/output-schema.md` | JSON schema reference for the report, cache files, and endpoint responses. |
| `docs/design.md` | AlphaPulse Design System — colors, typography, component patterns. |
