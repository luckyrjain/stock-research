// Small, pure formatting helpers shared across several ResultsDashboard cards
// (and, for safeExternalHref below, market-picks-dashboard.tsx too — the
// same link-safety concern isn't specific to one dashboard). Anything used
// by only one card lives with that card instead (see e.g.
// insider-activity-card.tsx's fmtActivityDate, street-consensus-card.tsx's
// fmtConsensusDate).

import type { DataFreshness } from '@/types';

export function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return '—';
  return n.toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtCr(n: number | null | undefined) {
  if (n == null) return '—';
  if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(2)}L Cr`;
  if (n >= 1_000)    return `₹${(n / 1_000).toFixed(2)}K Cr`;
  return `₹${fmt(n)} Cr`;
}

export function fmtVolume(n: number | null | undefined) {
  if (n == null) return '—';
  return n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

// Raw-rupee formatter for insider-trade/bulk-deal values (L/Cr), distinct
// from fmtCr() above which expects a value already denominated in crores
// (e.g. market_cap_cr) — these come off the wire as plain rupee amounts.
export function fmtInr(n: number): string {
  if (n >= 1_00_00_000) return `₹${(n / 1_00_00_000).toFixed(1)} Cr`;
  if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(1)}L`;
  return `₹${n.toLocaleString('en-IN')}`;
}

// Bridges ratio labels between two independent sources — the analyst-facing
// "research" task's own ratio names (e.g. "P/E", "ROCE") and Screener's peer
// table's column headers (e.g. "P/E", "ROCE %") — so a percentile computed
// against one set of labels can still be looked up against the other.
export function normalizeRatioKey(key: string): string {
  return key.toLowerCase().replace(/[^a-z0-9]/g, '');
}

export function formatAge(dateStr: string): string {
  const today     = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  if (dateStr === today)     return 'Updated today';
  if (dateStr === yesterday) return 'Updated yesterday';
  return `Updated ${new Date(dateStr).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}`;
}

// Human-friendly "how long ago" for a real fetch timestamp (data_freshness),
// distinct from formatAge() above — that one describes report.generated_at
// (when the report was assembled, stamped fresh on every call regardless of
// whether anything was actually refetched); this describes when a specific
// task's underlying data was actually last fetched, which can lag well
// behind "today" for a long-TTL task (shareholding/mf_holdings, 168h).
export function formatDataAge(iso: string | null): string {
  if (!iso) return 'unknown';
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return 'unknown';
  const hours = ms / 3_600_000;
  if (hours < 1) return 'just now';
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export const DATA_FRESHNESS_LABELS: Record<string, string> = {
  stock_info: 'Price',
  research: 'Fundamentals',
  news: 'News',
  shareholding: 'Shareholding',
  mf_holdings: 'MF Holdings',
  filings: 'Filings',
};

// The oldest of the six tasks' own fetch timestamps — the true bottleneck
// on "how fresh is everything shown on this page", since a report can be
// "generated" (assembled) today while its stalest constituent (typically
// shareholding/mf_holdings, 168h TTL) is up to a week old.
export function oldestDataFreshness(
  freshness: DataFreshness | undefined,
): { task: string; iso: string } | null {
  if (!freshness) return null;
  let oldest: { task: string; iso: string; ts: number } | null = null;
  for (const [task, iso] of Object.entries(freshness)) {
    if (!iso) continue;
    const ts = new Date(iso).getTime();
    if (!Number.isFinite(ts)) continue;
    if (!oldest || ts < oldest.ts) oldest = { task, iso, ts };
  }
  return oldest ? { task: oldest.task, iso: oldest.iso } : null;
}

// Humanizes a SignalItem.meta key ("fii_dii_flow_cr" -> "Fii Dii Flow Cr") for
// the per-signal tooltip below — this is diagnostic context (why a signal
// scored the way it did: RSI value, FII/DII flow, repo rate, CPI, ...) that
// was previously fetched but never surfaced anywhere in the UI.
export function humanizeMetaKey(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

export function formatMetaValue(value: number | string | null): string {
  if (value == null) return '—';
  return typeof value === 'number' ? fmt(value, Number.isInteger(value) ? 0 : 2) : value;
}

export function fmtRatio(raw: string): string {
  const s = String(raw).trim();
  if (/[a-zA-Z%]/.test(s)) return s;
  const n = parseFloat(s.replace(/,/g, ''));
  if (!isNaN(n) && isFinite(n)) return fmt(n, s.includes('.') ? 2 : 0);
  return s;
}

// Every card that renders a scraped/LLM-sourced link (news articles, filing
// attachments, market-picks source headlines, Screener concall links, ...)
// used to pass its URL straight into an <a href> with no validation, while
// street-consensus-card.tsx's NumericConsensusRow alone checked its own
// (Trendlyne-specific) URL against a host prefix before rendering it as a
// link. None of these fields are user-typed, but they ARE scraped/LLM-
// extracted third-party text this codebase never fully trusts elsewhere
// (see e.g. the "never invent" convention) — a compromised/malicious source
// article, or an LLM extraction bug, returning a `javascript:`/`data:` URI
// would otherwise render as a clickable link a browser executes on click.
// This is the shared, scheme-only equivalent of that same discipline: only
// http(s) URLs are ever rendered as a real href, everything else renders as
// plain (unclickable) text via the caller checking this function's result.
export function safeExternalHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  return /^https?:\/\//i.test(url) ? url : undefined;
}
