# AlphaPulse — Product Requirements Document

**Status:** Living document. This is the **strategy layer** — vision, goals, principles, priority,
roadmap, and business context. For the detailed "what is already built" inventory, see
[`feature-catalog.md`](feature-catalog.md). For exhaustive engineering/behavioral detail, see
[`../backend/CLAUDE.md`](../backend/CLAUDE.md) (backend) and
[`../frontend/CLAUDE.md`](../frontend/CLAUDE.md) (frontend) — the single source of implementation
truth. Where any of these could drift, the `CLAUDE.md` files describe implementation truth and
the others should be corrected to match them, not the other way round.

**A note on candor:** several sections below are marked **DRAFT** or **TBD**. That's deliberate —
this document follows the same "never invent" discipline the product itself is built on (see
Product Principles below). A competitive claim, a KPI target, or a roadmap date that isn't backed
by real market research or a real decision is a fabrication dressed up as a plan, and this
document exists to prevent exactly that kind of confident-looking guess. Where a number or
decision genuinely doesn't exist yet, it says so.

---

## 1. Executive Summary

AlphaPulse is an AI-powered equity research platform for Indian retail investors (NSE/BSE), built
around a strict discipline: run deterministic, disclosed quantitative analysis first, use an LLM
only for synthesis on top of it, and never let either component invent a number that isn't
actually in the data. Individual investors today stitch together a broker's app, Screener.in,
NSE's filings portal, news sites, and their own memory to research a stock — none of these
individually apply a repeatable framework, and none remember what they told you last time.

AlphaPulse's near-term goal is to become the highest-trust, fastest research companion for this
audience — trustworthy enough that its own track record (not just its confidence) is the pitch,
and fast enough that it becomes a daily habit rather than an occasional lookup. The long-term goal
(§13) is to become the one place an Indian retail investor's research and portfolio tracking
happens, instead of ten disconnected tools.

This is a single-engineer project today (§17.1) — real, and disclosed rather than hidden.

---

## 2. Problem & Vision

Individual investors researching Indian equities (NSE/BSE) stitch together data from half a
dozen disconnected places — a broker's app for quotes, Screener.in for fundamentals, NSE's own
filings portal for disclosures, Twitter/news sites for sentiment, and their own memory for
"what did I decide about this stock last month." None of these individually apply a consistent,
repeatable analysis framework, and none of them tell an investor when a call has already been
made and changed.

**AlphaPulse's vision:** one place that (a) pulls every relevant public data source for a stock,
(b) applies the same disciplined, disclosed analysis framework every time — quantitative signals
first, LLM synthesis second, never inventing a number that isn't actually in the data — and
(c) remembers what it told you, so you can see whether its own calls have been right.

---

## 3. Product Goals

### Primary Goal
Become the research companion Indian retail investors trust enough to return to daily — trust
earned through a visible, checkable track record, not through confident-sounding prose. Speed
(minutes, not hours, to a decision) and daily-use habit are downstream of that trust, not
competing goals: nobody returns daily to a tool they don't trust, no matter how fast it is.

### Secondary Goals
- Reduce research time per stock from a multi-source, multi-hour manual process to a single
  query.
- Improve investment decision quality by applying the same disciplined framework every time,
  instead of ad hoc gut calls.
- Build reusable research memory — verdict history, stake-delta tracking, track-record scoring —
  so a user's (and the product's own) past calls compound into better future ones, rather than
  every session starting from zero.
- Become the daily entry point for "what should I look at / do about my portfolio today,"
  anchored by the shared search box and the Watchlist/Portfolio surfaces already built for this.

### Non-Goals
Explicitly out of scope for the foreseeable future:
- **Live trading / brokerage execution.** No buy/sell order placement, ever — this is a research
  and tracking tool, not a brokerage.
- **Intraday/real-time trading terminal.** No tick-by-tick charts, order books, or Level 2 data —
  the product is built around a batch-fetch, disciplined-analysis model (see Data Freshness in
  the Feature Catalog), not a streaming terminal.
- **Global (non-Indian) markets.** NSE/BSE stays the entire universe for the foreseeable future.

---

## 4. Product Principles

These are decision filters, not aspirations — every one below is already a load-bearing
engineering constraint in the codebase, not a marketing statement:

- **Accuracy > Speed.** A guardrail retry (or a labeled, degraded HOLD fallback) is preferred over
  a fast, wrong answer.
- **Explain > Predict.** Every recommendation is grounded in cited source data, not a bare
  prediction with no traceable reasoning.
- **Data > Opinion.** A missing scraped field is `null`, never a guessed plausible-looking value.
- **Memory > Conversation.** There is no chat interface. The product remembers what it told a user
  (verdict history, stake deltas, track record) instead of asking them to re-explain context in a
  conversation.
- **Deterministic > Magical.** Trade levels (entry/target/stop-loss), DCF estimates, and signal
  scores are all computed, never LLM-generated — the LLM synthesizes and explains, it doesn't
  invent numbers.
- **Transparency > Confidence.** A degraded/fallback analysis is visibly labeled as such, never
  presented indistinguishably from a genuine analyst call.
- **Trust > Engagement.** Every feature above defers to trust when the two are in tension — see
  Product Goals §3.

---

## 5. Target Users

| Persona | Goals | Frustrations today | Typical frequency & session | Success looks like |
|---|---|---|---|---|
| **Retail investor researching one stock** | A fast, trustworthy BUY/HOLD/SELL read on a specific ticker, backed by real fundamentals/news/filings — not a black box. | Stitching together a broker app, Screener.in, and news manually; no single disciplined framework. | Ad hoc, triggered by a specific stock they're considering; single-session, 5-15 min. | Reaches a confident decision in one session, with sources they can check. |
| **Screener/discovery user** | "What's worth looking at this week?" — a ranked, sourced watchlist rather than reading ten brokerage notes by hand. | No single ranked, cross-source discovery feed exists elsewhere. | Weekly, aligned to Market Picks' own refresh cadence. | Finds 1-2 genuinely new ideas per week worth a deeper look. |
| **Momentum/technical trader (SME segment)** | A systematic golden-cross/death-cross screener over the NSE Emerge + BSE SME universe, which mainstream tools don't cover well. | Mainstream screeners (Screener, Tickertape) have thin or no SME/Emerge coverage. | Daily/near-daily check during active trading periods. | Catches a real cross signal before it's obvious from price action alone. |
| **Active portfolio tracker** | A place to star stocks, log actual buys, and see aggregate P&L — without needing a real brokerage integration. | Tracking buys/targets/stop-losses in a spreadsheet or memory. | Ongoing, checked whenever the market moves meaningfully. | Sees aggregate portfolio health at a glance without manual bookkeeping. |
| **Power user / builder** | Programmatic API access (API keys, `/api/v1/*`) to pull AlphaPulse's own aggregated view into their own tooling. | No API-accessible source of the same synthesized view exists elsewhere at this price point. | Automated/scripted, not session-based. | Integrates AlphaPulse's view into their own workflow without re-building the aggregation themselves. |
| **Personal finance tracker** | A single net-worth view across brokers, mutual funds, FDs, EPF/PPF, and cash — imported from CAS statements or broker CSVs rather than re-typed by hand. | Net worth lives across 4+ disconnected apps/PDFs; no single aggregated view. | Monthly/quarterly check-ins, plus one-time import sessions. | Imports once, sees true net worth without manual re-entry going forward. |

*(This table is a reasonable first draft from the product's own feature set, not user-research-validated personas — treat the "frustrations/frequency/success" columns as hypotheses to confirm, not settled fact.)*

---

## 6. User Journey

Two distinct journeys exist today, matching the two genuinely separate product surfaces
(Market-Picks-driven positions vs. the standalone Portfolio Aggregator):

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

Both journeys currently start and end in isolation from each other — a stock analyzed via the
first journey and a stock held in the second aren't cross-referenced today. Closing that gap
(e.g. "you're already researching TCS — you also hold it in your Portfolio Aggregator") is a
plausible future integration point, not yet built.

---

## 7. Trust Framework (the core differentiator)

This is AlphaPulse's actual moat, and it's stronger, more specific messaging than "AI stock
research" — every stage below is a real, already-built check, not aspirational:

```
Raw Data (6 scraped sources, "never invent" convention — missing = null, not guessed)
        │
        ▼
Signals (6-signal deterministic quant engine, sector-aware weighting)
        │
        ▼
Guardrails (structural checks, grounded-claims check, bidirectional directional
            cross-check, numeric-misread check — reject and retry once, then a
            labeled degraded fallback, never a confident-looking guess)
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

Every one of these seven stages is a real, checkable behavior in the shipped product (see
`docs/feature-catalog.md` and `CLAUDE.md` for exactly how). This is the honest differentiator to
lead with in any external-facing messaging — not "we use AI," but "here's the checkable pipeline,
and here's our own track record against it."

---

## 8. AI Strategy

The operating principles for every LLM call in the system (already true of the shipped code, not
a proposed policy):

- **Never invent numbers.** A dividend yield, P/E, or trade level either comes from real source
  data or is computed deterministically — the LLM never originates one.
- **Always cite.** Every claim in a report must be grounded in the data actually fetched for that
  symbol; an unsupported claim fails the grounded-claims guardrail.
- **Quant before LLM.** The six-signal engine runs first and produces a bounded score before the
  LLM ever sees the data — the LLM synthesizes around a quantitative anchor, it doesn't reason
  from a blank slate.
- **Guardrails before output.** No LLM response reaches a user unvalidated — structural checks,
  directional cross-checks, and the numeric-misread check all run before anything is shown.
- **Memory over chat.** There is no conversational interface — the product's "memory" is
  structured history (verdict snapshots, stake deltas), not a chat log.
- **Repeatability over creativity.** The same inputs should produce the same disciplined
  analysis — this is a research tool, not a creative-writing one.

---

## 9. Product Moat

```
Unified data aggregation (6 core scraped sources + a dozen bolt-on ones — peers,
financials, insider activity, street consensus, macro overlay, EOD price store)
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
Historical memory (verdict history, MF stake deltas, SME/screener daily snapshots)
        │
        ▼
Performance tracking (win-rate scoring, alpha vs. Nifty, per-tier stats — the
                       product's own track record is a real, computed artifact)
        │
        ▼
Continuous improvement (10 rounds of adversarial code-review audits already
                          shipped — a demonstrated, repeatable quality process)
```

No single stage here is unique in isolation (every research tool aggregates some data; several
LLM tools have guardrails). The moat is the full stack, end to end, with a real, checkable track
record at the bottom of it — that combination is what's hard to replicate quickly.

---

## 10. Competitive Landscape — **DRAFT, unverified**

*This table uses the framing an outside product review suggested. It has not been checked
against each competitor's actual current pricing, feature set, or positioning — treat every cell
as a hypothesis to confirm before using externally, the same "disclosed, not silently assumed"
convention this codebase applies to every unverified scraper assumption.*

| Competitor | Strength | Weakness (unverified) | Why AlphaPulse's pitch differs |
|---|---|---|---|
| Screener.in | Deep, trusted fundamentals data | No synthesis or recommendation layer | AlphaPulse adds a disciplined, guardrailed AI synthesis layer on top of (and citing) the same kind of fundamentals data |
| Trendlyne | Broad data + analyst consensus | Pricing tier structure unconfirmed | AlphaPulse unifies fundamentals + signals + LLM synthesis + track record in one flow |
| Tickertape | Polished UI/UX | Reasoning/synthesis depth unconfirmed | AlphaPulse's differentiator is the checkable trust pipeline (§7) and research memory, not UI polish alone |
| Moneycontrol | News/data breadth, brand trust | Manual synthesis — no repeatable framework | AlphaPulse applies the same structured framework every time instead of requiring the reader to synthesize manually |

---

## 11. Business Model

### Revenue — current state
- **Free / Pro API tiers exist in code** (`users.tier`, 100 calls/hr free, 1,000/hr pro) — but
  **no self-serve checkout exists**; tier is set by an operator by hand. This is disclosed
  honestly on the product's own `/pricing` page rather than faked.
- **No pricing (₹ amounts, plan names) has been decided yet.** Standing up real billing is a
  business decision (processor choice, actual price points, India-specific tax/compliance, refund
  policy) that has to precede engineering work — see §17.3. This document will not invent numbers
  for it.

### Revenue — plausible future directions (not committed, not scoped)
- Subscriptions once self-serve billing exists.
- Paid `/api/v1/*` tiers beyond the current single endpoint, once real external demand is
  confirmed.
- B2B/enterprise access (e.g. white-label or bulk API access) — directionally plausible given the
  existing tiered-API foundation, not evaluated in depth.

---

## 12. Success Metrics

**What's already real and computed today** (the one category not marked TBD):
- **Market Picks track record** — per-tier win rate, average change%, alpha vs. Nifty, computed
  and surfaced on the product's own track-record page. This is the closest thing AlphaPulse has
  to a "prediction accuracy" metric today, and it's real.
- **Verdict history win/loss scoring** — per-stock, scored against actual subsequent price moves.

**Everything else below is a proposed measurement framework, not a dashboard that exists yet** —
this codebase has no analytics/telemetry layer for user behavior today, so these are what to
start instrumenting, not numbers to report against:

| Category | Candidate metric | Status |
|---|---|---|
| North Star (proposed) | Weekly Active Investors | Not instrumented |
| Input | Analyses run / user | Not instrumented |
| Input | Watchlist additions | Not instrumented (data exists in DB; no rollup/dashboard) |
| Input | Market Picks opens | Not instrumented |
| Input | Portfolio Aggregator imports (CAS/CSV) | Not instrumented (data exists in DB; no rollup) |
| Output | Recommendation acceptance (did the user act on it?) | Not instrumented — no concept of "acted on" exists today |
| Output | 30-day retention | Not instrumented |
| Output | Paid conversion | N/A — no self-serve payment exists (§11) |
| Output | Prediction accuracy | **Real today** — see Market Picks track record / verdict history above |
| Output | Research completion rate (did the SSE stream finish?) | Partially inferable from existing structured logs, not surfaced as a metric |

**Recommendation:** before setting numeric targets on any "Not instrumented" row, stand up basic
usage analytics first — setting a target for a metric nobody can currently measure just produces
a number nobody can verify.

---

## 13. Long-Term Vision (2–3 years)

Not another Screener, not another broker. **An AI investment operating system for Indian retail
investors** — the one place research, tracking, and net-worth aggregation happen, so a user stops
needing to visit ten different sites to make one investment decision. Every feature shipped
should move toward that: fewer tabs open, more decisions made in one place, backed by a visible
track record rather than a confident tone.

---

## 14. Feature Priority

| Tier | Features |
|---|---|
| **P0** | Stock Analysis, Market Picks |
| **P1** | Watchlist, Portfolio/Positions, Compare |
| **P2** | Accounts & API access, Portfolio Aggregator, SME Signals, Screener |
| **P3** | Broader developer/programmatic API surface |

This mirrors the product's own core-vs-adjacent shape: Stock Analysis and Market Picks are the
trust-building core (§7-§9); Watchlist/Portfolio/Compare extend daily-use habit around that core;
Accounts, Portfolio Aggregator, and the specialist screeners (SME, NIFTY 500) serve real but
narrower segments; the broader API surface is deliberately last, per the existing "ship one real
endpoint, not a speculative surface" scope call already in the codebase.

---

## 15. Roadmap

**Now:**
- Push notifications and better screener filters — the two most-cited near-term gaps, and the
  confirmed next focus.

**Next / Later:** not yet decided — rather than invent a plausible-sounding sequence, this is left
as an open prioritization question. `docs/feature-catalog.md`'s "Documented Product Gaps" style
list (real push-notification infra, a backtested signal model, a broader `/api/v1/*` surface) are
the leading candidates once "Now" is done, not a committed order.

**Explicitly and permanently declined (not "later," but "no," with a stated reason):**
- **IPO grey-market-premium (GMP) data.** Considered and rejected: GMP is an unregulated,
  informal indicator that SEBI itself has warned doesn't reflect a security's real value, sourced
  only from grey-market-tracking portals with materially different ToS/reliability risk than
  this app's existing regulator/vendor sources. Revisit only if a specific, reliable,
  ToS-compatible source is identified.

---

## 16. Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| NSE/BSE/Screener.in/Trendlyne blocks or changes scraper access | Medium | High — several core data slices go stale or empty | Schema-drift detection, source-health monitoring, per-scraper error counters already shipped; graceful "temporarily unavailable" degradation, not a crash |
| LLM hallucination / fabricated numeric claim | Medium (mitigated) | High if unmitigated | The entire Trust Framework (§7) exists specifically for this — guardrails, numeric-misread check, degraded-fallback labeling |
| Broad market crash / regime shift | Low-Medium | Medium — signal weights aren't back-tested against a realized-return dataset, so calibration confidence is unproven in a real drawdown | Documented, disclosed gap (see "Documented Product Gaps" in the Feature Catalog); a real backtest harness is the mitigation, not yet built |
| Scraper/API rate-limit or cost spike (LLM or data-source) | Medium | Medium — could degrade freshness or blow a cost budget | Per-call LLM cost instrumentation with a daily running total; sliding-window rate limiting (Redis-backed) already shipped |
| No legal/compliance review of the scraping surface | Unknown (unassessed) | High if a source objects | See §17.2 — requires a licensed professional, not an engineering fix |
| Bus factor of one | High (certain, today) | High for anyone relying on this as durable infrastructure | See §17.1 — requires a real second engineer or a written handoff plan |

---

## 17. Explicitly Out of Scope: Organizational, Legal & Business

These three items are **not engineering problems** — no amount of further code work closes them.
Restated here because a PRD that omits them would misrepresent the product's actual readiness for
scale.

### 17.1 Bus factor of one
The entire commit history traces to a single human author (with AI pair-programming assistance).
The density of `CLAUDE.md` itself is real engineering discipline, but it is not evidence a team
exists — if anything it reads as compensation for the absence of one. **Needs:** a real second
engineer, or at minimum a written handoff plan, before this is treated as infrastructure a
business depends on.

### 17.2 No legal/compliance review of the scraping surface
AlphaPulse scrapes `screener.in`, `nseindia.com`/`nsearchives.nseindia.com`,
`bseindia.com`/`api.bseindia.com`, `trendlyne.com`, `rbi.org.in`, plus GNews-mediated coverage of
several news publishers, on a recurring schedule at a scale beyond casual use — with no confirmed
Terms-of-Service review by qualified counsel behind any of it. (This is also *why* Watchlist/
Positions ownership is never auto-migrated on sign-in, and why the account-claim flow is
tightly rate-limited and audit-logged — the product's default posture throughout is "ask,
disclose, and bound the blast radius," not "assume it's fine.") **Needs:** a licensed
professional reviewing each source's actual ToS and applicable Indian data-protection/scraping
case law before scaling traffic materially beyond where it sits today.

### 17.3 No real payment processing
`users.tier` and the informational `/pricing` page exist specifically to *stop short* of a real
checkout flow — there is no payment processor integration, and `'pro'` tier is set by an operator
by hand today. This is disclosed by design, not an oversight: standing up real billing is itself
a business decision (processor choice, actual pricing, India-specific tax/compliance, refund
policy) that has to precede engineering work, not follow it. **Needs:** those business decisions
made first; the engineering task that follows is then a normal, scoped piece of work.

---

## Appendix — Documentation Map

| Doc | Purpose |
|---|---|
| `docs/PRD.md` (this doc) | Strategy layer — vision, goals, principles, personas, priority, roadmap, business context. Read this for "what is AlphaPulse trying to become, and why." |
| `docs/feature-catalog.md` | Implementation inventory — every shipped feature area, one level above `CLAUDE.md`. Read this for "what's already built." |
| `CLAUDE.md` | Root-level engineering overview + pointers — project summary, repo structure, cross-stack conventions. |
| `backend/CLAUDE.md` | Exhaustive backend engineering reference — the ground truth for every feature's exact behavior, disclosed limitations, and design rationale. Read this for "how does X actually work" on the Python/API side. |
| `frontend/CLAUDE.md` | Frontend engineering conventions, testing, and PWA/design-system notes. |
| `README.md` | Quickstart — install, run, top-level feature summary. |
| `docs/setup.md` | Full environment variable reference, local dev setup, troubleshooting. |
| `docs/deployment.md` | Docker Compose, manual deployment, scaling guidance. |
| `docs/architecture.md` | System-level request flows and module boundaries. |
| `docs/tools.md` | Reference for every data-fetching tool/scraper and its output shape. |
| `docs/output-schema.md` | JSON schema reference for the report, cache files, and standalone endpoint responses. |
| `docs/design.md` | AlphaPulse Design System — colors, typography, component patterns. |
