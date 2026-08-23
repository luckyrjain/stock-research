'use client';

import { useEffect, useState } from 'react';

export interface SharedResource<T> {
  /** React hook: current value + whether the first fetch is still in
   * flight. Every mounted instance across the page shares one cache, one
   * in-flight fetch, and one listener set. */
  useValue(): { value: T; loading: boolean };
  /** Current cached value, or `undefined` if never fetched. */
  getCache(): T | undefined;
  /** Fetches (joining an in-flight fetch if one exists); updates the cache
   * and notifies subscribers only if this fetch's generation is still
   * current when it resolves. */
  fetch(): Promise<T>;
  /** Bumps the generation, clears any in-flight fetch, and re-fetches —
   * call after an identity change (sign-in/sign-out) so a fetch already in
   * flight under the old identity can't clobber the fresher result. */
  refresh(): Promise<T>;
  /** Bumps the generation and clears any in-flight fetch WITHOUT
   * re-fetching — for callers about to set the cache directly (claim
   * endpoints, logout) instead of re-fetching. */
  bumpGeneration(): void;
  /** The generation to capture BEFORE starting a mutation's own request, so
   * the mutation can later confirm (via `isCurrent`) that no identity
   * change superseded it while the request was in flight. */
  getGeneration(): number;
  /** True if `gen` still matches the current generation. Call AFTER an
   * await, with `gen` captured BEFORE it, to guard a mutation's cache write
   * against a stale, out-of-order response. */
  isCurrent(gen: number): boolean;
  /** Overwrites the cache and notifies subscribers unconditionally — call
   * only after an `isCurrent` check (or right after `bumpGeneration()`,
   * when the caller already knows the write is authoritative). */
  setCache(value: T): void;
}

/**
 * Generic module-level shared-resource cache: one cache / one in-flight
 * fetch / one listener Set per resource, shared by every mounted hook
 * instance on the page, plus a generation counter that fences stale
 * responses after an identity change (sign-in/sign-out). Factored out of
 * lib/watchlist.ts, lib/positions.ts, and lib/auth.ts, which each
 * independently reimplemented this exact pattern — those files are now thin
 * domain wrappers (their own fetch function + their own mutation methods)
 * built on this shared machinery.
 *
 * `fetchFn` must never reject (catch internally) and must resolve to a
 * concrete `T`. It receives the previously cached value so it can implement
 * its own "keep stale data on failure" fallback (the
 * `data.items ?? previous ?? []` convention watchlist/positions use) — or
 * ignore it and always overwrite, as auth's fetchFn does.
 *
 * `emptyValue` is what `useValue()` reports before the first fetch
 * resolves. `T` must not itself use `undefined` as a valid loaded value,
 * since `undefined` is the internal "never fetched" sentinel.
 */
export function createSharedResource<T>(
  fetchFn: (previous: T | undefined) => Promise<T>,
  emptyValue: T,
): SharedResource<T> {
  let cache: T | undefined;
  let inFlight: Promise<T> | null = null;
  let generation = 0;
  const listeners = new Set<() => void>();

  function notify(): void {
    listeners.forEach(fn => fn());
  }

  async function fetchValue(): Promise<T> {
    // Captured BEFORE joining/creating the in-flight fetch — a later
    // refresh()/bumpGeneration() call can advance `generation` while this
    // fetch is still pending, and the write below must be able to tell.
    const myGeneration = generation;
    if (!inFlight) {
      inFlight = fetchFn(cache).finally(() => { inFlight = null; });
    }
    const value = await inFlight;
    if (myGeneration === generation) {
      cache = value;
      notify();
    }
    // No caller of fetch()/refresh() in this codebase uses the resolved
    // value for correctness (they rely on the cache+notify side effect
    // above, e.g. `fetchValue().finally(() => setLoading(false))`) —
    // returning it even when stale keeps this usable as a plain fetch for
    // any future caller that does.
    return value;
  }

  function refresh(): Promise<T> {
    generation++;
    inFlight = null;
    return fetchValue();
  }

  function bumpGeneration(): void {
    generation++;
    inFlight = null;
  }

  function setCache(value: T): void {
    cache = value;
    notify();
  }

  function useValue(): { value: T; loading: boolean } {
    const [value, setValue] = useState<T>(cache ?? emptyValue);
    const [loading, setLoading] = useState(cache === undefined);

    useEffect(() => {
      const onChange = () => setValue(cache ?? emptyValue);
      listeners.add(onChange);
      if (cache === undefined) {
        fetchValue().finally(() => setLoading(false));
      } else {
        setLoading(false);
      }
      return () => { listeners.delete(onChange); };
    }, []);

    return { value, loading };
  }

  return {
    useValue,
    getCache: () => cache,
    fetch: fetchValue,
    refresh,
    bumpGeneration,
    getGeneration: () => generation,
    isCurrent: (gen: number) => gen === generation,
    setCache,
  };
}
