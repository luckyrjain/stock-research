'use client';

import { useCallback } from 'react';
import { useToast } from '@/components/toast';
import { createSharedResource } from '@/lib/shared-resource';

export interface WatchlistItem {
  symbol: string;
  company: string;
  exchange: string;
  addedAt: string;
}

const CLIENT_ID_KEY = 'alphapulse_client_id';

// Fallback when localStorage.setItem fails (quota exceeded, private
// browsing, or blocked by browser/extension policy) -- without this, every
// call to getClientId() would mint a brand-new UUID (since
// localStorage.getItem keeps returning null), so a write made under one
// generated id would never be found again by the very next read. Keeping it
// in memory for the rest of this page session means every call at least
// agrees with itself, even though it still won't survive a reload.
let inMemoryClientId: string | null = null;

/** Opaque per-browser identifier — NOT an account. There is no login; this is
 * just how the backend groups one browser's watchlist rows in Postgres. See
 * db/models.py's watchlist_items table for the server side of this. */
export function getClientId(): string {
  if (typeof window === 'undefined') return '';
  let id = window.localStorage.getItem(CLIENT_ID_KEY);
  if (!id) {
    id = inMemoryClientId ?? crypto.randomUUID();
    try {
      window.localStorage.setItem(CLIENT_ID_KEY, id);
    } catch {
      inMemoryClientId = id;
    }
  }
  return id;
}

// Shared cross-component cache: every mounted useWatchlist() instance on a
// page (one per star button, potentially dozens on the Market Picks table)
// reads from and subscribes to this single resource instead of each
// independently fetching /api/watchlist on mount. See lib/shared-resource.ts
// for the cache/inFlight/generation/listeners machinery this builds on —
// the generation fencing matters here just as much as in lib/auth.ts's
// fetchMe()/refreshAuth(): on logout, a stale account-scoped response
// arriving late would otherwise briefly re-display the signed-out-from
// account's rows, not just show outdated data.
async function fetchItems(previous: WatchlistItem[] | undefined): Promise<WatchlistItem[]> {
  const clientId = getClientId();
  // client_id is always sent, but the backend transparently prefers a
  // valid account session over it when one is present (see api.py's
  // _resolve_watchlist_owner) — this fetch doesn't need to know which
  // identity actually served the request.
  const items = await fetch(`/api/watchlist?client_id=${encodeURIComponent(clientId)}`, { cache: 'no-store' })
    .then(res => (res.ok ? res.json() : { items: null }))
    .then((data: { items?: WatchlistItem[] | null }) => data.items ?? null)
    .catch(() => null);
  // A failure (items === null) leaves the previous value in place rather
  // than wiping a populated watchlist to empty, matching toggle()/remove()
  // below's "leave state as-is" convention on a failed mutation.
  return items ?? previous ?? [];
}

const resource = createSharedResource<WatchlistItem[]>(fetchItems, []);

/** Re-fetches /api/watchlist and updates every subscribed useWatchlist()
 * instance — call after sign-in/sign-out so the watchlist switches between
 * the account's rows and the anonymous client_id's rows without a full page
 * reload (the shared cache above otherwise has no way to know the caller's
 * identity changed). */
export function refreshWatchlist(): Promise<WatchlistItem[]> {
  return resource.refresh();
}

export interface ClaimResult {
  claimed: number;
  skippedOverCap: number;
}

/** Opt-in migration of the anonymous browser's watchlist onto the account
 * that just signed in — the escape hatch for this app's deliberate "no
 * migration on sign-in" default (see CLAUDE.md's "Watchlist flow"). Must be
 * called with a session cookie already set (see app/auth/verify/page.tsx,
 * the one caller) — the backend requires it and returns 401 without one.
 * Returns null on any failure (network, 401, backend down) so the caller
 * can show a generic "couldn't claim" message rather than crashing the
 * sign-in flow over a non-critical step. */
export async function claimWatchlist(clientId: string): Promise<ClaimResult | null> {
  try {
    const res = await fetch('/api/watchlist/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: clientId }),
    });
    if (!res.ok) return null;
    const data = await res.json() as { claimed: number; skipped_over_cap: number; items: WatchlistItem[] };
    resource.bumpGeneration();
    resource.setCache(data.items);
    return { claimed: data.claimed, skippedOverCap: data.skipped_over_cap };
  } catch {
    return null;
  }
}

/** Shared cross-component watchlist state, backed by Postgres (watchlist_items,
 * keyed by an anonymous per-browser client_id — see getClientId above) or, once
 * signed in, an account (see refreshWatchlist above and CLAUDE.md's "Watchlist
 * flow"). The hook itself doesn't need to know which identity is active — the
 * backend resolves that per request. */
export function useWatchlist() {
  const { showError } = useToast();
  const { value: items, loading } = resource.useValue();

  const isWatched = useCallback(
    (symbol: string) => items.some(i => i.symbol === symbol.toUpperCase()),
    [items],
  );

  const toggle = useCallback(async (item: { symbol: string; company: string; exchange: string }) => {
    const symbol = item.symbol.toUpperCase();
    const clientId = getClientId();
    const currentlyWatched = (resource.getCache() ?? []).some(i => i.symbol === symbol);

    if (currentlyWatched) {
      await resource.mutate(async () => {
        try {
          const res = await fetch(`/api/watchlist/${encodeURIComponent(symbol)}?client_id=${encodeURIComponent(clientId)}`, {
            method: 'DELETE',
          });
          if (!res.ok) { showError("Couldn't update your watchlist — try again."); return undefined; }
          const data = await res.json() as { items: WatchlistItem[] };
          return data.items;
        } catch {
          // Backend unreachable — leave state as-is rather than optimistically
          // flipping the star to something that didn't actually save.
          showError("Couldn't reach the server — your watchlist wasn't updated.");
          return undefined;
        }
      });
    } else {
      await resource.mutate(async () => {
        try {
          const res = await fetch('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_id: clientId, symbol, company: item.company, exchange: item.exchange }),
          });
          if (!res.ok) { showError("Couldn't update your watchlist — try again."); return undefined; }
          const data = await res.json() as { items: WatchlistItem[] };
          return data.items;
        } catch {
          showError("Couldn't reach the server — your watchlist wasn't updated.");
          return undefined;
        }
      });
    }
  }, [showError]);

  const remove = useCallback(async (symbol: string) => {
    const clientId = getClientId();
    await resource.mutate(async () => {
      try {
        const res = await fetch(`/api/watchlist/${encodeURIComponent(symbol.toUpperCase())}?client_id=${encodeURIComponent(clientId)}`, {
          method: 'DELETE',
        });
        if (!res.ok) { showError("Couldn't remove from your watchlist — try again."); return undefined; }
        const data = await res.json() as { items: WatchlistItem[] };
        return data.items;
      } catch {
        // silently ignore in state — the row just won't disappear; user can retry
        showError("Couldn't reach the server — try again.");
        return undefined;
      }
    });
  }, [showError]);

  return { items, loading, isWatched, toggle, remove };
}
