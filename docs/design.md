# AlphaPulse Design System

Single source of truth for the shipped UI. Every claim below is verified against
`frontend/tailwind.config.ts`, `frontend/app/globals.css`, `frontend/app/layout.tsx`, and the
components in `frontend/components/`. Paths are relative to the repo root.

**The rule this document exists to make enforceable** (also stated in `frontend/CLAUDE.md` and the
root `CLAUDE.md`): use the tokens in `tailwind.config.ts`; do not hard-code hex values; do not
invent new patterns. Anything not covered here follows the nearest existing pattern.

Sections marked **NOT IMPLEMENTED** describe a gap, not a shipped pattern — do not cite them as
precedent. §12 lists known drift between this document and the code.

---

## 1. Scope

**Product:** AlphaPulse — AI-powered Indian equity research (NSE/BSE). Information-dense tables,
charts, and verdict cards.

**Dark theme only.** No light mode, no toggle. `globals.css` sets `:root { color-scheme: dark; }`
and hard-codes `body { background: #0b1120; color: #e2e8f4 }`; `app/layout.tsx` sets
`viewport.themeColor = '#0b1120'` and `app/manifest.ts` sets `background_color` and `theme_color`
to the same value. Rationale: every token, opacity ramp, and chart stroke is tuned against one
background — a second theme doubles every contrast decision in §10. Do not add `dark:` variants;
they are dead code here.

**Stack constraints that shape the patterns below:** Tailwind CSS v3 with no plugins
(`plugins: []`), no ESLint, no Prettier, no CSS-in-JS, no component library, no chart library.
`tsconfig` strict mode plus the Playwright E2E suite are the only automated gates.

---

## 2. Color

Twelve colors are defined in `tailwind.config.ts` under `theme.extend.colors`. There are no
others — no numeric scales (`accent-500`), no `warning`, no `success`, no `info`.

### Surfaces

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0b1120` | Page background. Also the PWA theme/background color and the modal scrim base (`bg-bg/80`). |
| `surface` | `#0f1829` | Recessed/secondary fill: sticky `<thead>`, inactive filter chips, row hover (`hover:bg-surface/60`), scrollbar track. |
| `card` | `#132040` | Card and panel fill, popover/dropdown panels, most inputs. |
| `card-hi` | `#1a2848` | Elevated/selected state: expanded table row, stat tiles inside a card, suggestion list. |
| `border` | `#1d2e4e` | Default 1px borders and dividers. Also used as a *fill* for empty progress-bar tracks and skeletons (`bg-border/60`). |
| `border-hi` | `#243860` | Hover/active border, nav pipe separators (`text-border-hi`), scrollbar thumb, shimmer mid-stop. |

### Text

| Token | Hex | Use |
|---|---|---|
| `tx` | `#e2e8f4` | Primary text, headings, all values in a metric row. |
| `muted` | `#8093bd` | Labels, metadata, timestamps, placeholders, inactive controls, sparkline "close" line. |

`muted` was `#6b7fa8` until it was raised for contrast: at 3.99:1 on `card` it sat below the 4.5:1
WCAG AA body-text bar, and it is `MetricRow`'s label — the most-repeated text pairing in the
product. `#8093bd` measures **5.22:1 on `card`** and **6.12:1 on `bg`**. Do not darken it without
re-measuring both.

`muted` is routinely used at reduced opacity (`/70`, `/60`, `/50`, `/45`, `/40`, `/30`, `/25`) for
tertiary text. Those reduced-opacity uses still fall below AA — the base token now passes, the
faded variants do not. See §10.

### Signal

| Token | Hex | Semantic |
|---|---|---|
| `buy` | `#10d98e` | Gains, BUY, bullish, golden cross, win, upgrade, positive delta. |
| `sell` | `#e05568` | Losses, SELL, bearish, death cross, loss, downgrade, risk, destructive action. |
| `hold` | `#f5a623` | Neutral/HOLD, and — by convention, since no `warning` token exists — every caution state: degraded-analysis banner, watchlist star, illiquid badge, BSE exchange tag, EMA50 line, short horizon. |

### Accent

| Token | Hex | Use |
|---|---|---|
| `accent` | `#4d7fff` | Interactive affordances only: links, primary CTA fill, active nav item, active sort arrow, focus border, rank #1/#2–3 badges, EMA20 line, selection highlight (`::selection` at `#4d7fff40`). |

`accent` is never a data label. Where an "attention, but not good/bad" tone is needed on data,
`hold` is used instead (`results-dashboard.tsx` marks elevated volume `text-hold`, with a comment
saying exactly this).

### Legitimate raw-hex exceptions

Token classes cannot reach these, so the hex is duplicated deliberately. Keep them in sync with
the table above:

- `globals.css` — `body`, `::selection`, `::-webkit-scrollbar-*` (CSS outside Tailwind's reach).
- `app/layout.tsx` `viewport.themeColor`, `app/manifest.ts` — browser-chrome metadata.
- `app/icon.tsx`, `app/apple-icon.tsx`, `app/manifest-icons/[size]/route.tsx` — `ImageResponse`
  (satori) renders inline styles, not Tailwind classes.

Everything else in `app/` and `components/` must use tokens. One violation exists — see §12.

### Opacity ladder

Tinted signal surfaces are built from token + opacity, never a new color. Observed convention:

| Layer | Range | Typical |
|---|---|---|
| Background fill | `/5`–`/20` | `/10` or `/12` |
| Border | `/15`–`/40` | `/25` or `/30` |
| Text | full, or `/70`–`/80` to de-emphasise a tier | full |

Depth is expressed by *raising* opacity, not by switching colors — `sector-heatmap.tsx` ramps a
single tone across four steps: `buy/5 → buy/12 → buy/20 → buy/30` with borders `/20 → /25 → /30 → /40`.

### Recommendation tone mapping

Three independent scoring systems render recommendations. Keep these exact:

**4-tier (Market Picks, Consolidated card, Track Record)** — identical string in
`market-picks-dashboard.tsx`, `consolidated-card.tsx`, `app/market-picks/history/page.tsx`:

| Tier | Classes | Label rendered |
|---|---|---|
| BUY | `bg-buy/12 text-buy border-buy/25` | `BUY` |
| WATCHLIST | `bg-buy/8 text-buy/75 border-buy/15` | `WATCH` (in the picks table) |
| HOLD | `bg-hold/12 text-hold border-hold/25` | `HOLD` |
| SELL | `bg-sell/12 text-sell border-sell/25` | `SELL` |
| unknown | `bg-muted/10 text-muted border-muted/20` | as-is |

WATCHLIST is a *dimmer BUY*, not its own hue — it is deliberately the same green at lower
opacity, because it is a lower-conviction bullish tier. Do not give it `accent` or `hold`.

**3-tier (single-stock analysis hero)** — `REC_CONFIG` in `results-dashboard.tsx` carries four
class slots per verdict, used together on the hero: `bg` `bg-{tone}/10`, `border`
`border-{tone}/30`, `text` `text-{tone}`, `badge` `bg-{tone} text-bg`, `strip` `bg-{tone}`.

**Ink on a solid signal fill is `text-bg`, never `text-white`.** These fills are deliberately
bright; white on them measures 1.85:1 (`buy`) and 2.03:1 (`hold`) — failing even the 3:1 bar for
large text, on the most prominent element in the product. `text-bg` (`#0b1120`) measures
**10.17:1 on `buy`, 9.29:1 on `hold`, 5.08:1 on `sell`**. Applied to all three, not just the two
that failed, so the pill doesn't flip ink colour by outcome. Same rule applies anywhere else a
solid signal fill carries text — e.g. the completed-step circle in `/market-picks`' progress
stepper. The *tinted* variants (`text-{tone}` over a dark surface) are unaffected and pass:
`text-buy` 8.67:1, `text-hold` 7.92:1 on `card`.

**Verdict timeline** — `TIMELINE_REC_CLS` in `verdict-timeline.tsx` reuses the 4-tier
BUY/HOLD/SELL rows above (no WATCHLIST — single-stock analysis is 3-tier); unknown falls back to
`bg-card-hi text-muted border-border`.

Other semantic mappings: confidence `HIGH/MEDIUM/LOW → buy/hold/sell`; news sentiment
`Positive/Neutral/Negative → buy/muted/sell`; valuation `Undervalued/Overvalued/other →
buy/sell/hold`; confidence score `≥70/45–69/<45 → buy/hold/sell`; valuation percentile
`≤33/34–66/≥67 → buy/hold/sell`; exchange tag `BSE → hold`, `NSE → buy`.

---

## 3. Typography

Two Google fonts, loaded in `app/layout.tsx` via `next/font/google` (self-hosted at build time —
no runtime font request):

```ts
const inter = Inter({ subsets: ['latin'], variable: '--font-sans' });
const mono  = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono' });
```

Both variables are applied to `<html>`; `<body>` carries `className="font-sans"`. Tailwind maps
`font-sans → var(--font-sans), system-ui, sans-serif` and `font-mono → var(--font-mono), monospace`.
No weights are subset explicitly — the full variable range is available.

`globals.css` sets `-webkit-font-smoothing: antialiased`, `-moz-osx-font-smoothing: grayscale`,
and `text-rendering: optimizeLegibility` on `body`.

### Scale in use

Tailwind's default size scale plus arbitrary `text-[Npx]` values at the small end. No custom
`fontSize` config exists.

| Role | Classes | Where |
|---|---|---|
| Page H1 | `text-4xl font-black tracking-tight` | Home hero, Market Picks hero |
| Section / stat headline | `text-2xl font-black` | Stat tile values |
| Hero verdict badge | `text-3xl font-black px-8 py-2.5 rounded-xl` | Analysis hero BUY/HOLD/SELL |
| Card / entity heading | `text-xl font-bold` | Company name in hero |
| Sub-headline | `text-lg font-black` | Secondary stat values |
| Card title | `text-[11px] font-semibold text-muted tracking-[1px] uppercase` | The `Card` primitive — this, not `text-sm`, is the shipped card header |
| Body | `text-sm` (+ `leading-relaxed` for prose) | Summaries, thesis, metric rows |
| Nav link | `text-sm` | `SiteNav` |
| Table column header | `text-[10px] font-bold text-muted uppercase tracking-wider` | Every `<th>` |
| Meta / secondary | `text-xs text-muted` | Timestamps, sources, industry |
| Chip / small label | `text-[11px] font-semibold` | Filter chips, badges, tooltip body |
| Badge | `text-[10px] font-bold` | Recommendation badges |
| Micro badge | `text-[9px] font-bold` | Horizon, IPO, trend, timeline entries |
| Smallest | `text-[8px] font-bold uppercase tracking-wider` | "Bought" marker |

### Rules

- Financial figures use `font-mono`. Add `tabular-nums` wherever numbers stack in a column that
  must align (tables, confidence bars, portfolio rows) — it is not applied blanket-wide to
  `font-mono`, so add it deliberately.
- Ticker symbols always `font-mono`, usually uppercase with tracking (`tracking-wide` /
  `tracking-[2px]` on the large search input).
- Uppercase labels always pair with a tracking bump: `tracking-wider` (badges, `<th>`) or
  `tracking-[1px]` (card titles).
- Placeholders in mono/uppercase inputs must reset: `placeholder:font-sans placeholder:normal-case
  placeholder:tracking-normal` (see `header-search.tsx`, `ticker-search.tsx`).
- Never `font-light`. Never below `text-[8px]`.

---

## 4. Spacing & layout

### Page containers

There is **no single container class.** Each page picks a width for its content density; all use
`mx-auto px-4 pt-8 pb-16` unless noted.

| Width | Pages |
|---|---|
| `max-w-6xl` | `/screener` (widest table) |
| `max-w-5xl` | `/`, `/market-picks`, `/market-picks/history`, `/sme-signals`, `/watchlist` |
| `max-w-4xl` | `/portfolio`, `/portfolio-aggregator` (`px-4 py-8`) |
| `max-w-3xl` | `/api-keys`, `/pricing` |
| `max-w-2xl` | Home idle state (nested, `mx-auto pt-8`) |
| `max-w-6xl px-6 py-6` | Global disclaimer footer in `app/layout.tsx` |

Pick the narrowest width the content fits. `/compare` is the exception — it is unconstrained so
two full report columns can sit side by side (§9).

### Border radius

`rounded-xl` (74 uses) is the default for cards, panels, inputs, modals, and large buttons.
`rounded-lg` (63) for small buttons, chips-as-buttons, dropdowns, nested tiles, tooltips.
`rounded-full` (60) for pills, dots, rank circles, and all progress-bar tracks and fills.
`rounded-md` (20) for the recommendation badges. `rounded-2xl`/`rounded-sm`/`rounded-none` are unused.

### Vertical rhythm

- Page sections: `mb-5` / `mb-6` / `mb-8`; top-level dashboard stack is `space-y-5`.
- Card internals: `p-5` (the `Card` primitive), `px-4 py-4` (table cells), `px-3 py-2` /
  `px-3 py-2.5` (compact list rows), `px-5 py-4` (banners, modal header).
- Lists: `space-y-1.5` tight, `space-y-2` / `space-y-3` normal, `space-y-4` / `space-y-5` section-level.
- Divided lists use `divide-y divide-border` on the wrapper, not per-row bottom borders.
- Gaps: `gap-1.5`/`gap-2` inline, `gap-3` chip rows, `gap-4`/`gap-5` card grids.

### Grids

| Context | Pattern |
|---|---|
| Analysis main grid | `grid-cols-1 lg:grid-cols-5 gap-5` (thesis `lg:col-span-3`, sidebar `lg:col-span-2`) |
| Market Picks hero | `grid-cols-1 md:grid-cols-[1fr_320px] gap-10 md:gap-16 items-start` |
| Source cards | `grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3` |
| Stat tile rows | `grid-cols-2 md:grid-cols-5 gap-3` (SME), `grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3` (Track Record), `grid-cols-3 gap-3` (Market Picks) |
| Risk cards | `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3` |
| Paired data cards | `grid-cols-1 md:grid-cols-2 gap-4` |
| P/E · P/B · EPS tiles | `grid-cols-3 gap-2` |

---

## 5. Component patterns

### Shared primitives — `components/dashboard-primitives.tsx`

Use these; do not re-implement them.

**`Card`** — every dashboard panel:

```tsx
<div className="bg-card border border-border rounded-xl p-5">
  <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-3
                flex items-center gap-1.5">{title}</p>
  {children}
</div>
```

`title` is a `ReactNode`, so an `InfoTooltip` composes into the header (see the Quant Signals card).

**`MetricRow`** — label/value line inside a card. `py-1.5 border-b border-border last:border-0`;
label `text-sm text-muted`, value `text-sm font-semibold font-mono` with an optional
`colorClass` and an optional trailing `PercentileBadge`.

**`ExchangeTable`** — price block. One quote renders as a right-aligned `text-2xl font-bold
font-mono` price with a `text-sm font-mono` change line (`text-buy` / `text-sell` / `text-muted`
at exactly zero) and an exchange caption. Two or more render as a bordered stack with the
primary row tinted `bg-accent/5` and tagged `Primary`.

**`RangeBar`** — 52-week position. `h-1.5 rounded-full bg-border` track, a
`bg-gradient-to-r from-sell/25 via-hold/25 to-buy/25` overlay, and a
`w-2 h-2 rounded-full bg-tx border border-bg` marker positioned by percentage.

### Shared table atoms — `components/data-table-ui.tsx`

**`Skeleton`** — `bg-border/60 rounded animate-pulse` + a caller-supplied size class. This is the
loading placeholder for the app. **`FilterChip`** — `px-3 py-1.5 rounded-full text-[11px]
font-semibold border transition-colors`, active `bg-accent/15 border-accent/40 text-accent`,
inactive `bg-surface border-border text-muted hover:text-tx hover:border-border-hi`, and always
`aria-pressed`. **`SortableTh`** — a `<th>` carrying `aria-sort`, wrapping a full-padding
`<button>` with a `↑`/`↓`/`↕` indicator (`text-accent` when active, `text-muted/25
group-hover:text-muted/60` when not).

### Glass card

```css
.glass {                                   /* globals.css, @layer components */
  background: rgba(19, 32, 64, 0.65);      /* = card at 65% */
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.06);
}
```

Two call sites only: the Market Picks phase panel and the `ConsolidatedCard` modal. Glass needs
content behind it to blur — on a flat `bg-bg` region it renders as a slightly-off card with a
white hairline. Prefer `bg-card border border-border` unless something is genuinely layered behind.

### Badges

Recommendation badge (tones in §2):

```html
<span class="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold
             border tracking-wide bg-buy/12 text-buy border-buy/25">BUY</span>
```

Micro badge (horizon, IPO, trend, "Concentrated", timeline entry) —
`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold border`, e.g.
`bg-accent/15 text-accent border-accent/30`.

Percentile badge — `ml-1.5 text-[9px] font-semibold px-1.5 py-0.5 rounded-full bg-accent/10
text-accent border border-accent/20 whitespace-nowrap`, always with a `title` spelling out what
the percentile means.

Rank badge (`market-picks-dashboard.tsx`): #1 `w-6 h-6 rounded-full bg-accent text-[10px]
font-black text-white shadow-sm shadow-accent/30`; #2–3 same box with `bg-accent/20 text-accent`;
#4+ plain `text-xs font-semibold text-muted/50 tabular-nums pl-1`.

Watchlist star (`watchlist-button.tsx`) — a text glyph, not an icon: `★` `text-hold` when watched,
`☆` `text-muted/40 hover:text-hold` when not; `text-xl` (`md`) or `text-base` (`sm`); carries
`aria-pressed`, `aria-label`, and `title`, and calls `stopPropagation()` so it works inside a
clickable row.

### Buttons

There is no `<Button>` component. Four shipped shapes:

**Primary CTA** (`ticker-search.tsx`, the only one) — `rounded-xl font-semibold tracking-wide
bg-accent text-white hover:opacity-90 active:scale-[.98] transition-all duration-150
disabled:opacity-30 disabled:cursor-not-allowed disabled:shadow-none`, sized
`px-10 py-3.5 text-[15px]` or `px-7 py-2.5 text-sm` (compact). Its glow shadow currently uses a
stale raw hex — see §12; the intended value is `shadow-[0_4px_24px] shadow-accent/25`.

**Secondary / outline** — `px-3 py-1.5 rounded-lg text-xs font-semibold border
border-accent/30 text-accent hover:bg-accent/10 transition-colors`. Neutral variant swaps to
`border-border text-muted hover:text-tx hover:border-border-hi`.

**Ghost** — `text-xs text-muted hover:text-tx transition-colors` (also `hover:text-accent` for
nav-level actions). `hover:underline` for inline text links.

**Destructive** — `text-sell` with `hover:bg-sell/5` or `border-sell/40 hover:bg-sell/10`.

Rules: `transition-colors` (or `transition-all` where a transform is involved) on every
interactive element; `active:scale-95`/`active:scale-[.98]` on primary CTAs only; disabled state
is always opacity + `cursor-not-allowed`, never a color swap.

### Inputs

```html
<input class="w-full bg-card border border-border rounded-xl px-4 py-2 text-sm text-tx
              placeholder:text-muted/50 focus:outline-none focus:border-accent/40
              transition-colors">
```

Compact nav variant uses `bg-surface border-border rounded-lg px-3 py-1.5 text-xs` and
`focus:border-accent`. Two forms (`/login`, `/api-keys`) use `focus:ring-2 focus:ring-accent/50`
instead of a border change — prefer the ring; it is the more visible of the two and is the
closest thing this codebase has to a focus indicator (§10).

Search inputs: `pl-9 pr-8` with an absolutely-positioned SVG magnifier at
`left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted/60 pointer-events-none`, and a `✕` clear
button at `right-3` when non-empty. `<select>` reuses the input classes at `px-3 py-2 text-xs`.

### Tables

```html
<div class="rounded-xl border border-border overflow-hidden">
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border bg-surface sticky top-0 z-10">
          <th class="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">…</th>
        </tr>
      </thead>
      <tbody>
        <tr class="border-b border-border/60 hover:bg-surface/60 transition-colors">
          <td class="px-4 py-4">…</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
```

- Header cells `px-4 py-3`; body cells `px-4 py-4`. Keep row padding uniform — varying it per row
  breaks the scan rhythm of a dense table.
- Row borders are `border-border/60`, lighter than the container's `border-border`.
- Numeric cells `font-mono tabular-nums`, right-aligned, `text-buy`/`text-sell` by sign.
- Sortable headers use `SortableTh`; interactive cells inside a clickable row must
  `onClick={e => e.stopPropagation()}`.
- Expandable rows: `React.Fragment` wrapping the `<tr>` plus a detail `<tr>` spanning all columns.
  The open row swaps `hover:bg-surface/60` for `bg-card-hi`, and carries
  `role="button" tabIndex={0} aria-expanded` with an `Enter`/`Space` handler that ignores
  keydowns bubbled from nested controls (`if (e.target !== e.currentTarget) return`).

### Popovers, dropdowns, modals

**Popover** (`InfoTooltip`, `SourcesPopover`) — a full-viewport click-catcher plus an absolute
panel:

```html
<div class="fixed inset-0 z-10" onClick={close}></div>
<div role="dialog" aria-label="…" id={panelId}
     class="absolute top-full mt-2 z-20 w-64 bg-card border border-border rounded-xl
            shadow-2xl shadow-black/60 p-3">
```

The trigger carries `aria-expanded` + `aria-controls={panelId}` (`useId()`), and a document-level
`Escape` listener closes it. `InfoTooltip`'s trigger is a `w-3.5 h-3.5 rounded-full border
border-muted/40 text-muted/70 text-[9px] font-bold` circle containing `i`, labelled
`aria-label={`About ${title}`}`; the panel is a `text-[11px] font-bold text-tx` title over
`text-[11px] text-muted leading-relaxed space-y-1` body, with `align="left"` when a centered panel
would overflow.

**Dropdown menu** (`AuthWidget`, `SiteNav` mobile) — `absolute mt-2 w-44 rounded-lg bg-card border
border-border shadow-lg py-1 z-20`, `role="menu"` with `role="menuitem"` children; items are
`px-3 py-2 text-xs`. Closes on outside `mousedown` and on `Escape`, returning focus to the trigger.

**Modal** (`ConsolidatedCard`) — `fixed inset-0 z-40 flex items-start justify-center px-4 pt-24
sm:pt-32 bg-bg/80 backdrop-blur-sm` scrim (click-to-close), containing a `glass rounded-xl w-full
max-w-lg max-h-[75vh] overflow-y-auto` panel with `role="dialog" aria-modal="true"` and
`stopPropagation()`. Header is `sticky top-0 bg-card/90 backdrop-blur-sm`. Focus moves to the
close button on mount; `Escape` closes. Focus is **not** trapped — see §10.

### Progress, loading, and empty states

**Confidence bar** — `flex items-center gap-2.5 min-w-[108px]`, a `flex-1 h-1.5 rounded-full
bg-muted/15 overflow-hidden` track, a `h-full rounded-full transition-all duration-500` fill
colored `bg-buy` (≥70) / `bg-hold` (45–69) / `bg-sell` (<45), and a `text-[11px] font-bold
font-mono tabular-nums w-7 text-right` numeral in the matching text tone. Percentage bars inside
cards use the same shape on a `bg-border` track with a `bg-accent` fill.

**Skeleton** — `bg-border/60 rounded animate-pulse` (`Skeleton` in `data-table-ui.tsx`; the same
string is inlined on `/watchlist`, `/portfolio`, `/api-keys`, `/market-picks/history`). Modal
sections use `h-16 rounded-lg bg-border/40 animate-pulse`. Use this, not the shimmer gradient.

**Shimmer** — `bg-gradient-to-r from-border via-border-hi to-border bg-[length:200%_auto]
animate-shimmer`, one call site only (`ShimmerPill` on `/market-picks`, sized `h-8 w-24 rounded-lg
border border-border`), for placeholder source pills during a live scan. Not the general skeleton.

**Spinner** — a `⟳` glyph in a `animate-spin-slow` span, not an SVG or bordered circle.

**Pipeline step chip** (`progress-tracker.tsx`) — `flex items-center gap-2 px-3.5 py-2 rounded-lg
border text-[13px] font-medium transition-all duration-300` with an `aria-hidden` icon and a
per-status class set:

| Status | Classes | Icon |
|---|---|---|
| idle | `bg-card border-border text-muted` | `○` |
| running | `bg-accent/10 border-accent text-accent` | `⟳` + `animate-spin-slow` |
| ok | `bg-buy/10 border-buy/30 text-buy` | `✓` |
| fail | `bg-sell/10 border-sell/30 text-sell` | `✕` |
| cached | `bg-card border-border-hi text-muted` | `↺` |

Each chip carries `aria-label="{Task}: {status}"`; the wrapper is `role="status" aria-live="polite"`.

**Empty state** — a centered cell or panel, never a blank region:
`px-4 py-12 text-center` (inside a table, with `colSpan`) or `py-16 text-center` /
`rounded-xl border border-border bg-card px-6 py-16 text-center` (standalone), with
`text-sm text-muted` copy naming the reason, plus a recovery action where one exists
(`Clear filters` as a `text-xs text-accent hover:underline` button).

A section with genuinely no data renders **nothing** (`return null`) rather than an empty card —
`VerdictTimeline`, `PriceSparkline`, `SectorHeatmap`, `HorizonBadge`, and most add-on cards all do
this. Reserve the visible empty state for cases where the user's own action (a filter, an empty
watchlist) caused the emptiness.

### Banners

Error — `px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm`. Add
`flex items-start justify-between gap-4` with a trailing retry button when recovery is possible.

Warning / degraded — `px-5 py-3 rounded-xl bg-hold/10 border border-hold/30 text-sm flex
items-start gap-2` with an `aria-hidden` `⚠` in `text-hold shrink-0`, a `font-semibold text-hold`
lead-in, and the explanation in `text-tx`. This is the **degraded analysis** banner at the top of
`ResultsDashboard` when `report.degraded` is true (every LLM provider failed and the verdict is a
safe HOLD fallback). Body text stays `text-tx` so the message is readable — do not tint an entire
paragraph `text-hold`.

Inline note — `text-[11px] text-hold bg-hold/8 border border-hold/20 rounded-lg px-3 py-2
leading-relaxed` (used where the analysis and picks verdicts disagree).

Form-level error — `role="alert"` on a `text-sell text-xs` paragraph.

---

## 6. Navigation

`components/site-nav.tsx` is the only nav; every page renders `<SiteNav active={...} />`. Never
hand-copy this markup.

Container: `relative flex items-center gap-4 mb-8 pb-4 border-b border-border`, plus `flex-wrap`
when the `wrap` prop is set (pages with a `right` slot).

- **Logo** — `<Link href="/">` with `text-base font-black tracking-tight text-tx`, rendered as
  `Alpha<span className="text-accent">Pulse</span>`. Same lockup at `text-4xl` in the home hero.
- **Links** (`md:` and up) — a pipe-separated list inside `hidden md:contents`; separator is
  `<span className="text-border-hi">|</span>`. Inactive: `text-sm text-muted hover:text-tx
  transition-colors whitespace-nowrap`. Active: `text-sm font-semibold text-accent`, rendered as a
  `<span>`, not a link. Link set is the `LINKS` array — Market Picks, SME Signals, Screener, Track
  Record, Watchlist, Portfolio, Net Worth, Compare, API Keys.
- **Mobile** (below `md`) — the list collapses behind a `md:hidden w-8 h-8 rounded-lg border
  border-border` `☰`/`✕` toggle carrying `aria-haspopup`, `aria-expanded`, `aria-controls`,
  `aria-label`. The panel is `absolute left-0 top-full mt-1 w-56 rounded-lg bg-card border
  border-border shadow-lg py-1 z-20` with `role="menu"`; it closes on outside `mousedown` and on
  `Escape` (returning focus to the toggle).
- **Right cluster** — `ml-auto flex items-center gap-3` holding `HeaderSearch`, `AuthWidget`, and
  the page's optional `right` slot. Search and account stay visible at every width.
- **Alert dot** — `w-1.5 h-1.5 rounded-full bg-accent shrink-0` beside the Watchlist link,
  with both `aria-label` and `title`.
- **`extraLabel`** — for a page outside `LINKS`; renders as a trailing accent label.

---

## 7. Data visualization

No chart library. Everything is hand-written SVG or CSS. Do not add one without changing this
section first.

**`Sparkline`** (`sparkline.tsx`) — `<polyline>` with `stroke="currentColor"` at `strokeWidth
1.5`, `strokeLinecap`/`strokeLinejoin` round; the wrapper `<svg>` carries `text-buy` when the last
close ≥ the first, `text-sell` otherwise. `role="img"` with a descriptive `aria-label`. Default
96×28, `preserveAspectRatio="none"`. Returns `null` under 2 points. Passing a `dates` array of
equal length enables `cursor-crosshair` hover: a `strokeWidth 0.75 opacity 0.35` crosshair line, a
`r=2.5` dot, and an edge-clamped tooltip (`rounded-md border border-border bg-card px-2 py-1
text-[10px] font-mono text-tx shadow-lg shadow-black/40`).

**`EmaChart`** (`ema-chart.tsx`) — 640×220 default, `pad = 10`, three polylines: close
`text-muted/50` at `strokeWidth 1.25`, EMA50 `text-hold` and EMA20 `text-accent` at `1.75`. Cross
days get an `r=4` circle, `fill-buy` (golden) or `fill-sell` (death). Same hover crosshair and
tooltip contract as `Sparkline`. A legend row (`text-[10px] text-muted`) sits below with
`w-2.5 h-0.5` line swatches and `w-2 h-2 rounded-full` dot swatches. Under 2 usable points it
renders `text-xs text-muted py-8 text-center` copy instead of an empty chart.

**Bar / gauge** — `h-1.5 rounded-full` on a `bg-border` or `bg-muted/15` track with a
`rounded-full` signal-toned fill; `transition-all duration-500` when the value animates in.

**Heatmap** (`sector-heatmap.tsx`) — a four-step opacity ramp per direction (§2). Magnitude, not
just direction, drives the step; a genuinely balanced sector stays `hold`-toned rather than being
arbitrarily colored.

**Rules** — every chart is `role="img"` with an `aria-label` describing what it shows; color
alone never carries the meaning (the sparkline direction is in its label, the EMA legend names
each line, cross type is in the tooltip text); a series with too few points renders copy or
nothing, never an empty axis.

---

## 8. Animation & motion

Three custom animations in `tailwind.config.ts`, plus Tailwind's built-ins:

| Class | Definition | Use |
|---|---|---|
| `animate-fade-up` | `fadeUp 0.4s ease both` — opacity 0→1, `translateY(14px)→0` | Entering a dashboard / results section. Outermost wrapper only. |
| `animate-shimmer` | `shimmer 1.8s linear infinite` — `background-position -200% → 200%` | The one `ShimmerPill` (§5). Requires a gradient with `bg-[length:200%_auto]`. |
| `animate-spin-slow` | `spin 0.8s linear infinite` | Inline `⟳` spinners. |
| `animate-pulse` | Tailwind default | Skeletons, the live-LTP dot, the active pipeline batch cell. |

`animate-spin`, `animate-bounce`, and `animate-ping` are unused.

**Reduced motion is handled globally.** `globals.css` has a `@media (prefers-reduced-motion:
reduce)` block that forces `animation-duration: 0.01ms`, `animation-iteration-count: 1`, and
`transition-duration: 0.01ms` on `*, *::before, *::after`, and disables `scroll-behavior: smooth`.
Individual components do **not** need their own `motion-reduce:` variants.

**Rules**

- `transition-colors` on every hover/active state; `transition-all` only where a transform or
  shadow also changes.
- Standard durations: `duration-150` (buttons), `duration-200` (inputs), `duration-300` (status
  chips), `duration-500` (data bars filling).
- Animate `opacity`, `transform`, `color`, `border-color`, `background-color`, and
  `background-position`. Do not animate `width`/`height`/`top`/`left` — the one width transition
  that exists (the confidence bar) is a deliberate exception on a 1.5px-tall element.
- Don't nest `animate-fade-up` inside another faded wrapper.

---

## 9. Responsive strategy

Mobile-first: unprefixed classes are the phone layout and every prefix is an enhancement. This is
an India-focused product; assume a phone.

**Breakpoints in use** — `sm:` 640px, `md:` 768px, `lg:` 1024px, `2xl:` 1536px. `xl:` (1280px) is
not used anywhere. Tailwind defaults; no custom `screens` config.

| Breakpoint | What changes |
|---|---|
| `sm:` | Filter rows go row-wise (`flex-col sm:flex-row`); 2→3-column stat and risk grids; modal top offset. |
| `md:` | **Nav switches from hamburger to the full link list.** Two-column heroes split; source grids go 2→3 columns; paired data cards split. |
| `lg:` | Analysis grid becomes 3+2 columns; source grids reach 5; risk cards reach 3. |
| `2xl:` | `/compare` stacks two reports side by side (`flex-col 2xl:flex-row`). |

`/compare` is capped at 2 symbols on purpose: `ResultsDashboard`'s internal grids use *viewport*
breakpoints, not container queries (no container-query plugin is installed), so a narrower column
would compress rather than reflow. 1536px is the point at which each column is still wide enough
for `lg:` to be honest.

**Rules**

- Wide content scrolls, it never gets truncated: `overflow-hidden` on the rounded wrapper,
  `overflow-x-auto` on the inner div. Never hide columns on mobile.
- Overflowing flex text needs `truncate min-w-0`; the badges beside it need `shrink-0`.
- Chip rows use `flex-wrap gap-2`.
- The nav's `wrap` prop exists for pages with extra `right`-slot controls — use it rather than
  shrinking the controls.

---

## 10. Accessibility

### Implemented

- **Toggles** — `aria-pressed` on every filter chip, confidence/horizon/cap chip, and the
  watchlist star.
- **Disclosure** — `aria-expanded` + `aria-controls={useId()}` on every popover, dropdown, and
  nav toggle; `aria-expanded` on expandable table rows.
- **Roles** — `role="dialog"` (+ `aria-modal` on the true modal) on panels, `role="menu"` /
  `role="menuitem"` on dropdowns, `role="button" tabIndex={0}` with `Enter`/`Space` handling on
  clickable rows, `role="img"` + `aria-label` on charts, `role="alert"` on form errors,
  `role="status" aria-live="polite"` on the pipeline tracker.
- **Sorting** — `aria-sort="ascending" | "descending" | "none"` on sortable `<th>`s, with the
  control itself a real `<button>`.
- **Escape** — every popover, dropdown, and modal closes on `Escape`; `AuthWidget` and `SiteNav`
  return focus to their trigger, `ConsolidatedCard` focuses its close button on mount.
- **Labels** — `sr-only` `<label>` on the nav search; `aria-label` on the ticker input, watchlist
  star, popover triggers, close buttons, and the alert dot.
- **Live regions** — `aria-live="polite"` on the pipeline tracker and on the ticker input's
  `sr-only` validation-status text (the visible indicator is an icon only).
- **Decorative glyphs** — `aria-hidden="true"` on `⚠`, `⟳`, `☰`, and status icons whose meaning is
  already in an adjacent label.
- **Motion** — the global `prefers-reduced-motion` block in §8.
- **Redundant encoding** — signal color is always paired with a glyph or word (`▲`/`▼`, `↑`/`↓`,
  `✓`/`✗`, `+`/`−`, the verdict text itself), so red/green is never the sole carrier of meaning.

### Contrast, measured

WCAG 2.1 contrast ratios of the current tokens (AA needs 4.5:1 for normal text, 3:1 for text
≥18.66px bold or ≥24px):

| Foreground | on `bg` | on `surface` | on `card` | on `card-hi` |
|---|---|---|---|---|
| `tx` | 15.32 | 14.44 | 13.06 | 11.85 |
| `buy` | 10.17 | 9.58 | 8.67 | 7.87 |
| `hold` | 9.29 | 8.76 | 7.92 | 7.19 |
| `accent` | 5.20 | 4.90 | **4.43** | **4.02** |
| `sell` | 5.08 | 4.79 | **4.33** | **3.93** |
| `muted` | 6.12 | 5.77 | 5.22 | 4.74 |

Bold = below 4.5:1. State of play:

1. **`muted` on `card` now passes** (5.22:1), after the token was raised from `#6b7fa8` (3.99:1)
   to `#8093bd`. Reduced-opacity variants (`text-muted/60` and below) remain below the line — the
   base token passes, the faded ones don't.
2. **Solid signal badges now use `text-bg`, not `text-white`** — 10.17:1 on `buy`, 9.29:1 on
   `hold`, 5.08:1 on `sell`, versus 1.85 / 2.03 / 3.70 with white. Covers the `REC_CONFIG` hero
   badge and the `/market-picks` completed-step circle.
3. **Still open:** `text-white` on `bg-accent` is **3.62:1** — the primary CTA (`text-[15px]
   font-semibold`, so the 4.5:1 small-text bar applies) and the active progress-step circle.
   Left as-is deliberately: white-on-blue is the conventional button treatment and 3.62 is
   marginal rather than illegible, unlike the 1.85 it sits next to. Fixing it means either
   darkening `accent` or switching the CTA to dark ink — a brand call, not a mechanical one.
4. **Still open:** `sell` (4.33:1) and `accent` (4.43:1) as *text* on `card` sit just under AA.

**Rule for new work:** on a solid signal fill use `text-bg`; otherwise prefer the tinted pattern
(`bg-{tone}/12 text-{tone} border-{tone}/25`). Do not introduce new `text-muted` on `card-hi`, and
do not go below `text-muted/60` for anything a user needs to read.

### Focus indicator

One global rule in `globals.css` covers every interactive element — buttons, links, inputs, chips,
clickable table rows — so no component opts in individually:

```css
:focus-visible {
  outline: 2px solid #4d7fff;
  outline-offset: 2px;
  border-radius: 2px;
}
```

`:focus-visible`, not `:focus`, so a mouse click doesn't leave a ring behind while keyboard and
programmatic focus still get one. **Components must not re-declare their own focus outline.**

Known exception: five inputs (`/login`, `/api-keys`, `/market-picks/history`, and two in
`market-picks-dashboard.tsx`) use `focus:outline-none` paired with `focus:ring-2 focus:ring-accent/50`
or `focus:border-accent/40`. Those out-specify the global rule — `.focus\:outline-none:focus` is
(0,2,0) against the rule's (0,1,0) — so they keep their own designed treatment rather than the
ring. They are not blind, but they are a second, inconsistent focus style and they fire on mouse
click too. Consolidating them onto the global rule is open work.

### NOT IMPLEMENTED

Do not cite these as existing patterns; they are open gaps.
- **No focus trap** in the modal or dropdowns — Tab can leave an open `ConsolidatedCard`.
- **No skip-to-content link.**
- **No landmark elements** — pages are `<div>` trees; `<main>`/`<nav>`/`<header>` are unused.
- **Heading order is not audited** — several pages jump levels.
- **Touch targets** — the `InfoTooltip` trigger is 14×14px and the `sm` watchlist star ~16px,
  both well under the 44×44px guideline.

---

## 11. Do / Don't

| Do | Don't |
|---|---|
| Use the 12 tokens for every color in `app/` and `components/` | Hard-code a hex outside the four exception files in §2 |
| Reuse `Card`, `MetricRow`, `Skeleton`, `FilterChip`, `SortableTh`, `InfoTooltip`, `SiteNav` | Re-implement a primitive inline because the props don't quite fit |
| Express depth with token + opacity | Invent a new color, or add a Tailwind color scale |
| Use `hold` for warning/caution states | Add a `warning` token, or reuse `accent` for a data state |
| Keep `WATCHLIST` as dimmed `buy` | Give WATCHLIST its own hue |
| `font-mono` + explicit `tabular-nums` on aligned numbers | Mix sans and mono within one numeric column |
| Keep `px-4 py-4` on body cells and `px-4 py-3` on headers | Vary row padding per row |
| `return null` for an absent add-on section | Render an empty card with a placeholder value |
| Give an empty state a reason and a recovery action | Leave a blank region |
| Pair every signal color with a glyph or word | Rely on red/green alone |
| Add `aria-pressed` / `aria-expanded` / `aria-sort` to new controls | Ship a `<div onClick>` |
| Let the global `prefers-reduced-motion` block handle motion | Add per-component `motion-reduce:` variants |
| Use `bg-card border border-border` by default | Use `glass` where nothing is layered behind it |
| `truncate min-w-0` on flex text, `shrink-0` on its badges | Let text overflow, or let a badge collapse |
| Add new SSE/report fields to `frontend/types/index.ts` first | Inline ad-hoc types in a component |
| Run `npx tsc --noEmit` (and `npm run build` after CSS changes) before finishing | Ship without type-checking |

---

## 12. Known drift

Real inconsistencies in the shipped code, listed so they aren't mistaken for precedent. This
document describes the intended rule; unless stated otherwise the code has not been changed to
match.

1. ~~**Stale raw hex in the primary CTA.**~~ **Fixed.** `ticker-search.tsx` hardcoded
   `#6c71f040`/`#6c71f060` — the *previous* accent, retired in favour of `#4d7fff` — so the app's
   one primary CTA glowed in a dead brand colour. Now `shadow-accent` / `hover:shadow-accent-lg`,
   tokenised in `tailwind.config.ts` under `boxShadow` (a Tailwind arbitrary value can't read
   `theme.colors.accent`, which is exactly how the original rotted unnoticed).
2. ~~**No global focus indicator.**~~ **Fixed.** See §10 "Focus indicator". The five
   `focus:outline-none` inputs that out-specify the global rule remain a known exception.
3. ~~**`muted` fails AA on `card`; white-on-`buy`/`hold` badges fail the large-text bar.**~~
   **Fixed.** `muted` raised `#6b7fa8` → `#8093bd` (3.99 → 5.22 on `card`); solid signal badges
   moved from `text-white` to `text-bg`. Still open: `text-white` on `bg-accent` at 3.62:1, and
   the reduced-opacity `muted` variants. See §10.
4. **Page container width is not standardised** (§4) — five distinct `max-w-*` values across
   twelve pages. Reasonable per page, but there is no stated rule beyond "narrowest that fits".
5. **Two focus treatments on inputs** — `focus:border-accent/40` (most) vs. `focus:ring-2
   focus:ring-accent/50` (`/login`, `/api-keys`). Both now also sit alongside the global
   `:focus-visible` rule and out-specify it. Consolidate onto the global rule.
6. **`Skeleton` is duplicated**, not imported. `data-table-ui.tsx` exports it, but
   `app/watchlist/page.tsx` and `app/market-picks/history/page.tsx` each define a byte-identical
   local copy, and `/portfolio` + `/api-keys` inline the class string.
7. **No `warning` token.** `hold` is doing double duty as both "neutral verdict" and "caution",
   which two components call out in their own comments (`ConcentrationBadge`, the SME illiquid
   badge, which reach for `accent` and `hold` respectively for the same intent).
8. **`§`-numbered references in code.** `sparkline.tsx` cites "design.md §7" (Data Visualization) —
   still accurate. `consolidated-card.tsx`, `ema-chart.tsx`, `info-tooltip.tsx`, and
   `results-dashboard.tsx` reference this document by name without a section number. If §7 is ever
   renumbered, update `sparkline.tsx`.
