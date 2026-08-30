'use client';

import { useEffect, useState } from 'react';

// financial-statements-card.tsx::useFinancials, peer-comparison-card.tsx::
// usePeerComparison, insider-activity-card.tsx::useInsiderActivity, and
// street-consensus-card.tsx::useStreetConsensus each independently built a
// byte-for-byte identical fetch-lifecycle hook: reset state on symbol
// change, fetch, map a non-ok response to null, and guard against a
// stale response landing after the component unmounted or the symbol
// changed again. This is the one implementation; each of those 4 keeps a
// thin named wrapper delegating here (e.g. `useFinancials(symbol) {
// return useSymbolResource<FinancialStatementsResponse>(symbol,
// 'financials'); }`) so call sites stay self-documenting and each card
// file keeps ownership of its own response type.
export function useSymbolResource<T>(symbol: string, slug: string): T | null {
  const [data, setData] = useState<T | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    fetch(`/api/${slug}/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((d: T | null) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [symbol, slug]);

  return data;
}
