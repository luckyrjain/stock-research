'use client';

import { useCallback, useEffect, useState } from 'react';

export interface Position {
  symbol: string;
  company: string;
  exchange: string;
  entry_price: number | null;
  target_price: number | null;
  stop_loss: number | null;
  // User-entered, not scraped/computed — unlike every other "never invent" field
  // in this codebase, there's no source of truth to guess this from at all, so
  // it's simply left null until the user fills it in on the Portfolio page.
  // null (not 0) means "unknown," never "zero shares held."
  shares: number | null;
  bought_at: string;   // ISO timestamp, set when the user marks the pick as bought
}

const STORAGE_KEY = 'alphapulse_positions';

// Module-level shared cache + listener set, same shared-state pattern as
// useWatchlist() (frontend/lib/watchlist.ts) — every mounted "I bought this"
// button reads from and subscribes to one cache instead of each re-parsing
// localStorage independently. Unlike the watchlist, there's no backend here
// at all (no accounts, no DB) — this is purely a per-browser localStorage
// list, so every read/write is synchronous.
let cachedPositions: Position[] | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach(fn => fn());
}

function loadFromStorage(): Position[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Position[]) : [];
  } catch {
    return [];
  }
}

function persist(positions: Position[]): void {
  cachedPositions = positions;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(positions));
  } catch {
    // Quota exceeded or private-browsing storage block — the in-memory cache
    // (and this tab's UI) still reflects the change even if it won't survive
    // a reload.
  }
  notify();
}

/** Shared cross-component "I bought this" position tracking, backed entirely
 * by localStorage — no accounts, no server round trip. entry/target/stop are
 * captured at mark-time from the live MarketPick the user acted on, so P&L
 * math stays correct even if a later pipeline run changes those levels.
 *
 * Initial render always starts from `cachedPositions ?? []` — never reads
 * localStorage synchronously during render — so the client's first paint
 * matches the server's (which has no localStorage access at all). The real
 * data loads in the effect below, same SSR-safe pattern useWatchlist() uses
 * for its own (network-backed) fetch. Reading localStorage during the
 * useState initializer would make the client's very first render diverge
 * from the server-rendered HTML and trigger a hydration mismatch. */
export function usePositions() {
  const [positions, setPositions] = useState<Position[]>(cachedPositions ?? []);

  useEffect(() => {
    const onChange = () => setPositions(cachedPositions ?? []);
    listeners.add(onChange);
    if (cachedPositions === null) {
      cachedPositions = loadFromStorage();
    }
    // Always sync on mount, not just when this instance was the one that
    // loaded the cache — another usePositions() instance may have populated
    // cachedPositions moments earlier (e.g. on an instant cache-hit, this
    // instance and PositionsStrip's can mount in the same commit), in which
    // case this instance would otherwise never learn about it: notify() only
    // reaches listeners already registered at the time it fired.
    onChange();
    return () => { listeners.delete(onChange); };
  }, []);

  const isPositioned = useCallback(
    (symbol: string) => positions.some(p => p.symbol === symbol.toUpperCase()),
    [positions],
  );

  const addPosition = useCallback((pos: Omit<Position, 'bought_at'>) => {
    const symbol = pos.symbol.toUpperCase();
    const withoutExisting = (cachedPositions ?? loadFromStorage()).filter(p => p.symbol !== symbol);
    persist([...withoutExisting, { ...pos, symbol, bought_at: new Date().toISOString() }]);
  }, []);

  const removePosition = useCallback((symbol: string) => {
    const upper = symbol.toUpperCase();
    persist((cachedPositions ?? loadFromStorage()).filter(p => p.symbol !== upper));
  }, []);

  // Filled in after the fact, typically from the Portfolio page — asking for
  // a share count at "I bought this" click-time would add friction to what's
  // meant to be a one-click action while browsing Market Picks.
  const updateShares = useCallback((symbol: string, shares: number | null) => {
    const upper = symbol.toUpperCase();
    persist((cachedPositions ?? loadFromStorage()).map(p => (p.symbol === upper ? { ...p, shares } : p)));
  }, []);

  return { positions, isPositioned, addPosition, removePosition, updateShares };
}
