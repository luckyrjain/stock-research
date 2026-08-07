// Canonical tone-mapping module (SRC-01, design.md §2) — a class string that
// encodes a *semantic mapping* (verdict -> tone, status -> chip, sentiment ->
// color) MUST live here and be imported, never re-declared per call site.

// 4-tier (Market Picks, Consolidated card, Track Record) — identical across
// every caller. WATCHLIST is a dimmer BUY, not its own hue (design.md §2):
// deliberately the same green at lower opacity, since it's a lower-
// conviction bullish tier. Never give it accent or hold.
export const REC_TONE_4TIER: Record<string, string> = {
  BUY:       'bg-buy/12 text-buy border-buy/25',
  WATCHLIST: 'bg-buy/8 text-buy/75 border-buy/15',
  HOLD:      'bg-hold/12 text-hold border-hold/25',
  SELL:      'bg-sell/12 text-sell border-sell/25',
};

export const REC_LABEL_4TIER: Record<string, string> = {
  BUY: 'BUY', WATCHLIST: 'WATCH', HOLD: 'HOLD', SELL: 'SELL',
};

// unknown falls back to this (design.md §2's 4-tier table, and
// verdict-timeline.tsx's own fallback for a null/unrecognized recommendation).
export const REC_TONE_UNKNOWN = 'bg-muted/10 text-muted border-muted/20';
export const REC_TONE_UNKNOWN_TIMELINE = 'bg-card-hi text-muted border-border';

// 3-tier (single-stock analysis hero) — results-dashboard.tsx's REC_CONFIG.
// `badge` is the solid-fill verdict pill; its text is text-bg (COLOR-06),
// never text-white — these fills are deliberately bright enough that white
// fails the 3:1 WCAG bar for large text, on the single most prominent
// element in the product. `text`/`bg`/`border` are the tinted variants over
// a dark surface and are unaffected.
export const REC_CONFIG_3TIER = {
  BUY:  { bg: 'bg-buy/10',  border: 'border-buy/30',  text: 'text-buy',  badge: 'bg-buy  text-bg', strip: 'bg-buy'  },
  SELL: { bg: 'bg-sell/10', border: 'border-sell/30', text: 'text-sell', badge: 'bg-sell text-bg', strip: 'bg-sell' },
  HOLD: { bg: 'bg-hold/10', border: 'border-hold/30', text: 'text-hold', badge: 'bg-hold text-bg', strip: 'bg-hold' },
} as const;

// Confidence HIGH/MEDIUM/LOW -> buy/hold/sell (design.md §2's "Other semantic
// mappings" line).
export const CONFIDENCE_TONE: Record<string, string> = {
  HIGH: 'text-buy', MEDIUM: 'text-hold', LOW: 'text-sell',
};

// News sentiment Positive/Neutral/Negative -> buy/muted/sell.
export const SENTIMENT_TONE: Record<string, string> = {
  Positive: 'text-buy', Neutral: 'text-muted', Negative: 'text-sell',
};

// Valuation verdict Undervalued/Overvalued/other -> buy/sell/hold.
export function valuationTone(verdict: string | null | undefined): string {
  if (verdict === 'Undervalued') return 'text-buy';
  if (verdict === 'Overvalued')  return 'text-sell';
  return 'text-hold';
}

// Exchange tag: BSE -> hold, NSE -> buy (design.md §2). The one caller with a
// third "both" state (a dual-listed stock's hero badge) isn't a tone this
// module defines — accent is never a data label (COLOR-02), so that caller
// falls back to a neutral surface/muted tone itself rather than getting a
// third branch here.
export function exchangeTone(exchange: string | null | undefined): string {
  return exchange === 'BSE'
    ? 'bg-hold/10 text-hold border-hold/20'
    : 'bg-buy/10 text-buy border-buy/20';
}
