# AlphaPulse Design System

> Single source of truth for visual language, component patterns, and interaction guidelines.
> All new UI work must reference this document. Decisions not covered here follow the nearest
> existing pattern rather than introducing new ones.

---

## 1. Brand Identity

**Product name:** AlphaPulse  
**Tagline:** Institutional-grade research. Individual-scale clarity.  
**Character:** Precise, confident, information-dense — never cluttered. Feels like a Bloomberg terminal
that actually respects the user's eyes.

---

## 2. Color Palette

All tokens are defined in `frontend/tailwind.config.ts` and must be used via Tailwind utilities,
never as raw hex values in component code. Update the config to change colors project-wide.

### Foundation

| Token       | Hex       | Usage |
|-------------|-----------|-------|
| `bg`        | `#0b1120` | Page background — Midnight Blue (brief-specified) |
| `surface`   | `#0f1829` | Raised surfaces: nav bars, sidebars, panels |
| `card`      | `#132040` | Card backgrounds |
| `card-hi`   | `#1a2848` | Hover/elevated card state |
| `border`    | `#1d2e4e` | Default dividers and card outlines |
| `border-hi` | `#243860` | Focused/active borders |

### Text

| Token   | Hex       | Usage |
|---------|-----------|-------|
| `tx`    | `#e2e8f4` | Primary text — cool white, high contrast on `bg` |
| `muted` | `#6b7fa8` | Secondary labels, metadata, placeholders |

### Signal Colors

| Token  | Hex       | Usage |
|--------|-----------|-------|
| `buy`  | `#10d98e` | Gains, BUY signals, positive change — **vibrant green** |
| `sell` | `#e05568` | Losses, SELL signals, negative change — **muted red** |
| `hold` | `#f5a623` | Neutral/HOLD signals, warnings, amber alerts |

### Accent

| Token    | Hex       | Usage |
|----------|-----------|-------|
| `accent` | `#4d7fff` | CTAs, links, active states, highlights — true blue matching Midnight Blue theme |

### Usage rules

- Never use `buy`/`sell` for decorative purposes — they carry semantic meaning.
- `accent` is for interactive affordances, not for informational labels.
- Opacity modifiers (`/10`, `/20`, `/30`) are used extensively for tinted backgrounds
  on badges and chips. Stick to the pattern: `/8`–`/15` for background fill, `/20`–`/30`
  for borders, full opacity for text.

---

## 3. Typography

**Sans:** Inter (Google Fonts, loaded via `next/font`) — all UI text  
**Mono:** JetBrains Mono (Google Fonts) — prices, percentages, tickers, table numbers

```
Font variable  →  --font-sans / --font-mono (set in layout.tsx)
Tailwind       →  font-sans / font-mono
```

### Scale

| Role | Tailwind class | Weight | Usage |
|------|---------------|--------|-------|
| Hero heading | `text-4xl font-black tracking-tight` | 900 | Page H1s |
| Section heading | `text-xl font-black tracking-tight` | 900 | Dashboard section titles |
| Card title | `text-sm font-semibold` | 600 | Card headers, stock names |
| Body | `text-sm` | 400 | Prose, descriptions |
| Label | `text-[11px] font-semibold uppercase tracking-wider` | 600 | Column headers, section labels |
| Meta | `text-xs text-muted` | 400 | Timestamps, source names |
| Micro | `text-[10px]` | varies | Badges, chips, sub-labels |
| Number | `font-mono tabular-nums` | bold | All financial figures |

### Rules

- All financial numbers (price, %, P/E, market cap) use `font-mono tabular-nums` for alignment.
- Column headers use `uppercase tracking-wider text-[10px] font-bold text-muted`.
- Never use `font-light` or `text-xs` for primary data — legibility at small sizes matters.

---

## 4. Spacing & Layout

### Page container

```
max-w-5xl mx-auto px-4
```

Wide content (full-bleed tables): nested `overflow-x-auto` inside a `rounded-xl border border-border` wrapper.

### Grid system

| Context | Pattern |
|---------|---------|
| 2-col hero | `grid-cols-1 md:grid-cols-[1fr_320px] gap-10 md:gap-16` |
| 3-col stats | `grid-cols-3 gap-3` |
| Card grid (sources, tags) | `grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3` |
| Expanded row detail | `grid-cols-1 md:grid-cols-3 gap-5` |

### Vertical rhythm

- Between major sections: `mb-8` / `mb-10`
- Between list items: `space-y-1.5` (tight), `space-y-3` (comfortable), `space-y-5` (section-level)
- Card internal padding: `px-4 py-4` (standard), `px-5 py-5` (roomy), `px-3 py-2.5` (compact)
- Table row height: `py-4` (always — keeps uniform row height across all rows)

---

## 5. Component Patterns

### Cards

Two card tiers:

**Solid card** — default, for data-dense panels:
```html
<div class="rounded-xl border border-border bg-card px-4 py-4">
```

**Glass card** — for hero sections, featured picks, modals, and elevated surfaces:
```html
<div class="glass rounded-xl">
```
`glass` is defined in `globals.css`:
```css
.glass {
  background: rgba(19, 32, 64, 0.65);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.06);
}
```

**Elevated hover state:**
```html
hover:bg-card-hi transition-colors
```

**Never** mix raw `bg-[#hex]` values in components — always use the design token.

---

### Badges & chips

Signal badges (recommendation, sentiment):
```html
<!-- BUY -->
<span class="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border
             bg-buy/12 text-buy border-buy/25">BUY</span>

<!-- SELL -->
<span class="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border
             bg-sell/12 text-sell border-sell/25">SELL</span>

<!-- HOLD -->
<span class="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border
             bg-hold/12 text-hold border-hold/25">HOLD</span>
```

Informational chips (filter, tag, type):
```html
<span class="px-3 py-1.5 rounded-full text-[11px] font-semibold border
             bg-accent/10 border-accent/30 text-accent">Active filter</span>
```

Status pills (cached, new, IPO):
```html
<span class="text-[9px] font-bold px-1.5 py-0.5 rounded-full uppercase tracking-wider
             bg-hold/10 text-hold border border-hold/20">cached</span>
```

**Rule:** Background opacity `/8`–`/15`, border opacity `/20`–`/30`, text at full opacity.

---

### Buttons

**Primary CTA:**
```html
<button class="px-7 py-3 rounded-xl bg-accent text-white font-bold text-sm
               hover:bg-accent/90 active:scale-95 transition-all shadow-lg shadow-accent/20">
  Action →
</button>
```

**Secondary / outline:**
```html
<button class="px-3 py-1.5 rounded-lg text-xs font-semibold border
               border-accent/30 text-accent hover:bg-accent/10 transition-colors">
  Secondary
</button>
```

**Ghost:**
```html
<button class="text-xs text-muted hover:text-tx transition-colors">
  Ghost action
</button>
```

**Destructive (error states):**
```html
<button class="px-3 py-1 rounded-lg text-xs font-semibold border
               border-sell/40 text-sell hover:bg-sell/10 transition-colors">
  Retry
</button>
```

**Rules:**
- Primary CTA always uses `shadow-lg shadow-accent/20` for the lift effect.
- `active:scale-95` on primary CTAs only — confirms the action registered.
- Rounded corners: `rounded-xl` (large buttons), `rounded-lg` (small), `rounded-full` (pills/chips).

---

### Inputs & search

```html
<input class="w-full bg-card border border-border rounded-xl px-4 py-2.5 text-sm text-tx
              placeholder:text-muted/50 focus:outline-none focus:border-accent/40 transition-colors">
```

Search inputs get `pl-9` + an absolutely-positioned SVG magnifier icon at `left-3`.  
Clear buttons sit at `right-3` as a `✕` ghost button.

---

### Tables

Full pattern:

```html
<div class="rounded-xl border border-border overflow-hidden">
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border bg-surface sticky top-0 z-10">
          <th class="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">
            Column
          </th>
        </tr>
      </thead>
      <tbody>
        <tr class="border-b border-border/60 hover:bg-surface/60 cursor-pointer transition-colors">
          <td class="px-4 py-4">...</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
```

**Rules:**
- `sticky top-0 z-10` on `<thead>` — always sticky.
- Row `py-4` — never vary this; it keeps row heights uniform.
- All financial cells: `font-mono tabular-nums text-xs`.
- Positive/negative delta cells: `text-buy` / `text-sell` with directional `↑`/`↓` prefix.
- Expandable rows use `<React.Fragment>` + a hidden `<ExpandedRow>` component spanning all columns.
- Expanded row background: `bg-card/60`.

---

### Progress & loading states

**Confidence bar:**
```html
<div class="flex items-center gap-2.5 min-w-[108px]">
  <div class="flex-1 h-1.5 rounded-full bg-muted/15 overflow-hidden">
    <div class="h-full rounded-full bg-buy transition-all duration-500" style="width: 75%"></div>
  </div>
  <span class="text-[11px] font-bold font-mono tabular-nums w-7 text-right text-buy">75</span>
</div>
```
Color: `bg-buy` (≥70), `bg-hold` (45–69), `bg-sell` (<45).

**Shimmer skeleton:**
```html
<div class="rounded bg-gradient-to-r from-border via-border-hi to-border
            animate-shimmer bg-[length:200%_auto]">
```

**Spinner:**
```html
<div class="w-3 h-3 rounded-full border border-accent border-t-transparent animate-spin-slow"></div>
```

**Pipeline stepper:** numbered circles — done = `bg-buy border-buy text-white`, active = `bg-accent border-accent text-white`, pending = `bg-surface border-border text-muted`.

---

### Rank badges

```
#1  → filled accent circle: w-6 h-6 rounded-full bg-accent text-white
#2–3 → outlined accent circle: bg-accent/20 text-accent
#4+  → plain muted number: text-xs text-muted/50
```

---

### Popovers

Backdrop: `<div class="fixed inset-0 z-10" onClick={close} />`  
Panel: `absolute top-full mt-2 z-20 w-64 bg-card border border-border rounded-xl shadow-2xl shadow-black/60`

---

### Notification / alert banners

Error:
```html
<div class="px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm">
```

Info/cached:
```html
<div class="px-5 py-4 rounded-xl bg-hold/10 border border-hold/30 text-hold text-sm">
```

---

## 6. Navigation

**Top bar (all pages):**
```html
<div class="flex items-center gap-4 mb-8 pb-4 border-b border-border">
  <a class="text-base font-black tracking-tight text-tx">
    Alpha<span class="text-accent">Pulse</span>
  </a>
  <span class="text-border-hi">|</span>
  <span class="text-sm font-semibold text-accent">Current Section</span>
</div>
```

Logo lockup: word + `text-accent` colored suffix — e.g., `Alpha` + `Pulse` in accent.

---

## 7. Data Visualization

- **Sparklines / mini-charts:** vector-based SVG, stroke color `#10d98e` (buy) or `#e05568` (sell).
- **Bar charts:** `h-1.5 rounded-full` bars on `bg-muted/15` track; bar color by signal.
- **Progress arcs / circles:** `w-full h-full border-2 rounded-full` — track `border-border`, fill via a clipped `div` or SVG arc.
- **No external chart libraries** unless explicitly added to `package.json` — use SVG/CSS for all current visualizations.

---

## 8. Animations

All animation tokens are in `tailwind.config.ts`:

| Token | Duration | Usage |
|-------|----------|-------|
| `animate-fade-up` | 400ms ease | Page sections entering, result dashboards |
| `animate-shimmer` | 1.8s linear infinite | Skeleton loading placeholders |
| `animate-spin-slow` | 0.8s linear infinite | Inline loading spinners |
| `animate-pulse` | default | Live indicators (LTP dot), active batch cells |

**Rules:**
- Only the outermost wrapper gets `animate-fade-up` — don't nest fade animations.
- Don't animate layout-affecting properties (width, height, top) — use `opacity` and `transform` only.
- `transition-colors` on all interactive elements (buttons, rows, chips).
- `transition-all duration-500` on data bars (confidence, progress bars) — gives satisfying fill animation.

---

## 9. Responsive Strategy

- **Mobile-first:** all components work at 375px. Desktop enhances, doesn't replace.
- Page wrapper: `max-w-5xl mx-auto px-4` — no separate mobile/desktop wrappers.
- Breakpoints used: `sm:` (640px), `md:` (768px), `lg:` (1024px).
- Tables: always wrapped in `overflow-x-auto` — never hide columns on mobile.
- Two-column layouts collapse to one column below `md:`.
- Grid source cards: `grid-cols-2 md:grid-cols-3 lg:grid-cols-5`.

---

## 10. Do / Don't

| Do | Don't |
|----|-------|
| Use design tokens for all colors | Hard-code hex values in components |
| Use `font-mono tabular-nums` for all numbers | Mix sans and mono arbitrarily |
| Use semantic signal colors (buy/sell/hold) | Use accent color for data labels |
| Keep `py-4` on all table rows | Vary row padding conditionally |
| Use glass card for hero/feature sections | Use glass on every card |
| Add `backdrop-blur` only where there's a background to blur against | Apply blur on solid `bg-bg` backgrounds (no effect) |
| Use `truncate min-w-0` for overflow text in flex | Let text overflow without truncation |
| Use `shrink-0` on badges alongside truncating text | Let badges shrink |
| Add new SSE events to `types/index.ts` first | Inline ad-hoc types in components |
| Run `npx tsc --noEmit` before marking frontend tasks done | Ship without type-checking |
