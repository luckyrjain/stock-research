'use client';

import { useCallback, useEffect, useState } from 'react';

export interface WatchlistItem {
  symbol: string;
  company: string;
  exchange: string;
  addedAt: string;
}

const CLIENT_ID_KEY = 'alphapulse_client_id';

/** Opaque per-browser identifier — NOT an account. There is no login; this is
 * just how the backend groups one browser's watchlist rows in Postgres. See
 * db/models.py's watchlist_items table for the server side of this. */
export function getClientId(): string {
  if (typeof window === 'undefined') return '';
  let id = window.localStorage.getItem(CLIENT_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    try { window.localStorage.setItem(CLIENT_ID_KEY, id); } catch { /* private browsing, etc. */ }
  }
  return id;
}

// Module-level shared cache: every mounted useWatchlist() instance on a page
// (one per star button, potentially dozens on the Market Picks table) reads
// from and subscribes to this single cache instead of each independently
// fetching /api/watchlist on mount.
let cachedItems: WatchlistItem[] | null = null;
// Resolves to null on failure (never rejects) — null means "keep whatever
// cachedItems already had," not "the watchlist is empty."
let inFlight: Promise<WatchlistItem[] | null> | null = null;
// Bumped by refreshWatchlist() so a fetch already in flight when the
// caller's identity changes (e.g. a slow anonymous request still running at
// the moment of sign-in/sign-out) can't clobber the fresher result if it
// resolves after the refresh's own fetch — same fix as lib/auth.ts's
// fetchMe()/refreshAuth(), and just as important here: on logout, a stale
// account-scoped response arriving late would otherwise briefly re-display
// the signed-out-from account's rows, not just show outdated data.
let generation = 0;
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach(fn => fn());
}

async function fetchItems(): Promise<WatchlistItem[]> {
  const myGeneration = generation;
  if (!inFlight) {
    const clientId = getClientId();
    // client_id is always sent, but the backend transparently prefers a
    // valid account session over it when one is present (see api.py's
    // _resolve_watchlist_owner) — this fetch doesn't need to know which
    // identity actually served the request.
    inFlight = fetch(`/api/watchlist?client_id=${encodeURIComponent(clientId)}`, { cache: 'no-store' })
      .then(res => (res.ok ? res.json() : { items: null }))
      .then((data: { items?: WatchlistItem[] | null }) => data.items ?? null)
      .catch(() => null)
      .finally(() => { inFlight = null; });
  }
  const items = await inFlight;
  if (myGeneration !== generation) {
    // A refresh (identity change) superseded this fetch — its own, later
    // request owns the cache now; don't let a straggler overwrite it.
    return cachedItems ?? [];
  }
  // A failure (items === null) leaves the previous value in place rather
  // than wiping a populated watchlist to empty, matching toggle()/remove()
  // below's "leave state as-is" convention on a failed mutation.
  cachedItems = items ?? cachedItems ?? [];
  notify();
  return cachedItems;
}

/** Re-fetches /api/watchlist and updates every subscribed useWatchlist()
 * instance — call after sign-in/sign-out so the watchlist switches between
 * the account's rows and the anonymous client_id's rows without a full page
 * reload (the module-level cache above otherwise has no way to know the
 * caller's identity changed). */
export function refreshWatchlist(): Promise<WatchlistItem[]> {
  generation++;
  inFlight = null;
  return fetchItems();
}

/** Shared cross-component watchlist state, backed by Postgres (watchlist_items,
 * keyed by an anonymous per-browser client_id — see getClientId above) or, once
 * signed in, an account (see refreshWatchlist above and CLAUDE.md's "Watchlist
 * flow"). The hook itself doesn't need to know which identity is active — the
 * backend resolves that per request. */
export function useWatchlist() {
  const [items, setItems] = useState<WatchlistItem[]>(cachedItems ?? []);
  const [loading, setLoading] = useState(cachedItems === null);

  useEffect(() => {
    const onChange = () => setItems(cachedItems ?? []);
    listeners.add(onChange);
    if (cachedItems === null) {
      fetchItems().finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    return () => { listeners.delete(onChange); };
  }, []);

  const isWatched = useCallback(
    (symbol: string) => items.some(i => i.symbol === symbol.toUpperCase()),
    [items],
  );

  const toggle = useCallback(async (item: { symbol: string; company: string; exchange: string }) => {
    const symbol = item.symbol.toUpperCase();
    const clientId = getClientId();
    const currentlyWatched = (cachedItems ?? []).some(i => i.symbol === symbol);

    try {
      if (currentlyWatched) {
        const res = await fetch(`/api/watchlist/${encodeURIComponent(symbol)}?client_id=${encodeURIComponent(clientId)}`, {
          method: 'DELETE',
        });
        if (!res.ok) return;
        const data = await res.json() as { items: WatchlistItem[] };
        cachedItems = data.items;
      } else {
        const res = await fetch('/api/watchlist', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ client_id: clientId, symbol, company: item.company, exchange: item.exchange }),
        });
        if (!res.ok) return;
        const data = await res.json() as { items: WatchlistItem[] };
        cachedItems = data.items;
      }
      notify();
    } catch {
      // Backend unreachable — leave state as-is rather than optimistically
      // flipping the star to something that didn't actually save.
    }
  }, []);

  const remove = useCallback(async (symbol: string) => {
    const clientId = getClientId();
    try {
      const res = await fetch(`/api/watchlist/${encodeURIComponent(symbol.toUpperCase())}?client_id=${encodeURIComponent(clientId)}`, {
        method: 'DELETE',
      });
      if (!res.ok) return;
      const data = await res.json() as { items: WatchlistItem[] };
      cachedItems = data.items;
      notify();
    } catch {
      // silently ignore — the row just won't disappear; user can retry
    }
  }, []);

  return { items, loading, isWatched, toggle, remove };
}
