'use client';

import { useCallback, useEffect, useState } from 'react';

export interface WatchlistItem {
  symbol: string;
  company: string;
  exchange: string;
  addedAt: number;
}

const STORAGE_KEY = 'alphapulse_watchlist';
const CHANGE_EVENT = 'alphapulse:watchlist-changed';

function readAll(): WatchlistItem[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeAll(items: WatchlistItem[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // localStorage unavailable (private browsing quota, etc.) — fail silently,
    // the watchlist just won't persist for this session.
  }
  // The native `storage` event only fires in OTHER tabs/windows, not the tab
  // that made the change — dispatch our own so every mounted useWatchlist()
  // instance on THIS page updates immediately (e.g. star buttons in three
  // different dashboards all reflecting the same toggle).
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

/** Read-only snapshot, for one-off reads outside React (e.g. the /watchlist page's initial fetch). */
export function getWatchlist(): WatchlistItem[] {
  return readAll();
}

/** Shared cross-component watchlist state, backed by localStorage. No account or
 * backend involved — this is Phase 1 of the cross-mode watchlist; syncing across
 * devices would need real accounts, which is a separate, larger decision. */
export function useWatchlist() {
  const [items, setItems] = useState<WatchlistItem[]>([]);

  useEffect(() => {
    setItems(readAll());
    const onChange = () => setItems(readAll());
    window.addEventListener(CHANGE_EVENT, onChange);
    window.addEventListener('storage', onChange);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener('storage', onChange);
    };
  }, []);

  const isWatched = useCallback(
    (symbol: string) => items.some(i => i.symbol === symbol.toUpperCase()),
    [items],
  );

  const toggle = useCallback((item: { symbol: string; company: string; exchange: string }) => {
    const symbol = item.symbol.toUpperCase();
    const current = readAll();
    const next = current.some(i => i.symbol === symbol)
      ? current.filter(i => i.symbol !== symbol)
      : [...current, { symbol, company: item.company, exchange: item.exchange, addedAt: Date.now() }];
    writeAll(next);
  }, []);

  const remove = useCallback((symbol: string) => {
    writeAll(readAll().filter(i => i.symbol !== symbol.toUpperCase()));
  }, []);

  return { items, isWatched, toggle, remove };
}
