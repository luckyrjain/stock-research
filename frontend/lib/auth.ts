'use client';

import { useCallback, useEffect, useState } from 'react';

export interface AuthUser {
  id: number;
  email: string;
}

// Module-level shared cache, same pattern as watchlist.ts's useWatchlist():
// every mounted useAuth() instance (AuthWidget in each page's nav bar) reads
// from and subscribes to this single cache instead of each independently
// hitting /api/auth/me on mount. `undefined` = not yet loaded, `null` = loaded
// and signed out.
let cachedUser: AuthUser | null | undefined;
let inFlight: Promise<AuthUser | null> | null = null;
// Bumped by refreshAuth() so a stale fetch already in flight (started before
// a refresh was requested) can't clobber the fresher result if it happens to
// resolve after it — the classic out-of-order-response race.
let generation = 0;
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach(fn => fn());
}

async function fetchMe(): Promise<AuthUser | null> {
  if (inFlight) return inFlight;
  const myGeneration = generation;
  inFlight = fetch('/api/auth/me', { cache: 'no-store' })
    .then(res => (res.ok ? res.json() : { user: null }))
    .then((data: { user?: AuthUser | null }) => data.user ?? null)
    .catch(() => null)
    .finally(() => { inFlight = null; });
  const user = await inFlight;
  if (myGeneration === generation) {
    cachedUser = user;
    notify();
  }
  return user;
}

/** Re-fetches /api/auth/me and updates every subscribed useAuth() instance —
 * call after a successful /auth/verify so the nav bar picks up the new
 * session without a full page reload. */
export function refreshAuth(): Promise<AuthUser | null> {
  generation++;
  inFlight = null;
  return fetchMe();
}

/** Shared cross-component auth state, backed by an httpOnly session cookie
 * the Next.js proxy routes manage — this hook never touches the cookie or
 * token directly, only the /api/auth/* JSON endpoints. */
export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(cachedUser ?? null);
  const [loading, setLoading] = useState(cachedUser === undefined);

  useEffect(() => {
    const onChange = () => setUser(cachedUser ?? null);
    listeners.add(onChange);
    if (cachedUser === undefined) {
      fetchMe().finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    return () => { listeners.delete(onChange); };
  }, []);

  const requestLink = useCallback(async (email: string) => {
    const res = await fetch('/api/auth/request-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || data.error || 'Could not send sign-in link.');
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } finally {
      cachedUser = null;
      notify();
    }
  }, []);

  return { user, loading, requestLink, logout };
}
