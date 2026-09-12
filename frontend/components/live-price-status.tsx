'use client';

// watchlist/page.tsx and portfolio/page.tsx each independently rendered this
// exact "LTP / stale" indicator over useLivePrices()' updatedAt/stale pair
// (design.md's five-states rule: a poll that's been failing must not render
// identically to one that's fresh). positions-strip.tsx shows a different,
// more compact stale note inline in its own header and isn't forced into
// this shape.
export function LivePriceStatus({ updatedAt, stale }: { updatedAt: Date | null; stale: boolean }) {
  if (!updatedAt) return null;
  const time = updatedAt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
  return (
    <div className="flex justify-end mb-2">
      {stale ? (
        <span className="flex items-center gap-1 text-[10px] text-hold/80" title="The live-price refresh has been failing — prices below may be outdated">
          <span className="w-1.5 h-1.5 rounded-full bg-hold inline-block" />
          Prices may be outdated · last updated {time} IST
        </span>
      ) : (
        <span className="flex items-center gap-1 text-[10px] text-buy/70">
          <span className="w-1.5 h-1.5 rounded-full bg-buy animate-pulse inline-block" />
          LTP {time} IST
        </span>
      )}
    </div>
  );
}
