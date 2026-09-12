// Shared logic for the app's two custom SVG charts (sparkline.tsx, ema-chart.tsx) —
// no charting library per frontend/CLAUDE.md, so both hand-roll hover tracking
// and date formatting. Extracted here since both implemented the same shapes.

/**
 * Finds the index of the data point nearest a mouse event's x-coordinate,
 * for crosshair/tooltip hover. `xAt(i)` returns point i's x position in the
 * SVG's own viewBox units (a chart may precompute these or compute lazily);
 * `viewBoxWidth` is the viewBox width used to convert the mouse event's
 * page-space clientX into that same coordinate space.
 */
export function nearestIndexByX(
  clientX: number,
  svg: SVGSVGElement,
  viewBoxWidth: number,
  count: number,
  xAt: (i: number) => number,
): number {
  const rect = svg.getBoundingClientRect();
  const relX = ((clientX - rect.left) / rect.width) * viewBoxWidth;
  let nearest = 0;
  let best = Infinity;
  for (let i = 0; i < count; i++) {
    const d = Math.abs(xAt(i) - relX);
    if (d < best) { best = d; nearest = i; }
  }
  return nearest;
}

/**
 * Formats a bare 'YYYY-MM-DD' string for display. A bare date string parses
 * as UTC midnight (per the Date spec), but toLocaleDateString() without an
 * explicit timeZone renders in the browser's LOCAL timezone — for any
 * visitor west of UTC, that silently shifts the displayed date back by one
 * day (e.g. "2026-07-27" renders as "26 Jul" for a Pacific-time visitor).
 * Pinning timeZone: 'UTC' here makes the render match the UTC calendar date
 * the string actually encodes, regardless of the viewer's own timezone.
 *
 * Callers with non-date strings mixed into the same field (e.g. sparkline.tsx's
 * quarter labels) must guard before calling this — it assumes `d` is a date.
 */
export function formatUtcDate(d: string): string {
  const parsed = new Date(d);
  if (Number.isNaN(parsed.getTime())) return d;
  return parsed.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}
