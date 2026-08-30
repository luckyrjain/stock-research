'use client';

import { useCallback } from 'react';
import { refreshWatchlist } from '@/lib/watchlist';
import { refreshPositions } from '@/lib/positions';
import { createSharedResource } from '@/lib/shared-resource';

export interface AuthUser {
  id: number;
  email: string;
  tier: 'free' | 'pro';
}

// Shared cross-component cache, built on lib/shared-resource.ts's generic
// createSharedResource() — same machinery lib/watchlist.ts's useWatchlist()
// uses: every mounted useAuth() instance (AuthWidget in each page's nav bar)
// reads from and subscribes to this single resource instead of each
// independently hitting /api/auth/me on mount.
async function fetchMe(): Promise<AuthUser | null> {
  return fetch('/api/auth/me', { cache: 'no-store' })
    .then(res => (res.ok ? res.json() : { user: null }))
    .then((data: { user?: AuthUser | null }) => data.user ?? null)
    .catch(() => null);
}

// `null` (not the "previous" param) is always what a failed/unauthenticated
// fetch resolves to — unlike watchlist/positions, auth doesn't fall back to
// keeping a stale cached user on failure; a network error is treated the
// same as "signed out."
const resource = createSharedResource<AuthUser | null>(() => fetchMe(), null);

/** Re-fetches /api/auth/me and updates every subscribed useAuth() instance —
 * call after a successful /auth/verify so the nav bar picks up the new
 * session without a full page reload. */
export function refreshAuth(): Promise<AuthUser | null> {
  return resource.refresh();
}

/** Shared cross-component auth state, backed by an httpOnly session cookie
 * the Next.js proxy routes manage — this hook never touches the cookie or
 * token directly, only the /api/auth/* JSON endpoints. */
export function useAuth() {
  const { value: user, loading } = resource.useValue();

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
      // Same generation-bump + inFlight reset refreshAuth() uses above —
      // without this, a fetchMe() call already in flight when logout()
      // runs (e.g. a second useAuth() consumer's mount-time fetch) can
      // resolve afterward, pass the (unbumped) generation check, and
      // silently revert cachedUser back to the signed-in user: the nav
      // bar would keep showing "signed in" after a real logout, with no
      // self-correction until something else calls refreshAuth().
      resource.bumpGeneration();
      resource.setCache(null);
      // The watchlist's and positions' own shared caches have no way to
      // know the caller's identity just changed back to the anonymous
      // client_id — without this they'd keep showing the account's rows
      // post-logout.
      refreshWatchlist();
      refreshPositions();
    }
  }, []);

  return { user, loading, requestLink, logout };
}
