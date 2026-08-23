'use client';

import { useMemo } from 'react';
import type { MfHoldingsStakeDelta, Report } from '@/types';
import { oldestDataFreshness, normalizeRatioKey } from '@/lib/format';
import { usePeerComparison } from '@/components/peer-comparison-card';
import { useFinancials } from '@/components/financial-statements-card';
import { useStreetConsensus } from '@/components/street-consensus-card';

// Coordination layer for ResultsDashboard: owns the three symbol-scoped
// add-on fetches (peers/financials/street-consensus) plus the derived maps
// several child cards depend on, so the dashboard component itself stays a
// pure renderer. Each fetch hook already collapses its own loading state to
// `null` (see e.g. usePeerComparison) — preserved as-is here, not merged
// into one combined loading flag.
export function useResultsPageData(symbol: string, report: Report) {
  const peers = usePeerComparison(symbol);
  const financials = useFinancials(symbol);
  const streetConsensus = useStreetConsensus(symbol);

  const percentileByNormalizedKey = useMemo(() => {
    const map: Record<string, number> = {};
    for (const [key, value] of Object.entries(peers?.percentiles ?? {})) {
      map[normalizeRatioKey(key)] = value;
    }
    return map;
  }, [peers]);

  // The true bottleneck on "how fresh is everything on this page" — see
  // lib/format.ts::oldestDataFreshness. report.generated_at alone
  // (rendered below as formatAge) is stamped fresh on every report
  // assembly regardless of whether anything was actually refetched, so a
  // long-TTL task (shareholding/mf_holdings, 168h) could otherwise read as
  // "Updated today" while being up to a week stale.
  const oldestFreshness = useMemo(() => oldestDataFreshness(report.data_freshness), [report.data_freshness]);

  const mfDeltaByFund = useMemo(() => {
    const map: Record<string, MfHoldingsStakeDelta> = {};
    for (const d of report.mf_holdings_trend ?? []) {
      if (d.delta_pct != null) map[d.fund] = d;
    }
    return map;
  }, [report.mf_holdings_trend]);

  return { peers, financials, streetConsensus, percentileByNormalizedKey, oldestFreshness, mfDeltaByFund };
}
