'use client';

import type { Report } from '@/types';

type Direction = 'lower' | 'higher' | 'neutral';

interface CoreMetric {
  key: string;
  label: string;
  direction: Direction;
  get: (r: Report) => number | null;
  fmt: (v: number) => string;
}

function fmtCr(n: number): string {
  if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(2)}L Cr`;
  if (n >= 1_000)    return `₹${(n / 1_000).toFixed(2)}K Cr`;
  return `₹${n.toFixed(0)} Cr`;
}

// Direction is only asserted for metrics with an unambiguous, well-known
// reading (a lower P/E is "cheaper", a higher signal score is "more
// bullish") — the review this shipped from called out that /compare wasn't
// really a comparison tool (two stacked reports, no diff highlighting).
// Getting "which number is better" wrong would be worse than not answering
// it, so this list is deliberately short and each direction is documented.
const CORE_METRICS: CoreMetric[] = [
  { key: 'pe',     label: 'P/E (cheaper)',    direction: 'lower',  fmt: v => v.toFixed(1),       get: r => r.stock_info?.pe_ratio ?? null },
  { key: 'pb',     label: 'P/B (cheaper)',    direction: 'lower',  fmt: v => v.toFixed(1),       get: r => r.stock_info?.price_to_book ?? null },
  { key: 'eps',    label: 'EPS',              direction: 'higher', fmt: v => `₹${v.toFixed(2)}`, get: r => r.stock_info?.eps ?? null },
  { key: 'mcap',   label: 'Market Cap',       direction: 'higher', fmt: fmtCr,                    get: r => r.stock_info?.market_cap_cr ?? null },
  { key: 'div',    label: 'Dividend Yield',   direction: 'higher', fmt: v => `${v.toFixed(2)}%`, get: r => r.stock_info?.dividend_yield_pct ?? null },
  { key: 'beta',   label: 'Beta',             direction: 'neutral',fmt: v => v.toFixed(2),       get: r => r.stock_info?.beta ?? null },
  { key: 'signal', label: 'Signal Score (bullishness)', direction: 'higher', fmt: v => v.toFixed(2), get: r => r.signals?.final_score ?? null },
];

// Best-effort direction hint for the dynamic research.ratios dict, whose key
// set varies per company (Screener's own ratio labels, not a fixed schema).
// A key that doesn't match any hint gets no highlight at all — "better"
// isn't assumed for a ratio this app doesn't recognize, same "never invent a
// judgment" instinct as everywhere else in this codebase.
const _LOWER_BETTER_HINTS = ['p/e', 'price to earning', 'p/b', 'price to book', 'debt'];
const _HIGHER_BETTER_HINTS = ['roce', 'roe', 'margin', 'growth', 'yield', 'eps', 'sales', 'profit'];

function ratioDirection(label: string): Direction {
  const lower = label.toLowerCase();
  // A "coverage" ratio (Interest Coverage, Debt Service Coverage) is always
  // higher-is-better even though its label usually also contains "debt" —
  // checked first so the generic lower-better "debt" hint below doesn't win.
  if (lower.includes('coverage')) return 'higher';
  if (_LOWER_BETTER_HINTS.some(h => lower.includes(h))) return 'lower';
  if (_HIGHER_BETTER_HINTS.some(h => lower.includes(h))) return 'higher';
  return 'neutral';
}

function parseRatioValue(raw: string): number | null {
  const n = parseFloat(String(raw).replace(/,/g, ''));
  return Number.isFinite(n) ? n : null;
}

const REC_CLS: Record<string, string> = {
  BUY:  'bg-buy/12 text-buy border-buy/25',
  HOLD: 'bg-hold/12 text-hold border-hold/25',
  SELL: 'bg-sell/12 text-sell border-sell/25',
};

function RecBadge({ rec }: { rec?: string }) {
  if (!rec) return <span className="text-muted text-xs">—</span>;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border ${REC_CLS[rec] ?? 'bg-muted/10 text-muted border-muted/20'}`}>
      {rec}
    </span>
  );
}

function winnerSide(a: number | null, b: number | null, direction: Direction): 'a' | 'b' | null {
  if (a == null || b == null || a === b || direction === 'neutral') return null;
  // "Lower is better" only holds for a ratio that's meaningfully a cheapness
  // measure when positive (P/E, P/B, a debt ratio) — a negative P/E signals
  // a loss-making company, not a "cheap" one, so a negative value here would
  // otherwise rank as falsely "cheaper" than a positive peer's. No highlight
  // rather than a backwards one.
  if (direction === 'lower' && (a < 0 || b < 0)) return null;
  if (direction === 'lower') return a < b ? 'a' : 'b';
  return a > b ? 'a' : 'b';
}

function DiffRow({ label, aValue, bValue, winner }: {
  label: string; aValue: React.ReactNode; bValue: React.ReactNode; winner: 'a' | 'b' | null;
}) {
  const cellCls = (side: 'a' | 'b') =>
    `px-3 py-2 text-right font-mono text-sm ${winner === side ? 'text-buy font-semibold' : 'text-tx'}`;
  return (
    <tr className="border-b border-border/60 last:border-0">
      <td className="px-3 py-2 text-xs text-muted whitespace-nowrap">{label}</td>
      <td className={cellCls('a')}>{aValue}</td>
      <td className={cellCls('b')}>{bValue}</td>
    </tr>
  );
}

// Metric-by-metric diff above the two stacked ResultsDashboard columns —
// closes the "Compare isn't actually a comparison" gap the design review
// flagged: previously this page was two independent full reports with no
// synchronized read of which stock wins on which metric. Uses only data
// both columns already fetch; no new backend call.
export default function CompareDiffTable({ reportA, reportB }: { reportA: Report; reportB: Report }) {
  const commonRatioLabels = Object.keys(reportA.research?.ratios ?? {}).filter(
    k => (reportB.research?.ratios ?? {})[k] !== undefined,
  );

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden mb-8">
      <div className="px-4 pt-4 pb-2">
        <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase">
          Head to Head
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th className="px-3 py-2 text-left text-[10px] font-bold text-muted uppercase tracking-wider w-1/3">Metric</th>
              <th className="px-3 py-2 text-right text-xs font-mono font-bold text-tx">{reportA.symbol}</th>
              <th className="px-3 py-2 text-right text-xs font-mono font-bold text-tx">{reportB.symbol}</th>
            </tr>
          </thead>
          <tbody>
            <DiffRow
              label="Verdict"
              aValue={<RecBadge rec={reportA.analysis?.recommendation} />}
              bValue={<RecBadge rec={reportB.analysis?.recommendation} />}
              winner={null}
            />
            <DiffRow
              label="Confidence"
              aValue={reportA.analysis?.confidence ?? '—'}
              bValue={reportB.analysis?.confidence ?? '—'}
              winner={null}
            />
            {CORE_METRICS.map(m => {
              const a = m.get(reportA);
              const b = m.get(reportB);
              return (
                <DiffRow
                  key={m.key}
                  label={m.label}
                  aValue={a != null ? m.fmt(a) : '—'}
                  bValue={b != null ? m.fmt(b) : '—'}
                  winner={winnerSide(a, b, m.direction)}
                />
              );
            })}
            {commonRatioLabels.map(label => {
              const rawA = reportA.research!.ratios![label];
              const rawB = reportB.research!.ratios![label];
              const a = parseRatioValue(rawA);
              const b = parseRatioValue(rawB);
              const direction = ratioDirection(label);
              return (
                <DiffRow
                  key={label}
                  label={label}
                  aValue={rawA}
                  bValue={rawB}
                  winner={a != null && b != null ? winnerSide(a, b, direction) : null}
                />
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-muted/60 px-3 py-2 leading-relaxed">
        Green marks the stronger side only for metrics with an unambiguous reading (lower P/E and
        P/B read as cheaper; higher EPS, market cap, yield, signal score, and growth/margin ratios
        read as stronger). Everything else is shown side by side without a preference.
      </p>
    </div>
  );
}
