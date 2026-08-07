# AlphaPulse Design System

Single source of truth for the shipped UI. Every claim below is verified against
`frontend/tailwind.config.ts`, `frontend/app/globals.css`, `frontend/app/layout.tsx`, and the
components in `frontend/components/`. Paths are relative to the repo root.

**The rule this document exists to make enforceable** (also stated in `frontend/CLAUDE.md` and the
root `CLAUDE.md`): use the tokens in `tailwind.config.ts`; do not hard-code hex values; do not
invent new patterns.

Sections marked **NOT IMPLEMENTED** describe a gap, not a shipped pattern — do not cite them as
precedent. §12 lists known drift between this document and the code.

**Revision 2** — this document now covers elevation (§13), shadows (§14), form controls (§15),
number/currency/date formatting (§16), data states (§17), and UI copy (§18); §19 defines
enforcement and §20 ownership. Rules carry stable IDs; the four decisions the previous revision
left open are recorded in §20.

---

## 0. How to read this

- **MUST / MUST NOT** — blocking. A reviewer rejects the PR and cites the rule ID.
- **SHOULD** — default. Deviating requires a one-line reason in the PR body.
- **MAY** — genuinely optional.

Every rule carries a stable ID (`COLOR-03`). IDs never change and are never reused. Code comments
and PR reviews cite the ID, never a section number — a section number is what let `sparkline.tsx`'s
"design.md §7" become a maintenance hazard (**SRC-04**).

**META-01** — When no rule covers the case, the PR proposes one — a diff to this document in the
same PR — rather than inferring from the nearest existing pattern. Adding a runtime dependency
(chart library, component library, CSS-in-JS) always requires an amendment first.

*This replaces the earlier escape hatch, "anything not covered here follows the nearest existing
pattern". Every drift item in §12 entered the codebase as a reasonable local inference.*

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

**Stack constraints that shape the patterns below:** Tailwind CSS v3, no ESLint, no Prettier, no
CSS-in-JS, no component library, no chart library. `tsconfig` strict mode plus the Playwright E2E
suite are the automated gates today; §19 proposes the rest.

`plugins: []` held until Revision 2. It now holds exactly one entry —
`@tailwindcss/container-queries`, admitted under **PAGE-04** so `/compare` can exceed two symbols.
A second plugin requires an amendment (META-01).

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

### Signal

| Token | Hex | Semantic |
|---|---|---|
| `buy` | `#10d98e` | Gains, BUY, bullish, golden cross, win, upgrade, positive delta. |
| `sell` | `#e46b7b` | Losses, SELL, bearish, death cross, loss, downgrade, risk, destructive action. |
| `hold` | `#f5a623` | Neutral/HOLD, and every caution state: degraded-analysis banner, watchlist star, illiquid badge, BSE exchange tag, EMA50 line, short horizon. |

### Accent

| Token | Hex | Use |
|---|---|---|
| `accent` | `#618eff` | Interactive affordances only: links, primary CTA fill, active nav item, active sort arrow, focus border, rank #1/#2–3 badges, EMA20 line, selection highlight (`::selection` at `#618eff40`). |

### Color rules

- **COLOR-01** — **No `warning` token is added.** `hold` is the caution token as well as the
  neutral verdict. Two components disagreed with each other about this; both MUST use `hold`
  (`ConcentrationBadge` drops `accent`; the SME illiquid badge is already correct). A 13th token
  buys one shade of nuance and costs a second contrast audit, a second chart stroke, and a
  permanent ambiguity at every call site. The double duty is legible because the two meanings never
  appear in the same component. *(Closes §12.7.)*
- **COLOR-02** — `accent` marks interaction, never data. If something is colored `accent`, a user
  MUST be able to click it — the one exception is the EMA20 chart line, which is legend-labelled.
  Where an "attention, but not good/bad" tone is needed on data, `hold` is used
  (`results-dashboard.tsx` marks elevated volume `text-hold`, with a comment saying exactly this).
- **COLOR-03** — Tinted-surface opacity comes from the ladder only: fill `/5 /8 /10 /12 /15 /20
  /30`, border `/15 /20 /25 /30 /40`. Off-ladder values (`/7`, `/35`) are rejected — arbitrary
  steps are invisible individually and incoherent in aggregate. `/15` fill is the active-state tint
  on chips, badges, and step circles (`FilterChip`, `SourcesPopover`'s type badge, the Market Picks
  progress stepper) — common enough to be its own rung, not an off-ladder value. Two categories
  outside the ladder's scope entirely, not exceptions to it: a `hover:` opacity that darkens/lightens
  an already-solid fill on interaction (a state transition, not a base tint), and a thin progress/
  connector line's own fill (`h-px` stepper connectors, chart strokes) — governed by §7's
  signal-toned-fill convention, not this one.
- **COLOR-04** — `sell` and `accent` were lightened in Revision 2 so every token clears AA on every
  surface. Hue and saturation held exactly; only HSL lightness moved, by 5 and 4 points.

  | Token | Was → is | on `bg` | on `card` | on `card-hi` |
  |---|---|---|---|---|
  | `sell` | `#e05568` → `#e46b7b` | 5.08 → 6.00 | 4.33 → 5.11 | 3.93 → 4.64 |
  | `accent` | `#4d7fff` → `#618eff` | 5.20 → 6.12 | 4.43 → 5.21 | 4.02 → 4.73 |

  Solid fills improved too — `text-bg` on `sell` 5.08 → 6.00, on `accent` 5.20 → 6.12. `sell` stays
  clear of `hold` in hue and keeps its glyph pairing; red-green separation was never carried by the
  color alone. The raw-hex exception files below carry `accent` literally and MUST be updated in
  the same PR as the token — exactly the failure mode of the retired `#6c71f0`.
- **COLOR-05** — Hard floor on faded text: `text-muted/60`. Below that, `muted` opacity is for
  *non-text* only — hairlines, inactive glyph strokes, chart gridlines. `/50`, `/45`, `/40`, `/30`,
  `/25` on text are rejected in review, including on placeholders. Anything a user must read is
  full `muted`.
- **COLOR-06** — **Ink on any solid tone fill is `text-bg`, never `text-white` — no exceptions.**
  These fills are deliberately bright; white on them measures 1.85:1 (`buy`), 2.03:1 (`hold`),
  3.70:1 (old `sell`) and 3.62:1 (old `accent`) — the first two failing even the 3:1 bar for large
  text, on the most prominent element in the product. `text-bg` (`#0b1120`) measures **10.17:1 on
  `buy`, 9.29:1 on `hold`, 6.00:1 on `sell`, 6.12:1 on `accent`**. Applied uniformly so a pill
  doesn't flip ink colour by outcome. Covers every solid fill carrying text: the hero verdict
  badge, the `#1` rank badge, the primary CTA, `/compare`'s submit, and both the done and active
  circles in `/market-picks`' progress stepper.

The *tinted* variants (`text-{tone}` over a dark surface) are a different case:
`text-buy` 8.67:1, `text-hold` 7.92:1, `text-sell` 5.11:1, `text-accent` 5.21:1 on `card`.

### Legitimate raw-hex exceptions

Token classes cannot reach these, so the hex is duplicated deliberately. Keep them in sync with
the tables above (**SRC-03**); each file carries a comment pointing back here:

- `globals.css` — `body`, `::selection`, `:focus-visible`, `::-webkit-scrollbar-*`.
- `app/layout.tsx` `viewport.themeColor`, `app/manifest.ts` — browser-chrome metadata.
- `app/icon.tsx`, `app/apple-icon.tsx`, `app/manifest-icons/[size]/route.tsx` — `ImageResponse`
  (satori) renders inline styles, not Tailwind classes.

Everything else in `app/` and `components/` must use tokens.

### Opacity ladder

Tinted signal surfaces are built from token + opacity, never a new color:

| Layer | Range | Typical |
|---|---|---|
| Background fill | `/5`–`/20` | `/10` or `/12` |
| Border | `/15`–`/40` | `/25` or `/30` |
| Text | full, or `/70`–`/80` to de-emphasise a tier | full |

Depth is expressed by *raising* opacity, not by switching colors — `sector-heatmap.tsx` ramps a
single tone across four steps: `buy/5 → buy/12 → buy/20 → buy/30` with borders `/20 → /25 → /30 → /40`.

### Recommendation tone mapping

Three independent scoring systems render recommendations. These strings live in **one** module
(`lib/tone.ts`, **SRC-01**) and are imported, never re-declared.

**4-tier (Market Picks, Consolidated card, Track Record):**

| Tier | Classes | Label rendered |
|---|---|---|
| BUY | `bg-buy/12 text-buy border-buy/25` | `BUY` |
| WATCHLIST | `bg-buy/8 text-buy/75 border-buy/15` | `WATCH` (in the picks table) |
| HOLD | `bg-hold/12 text-hold border-hold/25` | `HOLD` |
| SELL | `bg-sell/12 text-sell border-sell/25` | `SELL` |
| unknown | `bg-muted/10 text-muted border-muted/20` | as-is |

WATCHLIST is a *dimmer BUY*, not its own hue — deliberately the same green at lower opacity,
because it is a lower-conviction bullish tier. Do not give it `accent` or `hold`.

**3-tier (single-stock analysis hero)** — `REC_CONFIG` in `results-dashboard.tsx` carries four
class slots per verdict, used together on the hero: `bg` `bg-{tone}/10`, `border`
`border-{tone}/30`, `text` `text-{tone}`, `badge` `bg-{tone} text-bg`, `strip` `bg-{tone}`.

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
  `font-mono`, so add it deliberately (**NUM-01**).
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

Three tiers, keyed to content type. *(Revision 2 — replaces "pick the narrowest width the content
fits", which produced five widths across twelve pages and could not be enforced. Closes §12.4.)*

| Tier | Content type | Pages |
|---|---|---|
| `max-w-6xl` | Dense multi-column tables | `/screener`; the global disclaimer footer (`px-6 py-6`) |
| `max-w-5xl` | Dashboards, lists, everything with cards | `/`, `/market-picks` (+`/history`), `/sme-signals`, `/watchlist`, `/portfolio`, `/portfolio-aggregator` |
| `max-w-3xl` | Forms, settings, reading-length prose | `/api-keys`, `/pricing`, `/login` |

- **PAGE-01** — A page MUST use one of the three widths with `mx-auto px-4 pt-8 pb-16`.
  `max-w-4xl` and `max-w-2xl` are retired *as page containers*; the home idle block keeps
  `max-w-2xl` as a nested measure constraint, which is a different thing and stays legal.
  `/compare` is the one exception, at a wider fourth `PageShell` tier (`max-w-[1600px]`, see
  **PAGE-04**) rather than one of the three above — it renders two full side-by-side
  `ResultsDashboard`s and needs the extra room the standard tiers don't give it.
- **PAGE-02** — Every page MUST render the same shell: skip link → `<SiteNav>` inside `<header>` →
  exactly one `<main id="main">` → the global `<footer>` disclaimer. This lives in a `PageShell`
  component so it cannot be got wrong; it delivers A11Y-12 and A11Y-13 for free.
- **PAGE-03** — One `<h1>` per page, headings descend without skipping, and a card title is *not* a
  heading unless it introduces a landmark region.
- **PAGE-04** — `/compare` is **not** capped at two symbols. Before the cap is raised,
  `ResultsDashboard`'s internal grids MUST move from viewport breakpoints to container queries, so
  a column reflows on its own width rather than the window's. Raising the cap without that produces
  compressed columns, not a wider comparison. Once converted, N columns scroll horizontally at a
  fixed minimum column width; they do not shrink below it.

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

Use these; do not re-implement them (**SRC-02**).

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
primary row tinted `bg-accent/5` and tagged `Primary`. Every quote carries its as-of time
(**NUM-04**).

**`RangeBar`** — 52-week position. `h-1.5 rounded-full bg-border` track, a
`bg-gradient-to-r from-sell/25 via-hold/25 to-buy/25` overlay, and a
`w-2 h-2 rounded-full bg-tx border border-bg` marker positioned by percentage.

### Shared table atoms — `components/data-table-ui.tsx`

**`Skeleton`** — `bg-border/60 rounded animate-pulse` + a caller-supplied size class. This is the
loading placeholder for the app, imported from here and nowhere re-declared (**SRC-02**, closes
§12.6). **`FilterChip`** — `px-3 py-1.5 rounded-full text-[11px] font-semibold border
transition-colors`, active `bg-accent/15 border-accent/40 text-accent`, inactive `bg-surface
border-border text-muted hover:text-tx hover:border-border-hi`, and always `aria-pressed`.
**`SortableTh`** — a `<th>` carrying `aria-sort`, wrapping a full-padding `<button>` with a
`↑`/`↓`/`↕` indicator (`text-accent` when active, `text-muted/60` when not).

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
white hairline. Prefer `bg-card border border-border` unless something is genuinely layered behind;
a third call site requires an amendment.

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
font-black text-bg shadow-sm shadow-accent/30`; #2–3 same box with `bg-accent/20 text-accent`;
#4+ plain `text-xs font-semibold text-muted/60 tabular-nums pl-1`.

Watchlist star (`watchlist-button.tsx`) — a text glyph, not an icon: `★` `text-hold` when watched,
`☆` `text-muted/60 hover:text-hold` when not; `text-xl` (`md`) or `text-base` (`sm`); carries
`aria-pressed`, `aria-label`, and `title`, calls `stopPropagation()` so it works inside a
clickable row, and pads out to a 44px target (**A11Y-14**).

### Icons

Two systems only, deliberately — no third:

- **Text/glyph** for status and signal affordances: `✓`/`✕`/`⟳`/`○`/`↺` (pipeline/status), `▲`/`▼`
  (bull/bear case), `↑`/`↓` (EMA trend, golden/death cross, price direction), `★`/`☆` (watchlist).
  This set is closed; a new glyph requires an amendment.
- **Hand-written SVG** only for the two cases that need precise stroke control: the search
  magnifier and the table-row expand chevron.

No emoji. A prior pass (`📈⚡💀🔥📰🏦📊🔔🏛️💼✏️`, across the home hero, SME Signals cross/volume
badges, the Market Picks source-type icons, the watchlist alert badges, and Net Worth's
account/asset-type icons) was removed for this reason — three unreconciled icon vocabularies
across pages was one of the largest "doesn't read as one product" findings in a design audit. Where
an emoji was the only visual distinguisher on a badge with no text (SME's cross/volume badges),
it was replaced with the existing `↑`/`↓` glyph already used for the same bull/bear meaning
elsewhere. Where a type-distinguishing icon had no existing glyph equivalent (source/account/asset
type), it was dropped — the adjacent text label already carries the information, and inventing a
fourth vocabulary to replace the third would repeat the mistake.

### Buttons

There is no `<Button>` component. Four shipped shapes:

**Primary CTA** (`ticker-search.tsx`, the only one) — `rounded-xl font-semibold tracking-wide
bg-accent text-bg hover:opacity-90 active:scale-[.98] transition-all duration-150
disabled:opacity-30 disabled:cursor-not-allowed disabled:shadow-none shadow-accent-glow
hover:shadow-accent-glow-lg`, sized `px-10 py-3.5 text-[15px]` or `px-7 py-2.5 text-sm` (compact). The
glow is tokenised under `boxShadow` — a Tailwind arbitrary value can't read `theme.colors.accent`,
which is exactly how the original rotted unnoticed (§12.1).

**Secondary / outline** — `px-3 py-1.5 rounded-lg text-xs font-semibold border
border-accent/30 text-accent hover:bg-accent/10 transition-colors`. Neutral variant swaps to
`border-border text-muted hover:text-tx hover:border-border-hi`.

**Ghost** — `text-xs text-muted hover:text-tx transition-colors` (also `hover:text-accent` for
nav-level actions). `hover:underline` for inline text links.

**Destructive** — `text-sell` with `hover:bg-sell/5` or `border-sell/40 hover:bg-sell/10`.

Rules: `transition-colors` (or `transition-all` where a transform is involved) on every
interactive element; `active:scale-95`/`active:scale-[.98]` on primary CTAs only; disabled state
is always opacity + `cursor-not-allowed`, never a color swap. A submitting button shows progress in
place (**FORM-06**).

### Inputs

```html
<input class="w-full bg-card border border-border rounded-xl px-4 py-2 text-sm text-tx
              placeholder:text-muted/60 focus:border-accent/40 transition-colors">
```

Compact nav variant uses `bg-surface border-border rounded-lg px-3 py-1.5 text-xs`. `focus:outline-none`
is not used anywhere — every control inherits the global `:focus-visible` ring (§10, **A11Y-15**);
`focus:border-accent/40` is decorative and MAY stay alongside it.

Search inputs: `pl-9 pr-8` with an absolutely-positioned SVG magnifier at
`left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted/60 pointer-events-none`, and a `✕` clear
button at `right-3` when non-empty. `<select>` reuses the input classes at `px-3 py-2 text-xs`.
Everything else — labels, checkboxes, radios, textareas, validation — is §15.

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
- Numeric cells `font-mono tabular-nums`, right-aligned, `text-buy`/`text-sell` by sign (§16).
- Sortable headers use `SortableTh`; interactive cells inside a clickable row must
  `onClick={e => e.stopPropagation()}`.
- Expandable rows: `React.Fragment` wrapping the `<tr>` plus a detail `<tr>` spanning all columns.
  The open row swaps `hover:bg-surface/60` for `bg-card-hi`, and carries
  `role="button" tabIndex={0} aria-expanded` with an `Enter`/`Space` handler that ignores
  keydowns bubbled from nested controls (`if (e.target !== e.currentTarget) return`).

### Popovers, dropdowns, modals

Layering is §13; shadows are §14.

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
border-muted/60 text-muted/70 text-[9px] font-bold` circle containing `i`, labelled
`aria-label={`About ${title}`}` and padded to a 44px target (**A11Y-14**); the panel is a
`text-[11px] font-bold text-tx` title over `text-[11px] text-muted leading-relaxed space-y-1` body,
with `align="left"` when a centered panel would overflow.

**Dropdown menu** (`AuthWidget`, `SiteNav` mobile) — `absolute mt-2 w-44 rounded-lg bg-card border
border-border shadow-lg py-1 z-20`, `role="menu"` with `role="menuitem"` children; items are
`px-3 py-2 text-xs`. Closes on outside `mousedown` and on `Escape`, returning focus to the trigger.

**Modal** (`ConsolidatedCard`) — `fixed inset-0 z-40 flex items-start justify-center px-4 pt-24
sm:pt-32 bg-bg/80 backdrop-blur-sm` scrim (click-to-close), containing a `glass rounded-xl w-full
max-w-lg max-h-[75vh] overflow-y-auto` panel with `role="dialog" aria-modal="true"` and
`stopPropagation()`. Header is `sticky top-0 bg-card/90 backdrop-blur-sm`. Focus moves to the
close button on mount, is trapped while open, and returns to the trigger on close (**A11Y-11**);
`Escape` closes.

### Progress, loading, and empty states

The obligation to handle all five data states is §17; these are the shapes.

**Confidence bar** — `flex items-center gap-2.5 min-w-[108px]`, a `flex-1 h-1.5 rounded-full
bg-muted/15 overflow-hidden` track, a `h-full rounded-full transition-all duration-500` fill
colored `bg-buy` (≥70) / `bg-hold` (45–69) / `bg-sell` (<45), and a `text-[11px] font-bold
font-mono tabular-nums w-7 text-right` numeral in the matching text tone. Percentage bars inside
cards use the same shape on a `bg-border` track with a `bg-accent` fill.

**Skeleton** — `bg-border/60 rounded animate-pulse`, imported from `data-table-ui.tsx`. Modal
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
watchlist) caused the emptiness. See §17 for which case is which.

### Banners

Error — `px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm`. Add
`flex items-start justify-between gap-4` with a trailing retry button when recovery is possible.

Warning / degraded — `px-5 py-3 rounded-xl bg-hold/10 border border-hold/30 text-sm flex
items-start gap-2` with an `aria-hidden` `⚠` in `text-hold shrink-0`, a `font-semibold text-hold`
lead-in, and the explanation in `text-tx`. This is the **degraded analysis** banner at the top of
`ResultsDashboard` when `report.degraded` is true (every LLM provider failed and the verdict is a
safe HOLD fallback), and the shape any stale-data notice uses (**STATE-05**). Body text stays
`text-tx` so the message is readable — do not tint an entire paragraph `text-hold`.

Inline note — `text-[11px] text-hold bg-hold/8 border border-hold/20 rounded-lg px-3 py-2
leading-relaxed` (used where the analysis and picks verdicts disagree).

Form-level error — `role="alert"` on a `text-sell text-xs` paragraph (**FORM-07**).

### Toast

`components/toast.tsx` — `ToastProvider` wraps the whole app in `app/layout.tsx`; call
`useToast().showError(message)` from any client component or hook to raise one. Use for a failed
*background* mutation (a watchlist toggle, an "I bought this" save) that shouldn't take over the
page — the persistent inline Error banner above stays for failures that block a whole page's
content (a failed page-level fetch).

Reuses the Error banner's exact tone/classes, just floated, stacked, and auto-dismissing:

```html
<div class="fixed bottom-6 right-6 z-50 flex flex-col gap-2 w-full max-w-sm pointer-events-none">
  <div role="alert" class="pointer-events-auto animate-fade-up flex items-start justify-between
       gap-3 px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm
       shadow-2xl shadow-black/60">
    <span>{message}</span>
    <button aria-label="Dismiss" class="shrink-0 text-sell/70 hover:text-sell transition-colors">✕</button>
  </div>
</div>
```

Auto-dismisses after 6s or on manual close. `showError` is a no-op outside `ToastProvider` rather
than throwing. Only an error variant exists — no success/info toast has shipped, since nothing in
the product needs one yet; add one the same way if a real caller shows up, don't pre-build it.

Don't call `showError` from a handler that fires on every keystroke — a sustained failure would
stack a toast per keystroke. `updateShares` in `lib/positions.ts` deliberately stays
silent-on-failure for this reason; the mutations that do use it (`toggle`/`remove` in
`lib/watchlist.ts`, `addPosition`/`removePosition` in `lib/positions.ts`) are all one-shot click
actions.

---

## 6. Navigation

`components/site-nav.tsx` is the only nav; `PageShell` renders it inside `<header>` on every page
(**PAGE-02**). Never hand-copy this markup.

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

No chart library. Everything is hand-written SVG or CSS. Adding one requires an amendment
(META-01).

**`Sparkline`** (`sparkline.tsx`) — `<polyline>` with `stroke="currentColor"` at `strokeWidth
1.5`, `strokeLinecap`/`strokeLinejoin` round; the wrapper `<svg>` carries `text-buy` when the last
close ≥ the first, `text-sell` otherwise. `role="img"` with a descriptive `aria-label`. Default
96×28, `preserveAspectRatio="none"`. Returns `null` under 2 points. Passing a `dates` array of
equal length enables `cursor-crosshair` hover: a `strokeWidth 0.75 opacity 0.35` crosshair line, a
`r=2.5` dot, and an edge-clamped tooltip (`rounded-md border border-border bg-card px-2 py-1
text-[10px] font-mono text-tx shadow-lg shadow-black/40`).

**`EmaChart`** (`ema-chart.tsx`) — 640×220 default, `pad = 10`, three polylines: close
`text-muted/60` at `strokeWidth 1.25`, EMA50 `text-hold` and EMA20 `text-accent` at `1.75`. Cross
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
nothing, never an empty axis; hover-only readouts have a non-hover equivalent (**A11Y-16**).

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
| `2xl:` | `/compare` stacks reports side by side (`flex-col 2xl:flex-row`). |

`/compare`'s two-symbol cap was a consequence of `ResultsDashboard` using *viewport* breakpoints
rather than container queries — not a product decision. It is lifted under **PAGE-04**: the
component converts to container queries first (`@tailwindcss/container-queries`, §1), after which
a column reflows on its own width and N columns scroll horizontally at a fixed minimum width.

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
  return focus to their trigger.
- **Labels** — a real `<label>` or `aria-label` on every control (**FORM-01**).
- **Live regions** — `aria-live="polite"` on the pipeline tracker and on the ticker input's
  `sr-only` validation-status text; `aria-busy` on loading regions (**STATE-1**).
- **Decorative glyphs** — `aria-hidden="true"` on `⚠`, `⟳`, `☰`, and status icons whose meaning is
  already in an adjacent label.
- **Motion** — the global `prefers-reduced-motion` block in §8.
- **Redundant encoding** — signal color is always paired with a glyph or word (`▲`/`▼`, `↑`/`↓`,
  `✓`/`✗`, `+`/`−`, the verdict text itself), so red/green is never the sole carrier of meaning.

### Contrast, measured

WCAG 2.1 contrast ratios of the current tokens (AA needs 4.5:1 for normal text, 3:1 for text
≥18.66px bold or ≥24px). **Every token now clears AA on every surface** after `muted` was raised
(§2) and `sell`/`accent` were lightened (**COLOR-04**):

| Foreground | on `bg` | on `surface` | on `card` | on `card-hi` |
|---|---|---|---|---|
| `tx` | 15.32 | 14.44 | 13.06 | 11.85 |
| `buy` | 10.17 | 9.58 | 8.67 | 7.87 |
| `hold` | 9.29 | 8.76 | 7.92 | 7.19 |
| `accent` | 6.12 | 5.77 | 5.21 | 4.73 |
| `muted` | 6.12 | 5.77 | 5.22 | 4.74 |
| `sell` | 6.00 | 5.65 | 5.11 | 4.64 |

Ink on solid fills (`text-bg`): `buy` 10.17, `hold` 9.29, `accent` 6.12, `sell` 6.00 —
**COLOR-06**.

Reduced-opacity `muted` remains the one live hazard: `/70` is 3.59:1, `/60` 2.95:1, `/50` 2.41:1.
**COLOR-05** sets the floor at `/60` and bars faded `muted` from anything a user must read; legal
disclaimers use full `muted` (6.12:1).

### Focus indicator

One global rule in `globals.css` covers every interactive element — buttons, links, inputs, chips,
clickable table rows — so no component opts in individually:

```css
:focus-visible {
  outline: 2px solid #618eff;   /* = accent; keep in sync (SRC-03) */
  outline-offset: 2px;
  border-radius: 2px;
}
```

`:focus-visible`, not `:focus`, so a mouse click doesn't leave a ring behind while keyboard and
programmatic focus still get one. **Components MUST NOT re-declare their own focus outline**
(**A11Y-15**) — `focus:outline-none` out-specifies the global rule ((0,2,0) vs (0,1,0)) and is
banned; `focus:border-accent/40` MAY stay as decoration. *(Closes §12.5.)*

### Rules closing the former NOT IMPLEMENTED list

- **A11Y-11** — The modal MUST trap focus: Tab cycles within the panel, focus returns to the
  trigger on close, background is `inert`. A dialog a keyboard user can Tab out of but not see is a
  trap in the other direction.
- **A11Y-12** — Skip link as the first focusable element: `sr-only focus:not-sr-only`, jumping to
  `#main`. Delivered by `PageShell` (**PAGE-02**), so no page opts in.
- **A11Y-13** — Landmarks MUST exist: `<header>`, `<nav>`, one `<main>`, `<footer>`. A `<div>` tree
  with correct ARIA is not a substitute.
- **A11Y-14** — Every interactive target MUST present ≥44×44px of hit area, regardless of painted
  size. Expand with padding plus a negative margin (`p-3 -m-3`) so layout is unchanged. This is
  what fixes the 14px `InfoTooltip` trigger and the ~16px `sm` watchlist star — on a phone-first
  product, two of the most-tapped controls in the app.
- **A11Y-15** — No component declares `focus:outline-none`; the global `:focus-visible` ring is the
  focus indicator everywhere.
- **A11Y-16** — A hover-only affordance MUST have a non-hover equivalent. Chart crosshair tooltips
  carry the same figures in the chart's `aria-label` or an adjacent readout — on a phone-first
  product, hover reaches nobody.
- **A11Y-17** — Heading order is audited by the axe pass (**ENF-03**), not by eye (**PAGE-03**).

---

## 11. Do / Don't

| Do | Don't |
|---|---|
| Use the 12 tokens for every color in `app/` and `components/` | Hard-code a hex outside the four exception files in §2 |
| Reuse `Card`, `MetricRow`, `Skeleton`, `FilterChip`, `SortableTh`, `InfoTooltip`, `PageShell` | Re-implement or copy-paste a primitive because the props don't quite fit |
| Express depth with token + opacity, on the ladder | Invent a new color, add a color scale, or use an off-ladder opacity |
| Use `hold` for warning/caution states | Add a `warning` token, or reuse `accent` for a data state |
| Keep `WATCHLIST` as dimmed `buy` | Give WATCHLIST its own hue |
| `font-mono` + explicit `tabular-nums` on aligned numbers | Mix sans and mono within one numeric column |
| Format every number through `lib/format.ts` | Call `toFixed`/`toLocaleString` in a component |
| Render a missing value as `—` | Render `0`, `N/A`, or a blank cell |
| Keep `px-4 py-4` on body cells and `px-4 py-3` on headers | Vary row padding per row |
| Handle all five states in §17 | Ship a surface that only has a happy path |
| `return null` for an absent add-on section | Render an empty card with a placeholder value |
| Give an empty state a reason and a recovery action | Leave a blank region |
| Mark stale data visibly | Let a stale price render identically to a fresh one |
| Pair every signal color with a glyph or word | Rely on red/green alone |
| Add `aria-pressed` / `aria-expanded` / `aria-sort` to new controls | Ship a `<div onClick>` |
| Let the global `:focus-visible` rule and `prefers-reduced-motion` block do their job | Add `focus:outline-none` or per-component `motion-reduce:` variants |
| Pad small controls out to 44×44 | Ship a 14px tap target |
| Use `bg-card border border-border` by default | Use `glass` where nothing is layered behind it |
| `truncate min-w-0` on flex text, `shrink-0` on its badges | Let text overflow, or let a badge collapse |
| Add new SSE/report fields to `frontend/types/index.ts` first | Inline ad-hoc types in a component |
| Propose a rule when none covers your case (META-01) | Infer one from the nearest existing pattern |
| Run `npx tsc --noEmit` (and `npm run build` after CSS changes) before finishing | Ship without type-checking |

---

## 12. Known drift

Real inconsistencies in the shipped code, listed so they aren't mistaken for precedent.

1. ~~**Stale raw hex in the primary CTA.**~~ **Fixed.** `ticker-search.tsx` hardcoded
   `#6c71f040`/`#6c71f060` — a retired accent — so the app's one primary CTA glowed in a dead brand
   colour. Now `shadow-accent-glow` / `hover:shadow-accent-glow-lg`, tokenised under `boxShadow`.
2. ~~**No global focus indicator.**~~ **Fixed.** See §10.
3. ~~**`muted` fails AA; white-on-solid-fill text fails contrast.**~~ **Fixed.** `muted` raised
   `#6b7fa8` → `#8093bd`; every solid tone fill moved from `text-white` to `text-bg`.
4. ~~**Page container width is not standardised.**~~ **Fixed in Revision 2** — three tiers,
   **PAGE-01**. Migration: `/portfolio` and `/portfolio-aggregator` move `max-w-4xl` → `max-w-5xl`.
5. ~~**Two focus treatments on inputs.**~~ **Fixed in Revision 2** — **A11Y-15**; the five
   `focus:outline-none` inputs drop it and inherit the global rule.
6. ~~**`Skeleton` is duplicated.**~~ **Fixed in Revision 2** — **SRC-02**; the local copies in
   `app/watchlist/page.tsx` and `app/market-picks/history/page.tsx` and the inlined strings in
   `/portfolio` + `/api-keys` all import from `data-table-ui.tsx`.
7. ~~**No `warning` token.**~~ **Resolved in Revision 2** — **COLOR-01** rules that none is added
   and `ConcentrationBadge` moves from `accent` to `hold`.
8. ~~**`§`-numbered references in code.**~~ **Fixed in Revision 2** — **SRC-04**; comments cite rule
   IDs, so renumbering a section can no longer invalidate a reference.

### Still open

- **Reduced-opacity `muted`** — `/70` and below remain under AA. **COLOR-05** bounds where they
  may appear rather than removing them; the remaining uses are decorative or tertiary.
- **The Revision 2 migrations** (items 4–8 above) describe the intended state; each needs a PR.

---

## 13. Elevation & z-index

Four z-values ship, with meanings. This is the whole scale.

| Layer | Class | Occupied by |
|---|---|---|
| 0 | — | All page content. Cards never raise themselves. |
| 10 | `z-10` | Sticky `<thead>`, and a popover's full-viewport click-catcher. |
| 20 | `z-20` | Popover, dropdown, and nav panels — anything anchored to a trigger. |
| 40 | `z-40` | Modal scrim and panel. Nothing else ever sits at 40. |
| 50 | `z-50` | Toasts. The ceiling — a toast must survive an open modal. |

- **ELEV-01** — Components MUST use one of 10 / 20 / 40 / 50. Any other z-index, positive or
  negative, is rejected.
- **ELEV-02** — A positioned element MUST NOT carry a z-index it doesn't need. If two siblings
  fight, fix the DOM order first.
- **ELEV-03** — Two overlays MUST NOT be open at once. Opening a modal closes any popover; opening
  a popover closes any other. With only one overlay live, no layer ever has to out-rank a peer.

---

## 14. Shadow scale

On a near-black background a shadow reads as a dark halo, not as lift. It separates a floating
surface from the page, and does nothing else.

- **SHADOW-01** — In-flow surfaces (cards, panels, table wrappers, banners, inputs) MUST NOT carry
  a shadow. Depth in flow is `bg-card-hi` plus a border.
- **SHADOW-02** — Floating surfaces use exactly one of: `shadow-lg` (dropdown menus);
  `shadow-2xl shadow-black/60` (popovers, modal panel, toasts); `shadow-lg shadow-black/40`
  (chart hover tooltips).
- **SHADOW-03** — Colored shadow is reserved for the primary CTA glow (`shadow-accent-glow` /
  `hover:shadow-accent-glow-lg`). No other element MAY glow. Signal-toned glows (`shadow-buy`,
  `shadow-sell`) are forbidden: a glowing row implies urgency the data doesn't support.

---

## 15. Form controls

§5 covers `<input>` and `<select>`. Everything else is here, derived from the same pattern — no new
tokens.

```tsx
<label htmlFor={id} className="block text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-1.5">
  Label {required && <span aria-hidden className="text-sell">*</span>}
</label>
<input id={id} aria-describedby={hint ? hintId : undefined} aria-invalid={!!error} … />
<p id={hintId} className="mt-1.5 text-xs text-muted">Hint</p>
<p role="alert" className="mt-1.5 text-xs text-sell">Error</p>
```

- **FORM-01** — Every control MUST have a real `<label htmlFor>` or an `aria-label`.
  Placeholder-as-label is rejected — it disappears on focus and fails contrast.
- **FORM-02** — Checkbox and radio: the native control, restyled with `accent-accent`, sized
  `w-4 h-4`, inside a `<label>` with `flex items-center gap-2 py-2 cursor-pointer text-sm text-tx`.
  No custom div-based checkbox. The label's vertical padding is what reaches 44px (**A11Y-14**).
- **FORM-03** — Binary settings MUST use a checkbox, not a toggle switch. No switch component
  exists; do not build one for a single caller.
- **FORM-04** — 2–6 mutually exclusive options in a filter context use `FilterChip`; in a form that
  submits, use radios.
- **FORM-05** — `<textarea>` reuses the input string with `min-h-[96px] resize-y leading-relaxed`.
  Never `resize-none`.
- **FORM-06** — A submitting button MUST show progress in place: label swaps to a verb-ing form
  with the `⟳ animate-spin-slow` glyph, `disabled`, width held. Never a full-page spinner for an
  inline mutation.
- **FORM-07** — Validation fires on blur and on submit, never on keystroke. On failed submit, focus
  moves to the first invalid field. Field errors are `role="alert"`; a form-level failure uses the
  Error banner, not a toast.

---

## 16. Numbers, currency, dates

A financial UI where the same number renders three ways across three pages reads as unreliable,
whatever the data quality. All formatting MUST come from one module (`lib/format.ts`, **SRC-01**);
components never call `toFixed` or `toLocaleString` directly.

| Value | Renders as | Rule |
|---|---|---|
| Price | `₹1,24,500.50` | Indian grouping (`en-IN`), 2dp always, no space after ₹. |
| Market cap | `₹1.24 L Cr` | Cr / L Cr, never M / B / T. 2 significant decimals. |
| Change | `+2.4%` / `−1.8%` | Sign always shown. Minus is U+2212, not a hyphen — it aligns in mono. |
| Ratio (P/E) | `28.4` | 1dp. Negative earnings render `—`, never a negative P/E. |
| Volume | `12.4 L` | Lakh above 1,00,000; raw with grouping below. |
| Missing | `—` | Em dash in `text-muted`. Never `0`, `N/A`, `null`, or a blank cell. |
| Timestamp | `5 Aug 2026, 15:30 IST` | Always IST, always labelled. 24h. Wrapped in `<time dateTime>`. |
| Freshness | `4m ago` | Relative under 24h, absolute after, with the absolute in `title`. |

- **NUM-01** — Any number a user compares vertically MUST carry `font-mono tabular-nums` and be
  right-aligned. Labels stay sans.
- **NUM-02** — Precision is fixed per value type, never per value. A price is 2dp whether it is
  ₹4.00 or ₹42,318.75 — varying decimals break column alignment.
- **NUM-03** — A signed number MUST carry both sign and tone; exactly zero is `text-muted` with no
  sign. The redundant-encoding rule (§10) applied to numerals.
- **NUM-04** — Every quoted price MUST state its as-of time and exchange within the same visual
  block. A price without provenance is not shippable; delayed data presented as live is the one
  design bug in this product with regulatory weight.

---

## 17. The five states

Every component that renders fetched data MUST handle all five. A PR adding a data surface is
reviewed against this list.

1. **Loading** — `Skeleton` matching the final layout's shape and row count. Region carries
   `aria-busy="true"`. Never a spinner for content, never a layout that shifts on arrival.
2. **Empty by the user's action** — a filter matched nothing, the watchlist is unstarted. Visible
   empty state: reason in `text-sm text-muted` plus a recovery action.
3. **Empty by nature** — the upstream has no data for this stock. `return null` — no card, no
   placeholder, no "no data available".
4. **Error** — blocks the page → inline Error banner with retry. Background mutation → toast.
   Never both, never a silent failure.
5. **Stale or degraded** *(most often missed)* — data rendered, but older than its refresh interval
   or produced by a fallback path. The `hold`-toned warning banner (§5) exists for exactly this; a
   stale price MUST NOT render identically to a fresh one.

- **STATE-01** — A refetch MUST NOT replace visible content with skeletons. Keep the last good
  render, mark it stale, swap on arrival.

---

## 18. Voice & UI copy

Typography is specified down to `text-[8px]`; in a research product the wording carries as much
risk as the color.

- **VOICE-01** — Sentence case everywhere except verdict badges, `<th>` labels, and card titles,
  which are uppercase by §3. Never Title Case.
- **VOICE-02** — The UI describes analysis; it MUST NOT issue instructions. "Model reads bullish on
  a 6-month horizon", not "Buy this stock". The BUY/SELL badge is a scoring output and stays as-is;
  surrounding prose must not restate it as advice.
- **VOICE-03** — Error copy names what failed and what to do next: "Couldn't reach the NSE quote
  feed. Retry, or check back in a few minutes." No error codes in user-facing text, no bare
  "Something went wrong".
- **VOICE-04** — Model output is labelled as such wherever it appears next to market data, so a
  user can always tell a computed figure from a generated one.
- **VOICE-05** — No exclamation marks, no emoji (§5), no hype adjectives in system copy.
  Disclaimers render at full `text-muted`, never faded (**COLOR-05**).

---

## 19. Enforcement

A rule nothing checks is a preference. `tsc --noEmit` and Playwright are the gates today, and
neither can see a hex literal or a missing `aria-pressed`. Four additions cover most of this
document, with no new runtime dependency.

- **ENF-01** — A CI grep for `#[0-9a-fA-F]{3,8}` across `app/` and `components/`, allow-listing the
  four exception files in §2. Ten lines; catches the exact class of bug that let the CTA glow in a
  retired brand color for months.
- **ENF-02** — A second grep for off-ladder opacities (**COLOR-03**), sub-floor `text-muted/`
  values (**COLOR-05**), `text-white`, `dark:`, `motion-reduce:`, `focus:outline-none`, and
  z-indexes outside §13.
- **ENF-03** — An axe pass in the existing Playwright suite, one per route, failing on landmark,
  label, and heading-order violations (`ENF_03_RULES` in `e2e/fixtures.ts`). `color-contrast` is
  deliberately excluded from the scanned rule set — contrast is a design-token property, checked
  once at the palette level (**COLOR-05**), not per-render. The only mechanism that keeps
  A11Y-11…17 from re-entering a NOT IMPLEMENTED list.
- **ENF-04** — A unit test asserting the exported tone maps (**SRC-01**) equal the strings in §2,
  so a color change has to be a deliberate two-file edit.

---

## 20. Governance

**Owner: Lucky Jain.** Amendments are reviewed by him, and this document is edited in the same PR
as the code it describes (META-01).

### Single-source rules

Every drift item in §12 has the same shape: a value that lived in more than one place and stopped
agreeing with itself. The rule is structural, not vigilance-based.

- **SRC-01** — A class string or format function that encodes a *semantic mapping* (verdict → tone,
  status → chip, sentiment → color, number → display) MUST live in exactly one exported module and
  be imported. Tone maps live in `lib/tone.ts`; formatting in `lib/format.ts`.
- **SRC-02** — A shared primitive is imported, never re-declared. Copy-paste of a primitive is a
  review rejection even when byte-identical — especially then.
- **SRC-03** — Any raw hex outside the four exception files in §2 is a build failure, not a review
  note (**ENF-01**). Those files carry a comment pointing back at the token table.
- **SRC-04** — Code comments cite rule IDs, never section numbers.

### Decision log — Revision 2

| # | Question | Decision |
|---|---|---|
| 1 | Do `sell` and `accent` get lightened? | **Yes.** Measured, hue and saturation held; `#e46b7b` and `#618eff`. Every token now clears AA on every surface (**COLOR-04**). |
| 2 | Is the container migration worth the diff? | **Yes.** Three tiers (**PAGE-01**). The width jump between Watchlist and Portfolio is more visible than either width is on its own; both pages are card-and-row layouts that suit `5xl`. Two files, one class each. |
| 3 | Does `/compare` stay capped at two? | **No — it expands.** Container-query conversion first, then the cap (**PAGE-04**). The remaining product call is what N is: 3, 4, or unbounded with horizontal scroll. |
| 4 | Who owns this document? | **Lucky Jain.** |
