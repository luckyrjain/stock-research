'use client';

import { useState } from 'react';

interface Props {
  closes: number[];
  dates?: string[];   // aligned with closes, oldest first — enables the hover tooltip
  width?: number;
  height?: number;
  className?: string;
  ariaLabel?: string;
  /** Formats the value shown in the hover tooltip (defaults to 2 decimal places). */
  formatValue?: (v: number) => string;
}

function defaultFormatValue(v: number): string {
  return v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

const _ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// Only reformats genuine 'YYYY-MM-DD' entries (the price-history series) —
// quarter labels like "Mar 2024" (QuarterlyTrend.quarters) are already
// human-readable and shown as-is rather than round-tripped through Date,
// which would silently reinterpret "Mar 2024" as a specific day.
function formatDate(d: string): string {
  if (!_ISO_DATE_RE.test(d)) return d;
  const parsed = new Date(d);
  if (Number.isNaN(parsed.getTime())) return d;
  // A bare 'YYYY-MM-DD' string parses as UTC midnight (per the Date spec),
  // but toLocaleDateString() without an explicit timeZone renders in the
  // browser's LOCAL timezone — for any visitor west of UTC, that silently
  // shifts the displayed date back by one day (e.g. "2026-07-27" renders as
  // "26 Jul" for a Pacific-time visitor). Pinning timeZone: 'UTC' here makes
  // the render match the UTC calendar date the string actually encodes,
  // regardless of the viewer's own timezone.
  return parsed.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

// Vector SVG sparkline per design.md §7 — stroke color tracks buy/sell (rising/falling).
// When `dates` is supplied, hovering/tapping the chart shows the date + value at
// that point — previously PriceHistory.dates was fetched everywhere this component
// was used but never actually read, so no chart in the app was inspectable.
export default function Sparkline({
  closes, dates, width = 96, height = 28, className = '', ariaLabel, formatValue = defaultFormatValue,
}: Props) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  if (closes.length < 2) return null;

  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const rising = closes[closes.length - 1] >= closes[0];
  const hoverable = !!dates && dates.length === closes.length;

  const coords = closes.map((c, i) => ({
    x: (i / (closes.length - 1)) * width,
    y: height - ((c - min) / range) * height,
  }));
  const points = coords.map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');

  function nearestIndex(clientX: number, svg: SVGSVGElement): number {
    const rect = svg.getBoundingClientRect();
    const relX = ((clientX - rect.left) / rect.width) * width;
    let nearest = 0;
    let best = Infinity;
    coords.forEach((c, i) => {
      const d = Math.abs(c.x - relX);
      if (d < best) { best = d; nearest = i; }
    });
    return nearest;
  }

  const hovered = hoverIdx != null ? coords[hoverIdx] : null;

  return (
    <span className="relative inline-block">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className={`${rising ? 'text-buy' : 'text-sell'} ${className} ${hoverable ? 'cursor-crosshair' : ''}`}
        role="img"
        aria-label={ariaLabel ?? `Price trend, ${rising ? 'up' : 'down'} over the shown period`}
        onMouseMove={hoverable ? (e) => setHoverIdx(nearestIndex(e.clientX, e.currentTarget)) : undefined}
        onMouseLeave={hoverable ? () => setHoverIdx(null) : undefined}
      >
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {hovered && (
          <>
            <line x1={hovered.x} y1={0} x2={hovered.x} y2={height} stroke="currentColor" strokeWidth={0.75} opacity={0.35} />
            <circle cx={hovered.x} cy={hovered.y} r={2.5} fill="currentColor" />
          </>
        )}
      </svg>
      {hoverable && hoverIdx != null && hovered && (
        <span
          className="pointer-events-none absolute bottom-full -translate-x-1/2 mb-1.5 z-20
                     whitespace-nowrap rounded-md border border-border bg-card px-2 py-1 text-[10px]
                     font-mono text-tx shadow-lg shadow-black/40"
          style={{ left: Math.max(20, Math.min(width - 20, hovered.x)) }}
        >
          {formatValue(closes[hoverIdx])}
          <span className="block text-muted/70 font-sans">{formatDate(dates![hoverIdx])}</span>
        </span>
      )}
    </span>
  );
}
