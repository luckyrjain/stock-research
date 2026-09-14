'use client';

import { useCallback, useState, type Dispatch, type SetStateAction } from 'react';

type SortDir = 'asc' | 'desc';

// screener/page.tsx, sme-signals/page.tsx, and market-picks-dashboard.tsx each
// independently built the same paired sortKey/sortDir state + toggle: click
// the already-active column to flip direction, click a different column to
// select it and reset to 'desc'. This is the one implementation; each site's
// own SortableTh (data-table-ui.tsx) wires straight into the returned values
// (currentKey/currentDir/onSort). setSortKey/setSortDir are exposed alongside
// toggleSort for screener/page.tsx, the one site that also restores a
// persisted sortKey/sortDir pair directly rather than via a toggle click.
export function useSortState<K extends string>(initialKey: K): {
  sortKey: K;
  setSortKey: Dispatch<SetStateAction<K>>;
  sortDir: SortDir;
  setSortDir: Dispatch<SetStateAction<SortDir>>;
  toggleSort: (key: K) => void;
};
export function useSortState<K extends string>(initialKey?: K | null): {
  sortKey: K | null;
  setSortKey: Dispatch<SetStateAction<K | null>>;
  sortDir: SortDir;
  setSortDir: Dispatch<SetStateAction<SortDir>>;
  toggleSort: (key: K) => void;
};
export function useSortState<K extends string>(initialKey: K | null = null) {
  const [sortKey, setSortKey] = useState<K | null>(initialKey);
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const toggleSort = useCallback((k: K) => {
    setSortKey(prevKey => {
      if (prevKey === k) {
        setSortDir(prevDir => (prevDir === 'desc' ? 'asc' : 'desc'));
        return k;
      }
      setSortDir('desc');
      return k;
    });
  }, []);

  return { sortKey, setSortKey, sortDir, setSortDir, toggleSort };
}
