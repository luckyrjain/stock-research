'use client';

// Small, generic UI atoms shared by every filterable/sortable stock table in
// this app — extracted out of app/screener/page.tsx and app/sme-signals/page.tsx,
// which had each independently implemented byte-identical (or near-identical)
// copies of these. Deliberately NOT a shared full table component: the two
// pages' row shapes are genuinely different (SME Signals has expandable
// EMA-chart rows with 12 columns; Screener is a flat 10-column table), so
// forcing one supercomponent over both would trade a real duplication
// problem for a worse one — a single component with two divergent, mutually
// exclusive rendering paths. These atoms are where the two pages actually
// were identical.

export function Skeleton({ className }: { className: string }) {
  return <div className={`bg-border/60 rounded animate-pulse ${className}`} />;
}

export function FilterChip<T extends string | number>({
  value, active, onClick, label,
}: { value: T; active: boolean; onClick: (v: T) => void; label: string }) {
  // `font-bold` on the active chip, `font-semibold` on the rest: since `muted`
  // was raised to #8093bd for AA it is now *brighter* than `accent` (luminance
  // .291 vs .240), so `text-accent` on `bg-accent/15` (4.07:1) reads quieter
  // than `text-muted` on `bg-surface` (5.77:1) and selection was left carried
  // by hue alone. Weight restores a second channel without moving either token.
  return (
    <button
      onClick={() => onClick(value)}
      aria-pressed={active}
      className={`px-3 py-1.5 rounded-full text-[11px] border transition-colors
        ${active
          ? 'bg-accent/15 border-accent/40 text-accent font-bold'
          : 'bg-surface border-border text-muted font-semibold hover:text-tx hover:border-border-hi'}`}
    >
      {label}
    </button>
  );
}

export function SortableTh<K extends string>({ label, sortK, currentKey, currentDir, onSort, align = 'left' }: {
  label: string;
  sortK: K;
  currentKey: K | null;
  currentDir: 'asc' | 'desc';
  onSort: (k: K) => void;
  align?: 'left' | 'right';
}) {
  const active = currentKey === sortK;
  return (
    <th
      aria-sort={active ? (currentDir === 'desc' ? 'descending' : 'ascending') : 'none'}
      className={`p-0 text-[10px] font-bold text-muted uppercase tracking-wider whitespace-nowrap ${align === 'right' ? 'text-right' : 'text-left'}`}
    >
      <button
        type="button"
        onClick={() => onSort(sortK)}
        className={`inline-flex items-center gap-1 px-4 py-3 uppercase tracking-wider
                   cursor-pointer hover:text-tx transition-colors select-none group
                   ${align === 'right' ? 'flex-row-reverse' : ''}`}
      >
        {label}
        <span className={`text-[9px] transition-colors ${active ? 'text-accent' : 'text-muted/25 group-hover:text-muted/60'}`}>
          {active ? (currentDir === 'desc' ? '↓' : '↑') : '↕'}
        </span>
      </button>
    </th>
  );
}

export function fmtMarketCap(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1_000) return `₹${(v / 1_000).toFixed(2)}k Cr`;
  return `₹${v.toFixed(0)} Cr`;
}
