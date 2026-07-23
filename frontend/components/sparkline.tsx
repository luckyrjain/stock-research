interface Props {
  closes: number[];
  width?: number;
  height?: number;
  className?: string;
}

// Vector SVG sparkline per design.md §7 — stroke color tracks buy/sell (rising/falling).
export default function Sparkline({ closes, width = 96, height = 28, className = '' }: Props) {
  if (closes.length < 2) return null;

  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const rising = closes[closes.length - 1] >= closes[0];

  const points = closes
    .map((c, i) => {
      const x = (i / (closes.length - 1)) * width;
      const y = height - ((c - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={`${rising ? 'text-buy' : 'text-sell'} ${className}`}
      role="img"
      aria-label={`Price trend, ${rising ? 'up' : 'down'} over the shown period`}
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
