'use client';

import { useState } from 'react';
import type { SmeSignalHistoryRow } from '@/types';

interface Props {
  series: SmeSignalHistoryRow[];
  width?: number;
  height?: number;
}

function fmtDate(d: string): string {
  const parsed = new Date(d);
  if (Number.isNaN(parsed.getTime())) return d;
  return parsed.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function fmtVal(v: number | null): string {
  return v == null ? '—' : v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

// Vector SVG chart (no external chart lib, per design.md) showing close price,
// EMA20, and EMA50 over the stored window, with markers on cross days.
// Hover/tap shows a crosshair + tooltip with that day's date/close/EMA20/EMA50
// (and cross type, if that day was one) — same interaction sparkline.tsx
// already has; this was previously the one chart in the app without it.
export default function EmaChart({ series, width = 640, height = 220 }: Props) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
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

  function nearestIndex(clientX: number, svg: SVGSVGElement): number {
    const rect = svg.getBoundingClientRect();
    const relX = ((clientX - rect.left) / rect.width) * width;
    let nearest = 0;
    let best = Infinity;
    usable.forEach((_, i) => {
      const d = Math.abs(xFor(i) - relX);
      if (d < best) { best = d; nearest = i; }
    });
    return nearest;
  }

  const hovered = hoverIdx != null ? usable[hoverIdx] : null;
  // Clamp the tooltip's left offset the same way sparkline.tsx does, so it
  // doesn't run off either edge of the (much wider) EMA chart.
  const tooltipLeft = hoverIdx != null ? Math.max(60, Math.min(width - 60, xFor(hoverIdx))) : 0;

  return (
    <div className="relative">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto cursor-crosshair"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Close price, EMA20, and EMA50 over ${usable.length} trading days, with markers on golden/death cross days`}
        onMouseMove={(e) => setHoverIdx(nearestIndex(e.clientX, e.currentTarget))}
        onMouseLeave={() => setHoverIdx(null)}
      >
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
        {hoverIdx != null && (
          <line x1={xFor(hoverIdx)} y1={0} x2={xFor(hoverIdx)} y2={height} stroke="currentColor" strokeWidth={0.75} className="text-tx" opacity={0.35} />
        )}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute top-0 -translate-x-1/2 z-20 whitespace-nowrap
                     rounded-md border border-border bg-card px-2.5 py-1.5 text-[10px] font-mono text-tx shadow-lg shadow-black/40"
          style={{ left: `${(tooltipLeft / width) * 100}%` }}
        >
          <div className="font-sans text-muted/70 mb-0.5">{fmtDate(hovered.trade_date)}</div>
          <div className="text-muted/50">Close <span className="text-tx">{fmtVal(hovered.close_price)}</span></div>
          <div className="text-accent">EMA20 <span className="text-tx">{fmtVal(hovered.ema20)}</span></div>
          <div className="text-hold">EMA50 <span className="text-tx">{fmtVal(hovered.ema50)}</span></div>
          {hovered.cross && (
            <div className={`font-sans font-semibold mt-0.5 ${hovered.cross === 'golden' ? 'text-buy' : 'text-sell'}`}>
              {hovered.cross === 'golden' ? 'Golden cross' : 'Death cross'}
            </div>
          )}
        </div>
      )}
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
