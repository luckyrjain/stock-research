import type { SmeSignalHistoryRow } from '@/types';

interface Props {
  series: SmeSignalHistoryRow[];
  width?: number;
  height?: number;
}

// Vector SVG chart (no external chart lib, per design.md) showing close price,
// EMA20, and EMA50 over the stored window, with markers on cross days.
export default function EmaChart({ series, width = 640, height = 220 }: Props) {
  const usable = series.filter(r => r.close_price != null || r.ema20 != null || r.ema50 != null);
  if (usable.length < 2) {
    return <p className="text-xs text-muted py-8 text-center">Not enough history to chart.</p>;
  }

  const pad = 10;
  const values = usable.flatMap(r => [r.close_price, r.ema20, r.ema50]).filter((v): v is number => v != null);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const xFor = (i: number) => pad + (i / (usable.length - 1)) * (width - pad * 2);
  const yFor = (v: number) => pad + (1 - (v - min) / range) * (height - pad * 2);

  const lineFor = (key: 'close_price' | 'ema20' | 'ema50') =>
    usable
      .map((r, i) => (r[key] != null ? `${xFor(i).toFixed(1)},${yFor(r[key] as number).toFixed(1)}` : null))
      .filter((p): p is string => p != null)
      .join(' ');

  const crossMarkers = usable
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => r.cross && r.ema20 != null);

  return (
    <div>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" preserveAspectRatio="none">
        <polyline points={lineFor('close_price')} fill="none" stroke="currentColor" strokeWidth={1.25} className="text-muted/50" />
        <polyline points={lineFor('ema50')} fill="none" stroke="currentColor" strokeWidth={1.75} className="text-hold" />
        <polyline points={lineFor('ema20')} fill="none" stroke="currentColor" strokeWidth={1.75} className="text-accent" />
        {crossMarkers.map(({ r, i }) => (
          <circle
            key={r.trade_date}
            cx={xFor(i)}
            cy={yFor(r.ema20 as number)}
            r={4}
            className={r.cross === 'golden' ? 'fill-buy' : 'fill-sell'}
            stroke="currentColor"
            strokeWidth={1}
          />
        ))}
      </svg>
      <div className="flex items-center gap-4 mt-2 text-[10px] text-muted">
        <span className="flex items-center gap-1.5"><span className="w-2.5 h-0.5 bg-muted/50 inline-block" /> Close</span>
        <span className="flex items-center gap-1.5"><span className="w-2.5 h-0.5 bg-accent inline-block" /> EMA 20</span>
        <span className="flex items-center gap-1.5"><span className="w-2.5 h-0.5 bg-hold inline-block" /> EMA 50</span>
        <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-buy inline-block" /> Golden cross</span>
        <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-sell inline-block" /> Death cross</span>
      </div>
    </div>
  );
}
