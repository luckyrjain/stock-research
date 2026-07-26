// Small, pure formatting helpers shared across several ResultsDashboard cards.
// Anything used by only one card lives with that card instead (see e.g.
// insider-activity-card.tsx's fmtActivityDate, street-consensus-card.tsx's
// fmtConsensusDate).

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
