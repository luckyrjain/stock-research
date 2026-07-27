'use client';

import { useEffect, useState } from 'react';
import type { WatchlistCalendarEntry } from '@/types';
import { useWatchlist } from './watchlist';

/** Whether any currently-watched symbol has a same-day recommendation change
 * or notable price move — the same two conditions watchlist_alerts.py's
 * daily digest email already computes (via GET /api/watchlist/calendar,
 * verdict_history.detect_recent_changes()), surfaced here as a passive
 * nav-link badge instead of only ever being visible to a user who happens
 * to be on /watchlist itself or waits for the email. Previously a signed-
 * in user watching several stocks had no ambient signal that something
 * changed unless they remembered to check.
 *
 * Deliberately not gated on sign-in — the underlying detection works for
 * any watched symbol regardless of client_id-vs-account ownership, so an
 * anonymous browser gets the same visual signal even though it has no
 * email channel to fall back on. */
export function useWatchlistAlertsBadge(): boolean {
  const { items } = useWatchlist();
  const [hasAlerts, setHasAlerts] = useState(false);

  const symbolsKey = items.map(i => i.symbol).sort().join(',');

  useEffect(() => {
    if (!symbolsKey) {
      setHasAlerts(false);
      return;
    }
    let cancelled = false;
    fetch(`/api/watchlist/calendar?symbols=${encodeURIComponent(symbolsKey)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: { entries: WatchlistCalendarEntry[] } | null) => {
        if (cancelled || !data) return;
        setHasAlerts(data.entries.some(e => e.recommendation_change || e.price_move));
      })
      .catch(() => { if (!cancelled) setHasAlerts(false); });
    return () => { cancelled = true; };
  }, [symbolsKey]);

  return hasAlerts;
}
