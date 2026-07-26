'use client';

import { useCallback, useEffect, useState } from 'react';
import { getClientId } from '@/lib/watchlist';

export interface Position {
  symbol: string;
  company: string;
  exchange: string;
  entry_price: number | null;
  target_price: number | null;
  stop_loss: number | null;
  // User-entered, not scraped/computed — unlike every other "never invent"
  // field in this codebase, there's no source of truth to guess this from at
  // all, so it's simply null until the user fills it in on the Portfolio
  // page. null (not 0) means "unknown," never "zero shares held."
  shares: number | null;
  bought_at: string;   // ISO timestamp, set when the user marks the pick as bought
}

// Module-level shared cache + generation counter, same pattern as
// lib/watchlist.ts's useWatchlist() — this used to be a pure-localStorage
// feature (no backend, no accounts) before Positions got the same
// account-aware treatment Watchlist already had. `getClientId()` is reused
// directly from watchlist.ts rather than duplicated: it's the same opaque
// per-browser identifier grouping one browser's anonymous rows in Postgres,
// not something specific to either feature.
let cachedPositions: Position[] | null = null;
let inFlight: Promise<Position[] | null> | null = null;
// Bumped by refreshPositions() so a fetch already in flight when the
// caller's identity changes (sign-in/sign-out) can't clobber the fresher
// result if it resolves after the refresh's own fetch — same fix as
// lib/watchlist.ts's fetchItems()/refreshWatchlist().
let generation = 0;
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach(fn => fn());
}

async function fetchPositions(): Promise<Position[]> {
  const myGeneration = generation;
  if (!inFlight) {
    const clientId = getClientId();
    inFlight = fetch(`/api/positions?client_id=${encodeURIComponent(clientId)}`, { cache: 'no-store' })
      .then(res => (res.ok ? res.json() : { items: null }))
      .then((data: { items?: Position[] | null }) => data.items ?? null)
      .catch(() => null)
      .finally(() => { inFlight = null; });
  }
  const items = await inFlight;
  if (myGeneration !== generation) {
    return cachedPositions ?? [];
  }
  cachedPositions = items ?? cachedPositions ?? [];
  notify();
  return cachedPositions;
}

/** Re-fetches /api/positions and updates every subscribed usePositions()
 * instance — call after sign-in/sign-out so positions switch between the
 * account's rows and the anonymous client_id's rows without a full page
 * reload, same reasoning as refreshWatchlist(). */
export function refreshPositions(): Promise<Position[]> {
  generation++;
  inFlight = null;
  return fetchPositions();
}

/** Opt-in migration of the anonymous browser's positions onto the account
 * that just signed in — same escape hatch as lib/watchlist.ts's
 * claimWatchlist(), for the "no migration on sign-in" default. Must be
 * called with a session cookie already set; returns null on any failure. */
export async function claimPositions(clientId: string): Promise<{ claimed: number; skippedOverCap: number } | null> {
  try {
    const res = await fetch('/api/positions/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: clientId }),
    });
    if (!res.ok) return null;
    const data = await res.json() as { claimed: number; skipped_over_cap: number; items: Position[] };
    generation++;
    inFlight = null;
    cachedPositions = data.items;
    notify();
    return { claimed: data.claimed, skippedOverCap: data.skipped_over_cap };
  } catch {
    return null;
  }
}

/** Shared cross-component "I bought this" position tracking, backed by
 * Postgres (positions table, keyed by the same anonymous client_id as the
 * watchlist, or an account's user_id once signed in — see CLAUDE.md's
 * "Positions" section). entry/target/stop are captured at mark-time from
 * the live MarketPick the user acted on, so P&L math stays correct even if
 * a later pipeline run changes those levels. */
export function usePositions() {
  const [positions, setPositions] = useState<Position[]>(cachedPositions ?? []);
  const [loading, setLoading] = useState(cachedPositions === null);

  useEffect(() => {
    const onChange = () => setPositions(cachedPositions ?? []);
    listeners.add(onChange);
    if (cachedPositions === null) {
      fetchPositions().finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    return () => { listeners.delete(onChange); };
  }, []);

  const isPositioned = useCallback(
    (symbol: string) => positions.some(p => p.symbol === symbol.toUpperCase()),
    [positions],
  );

  const addPosition = useCallback(async (pos: Omit<Position, 'bought_at' | 'shares'>) => {
    const symbol = pos.symbol.toUpperCase();
    const clientId = getClientId();
    try {
      const res = await fetch('/api/positions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId, ...pos, symbol }),
      });
      if (!res.ok) return;
      const data = await res.json() as { items: Position[] };
      cachedPositions = data.items;
      notify();
    } catch {
      // Backend unreachable — leave state as-is, same convention as
      // useWatchlist()'s toggle()/remove().
    }
  }, []);

  const removePosition = useCallback(async (symbol: string) => {
    const clientId = getClientId();
    try {
      const res = await fetch(`/api/positions/${encodeURIComponent(symbol.toUpperCase())}?client_id=${encodeURIComponent(clientId)}`, {
        method: 'DELETE',
      });
      if (!res.ok) return;
      const data = await res.json() as { items: Position[] };
      cachedPositions = data.items;
      notify();
    } catch {
      // silently ignore — the row just won't disappear; user can retry
    }
  }, []);

  // Filled in after the fact, typically from the Portfolio page — asking for
  // a share count at "I bought this" click-time would add friction to what's
  // meant to be a one-click action while browsing Market Picks.
  const updateShares = useCallback(async (symbol: string, shares: number | null) => {
    const clientId = getClientId();
    try {
      const res = await fetch(`/api/positions/${encodeURIComponent(symbol.toUpperCase())}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId, shares }),
      });
      if (!res.ok) return;
      const data = await res.json() as { items: Position[] };
      cachedPositions = data.items;
      notify();
    } catch {
      // silently ignore — same "leave state as-is on a failed mutation"
      // convention as every other write in this hook
    }
  }, []);

  return { positions, loading, isPositioned, addPosition, removePosition, updateShares };
}
