'use client';

import Link from 'next/link';
import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { refreshAuth } from '@/lib/auth';
import { claimWatchlist, getClientId, refreshWatchlist, type WatchlistItem } from '@/lib/watchlist';
import { claimPositions, refreshPositions, type Position } from '@/lib/positions';

// Verification requires an explicit click rather than firing automatically
// on page load. The backend token is single-use, and corporate "safe link"
// pre-fetchers (Outlook Safe Links, Proofpoint, etc.) crawl links in emails
// before a human ever opens them — an auto-firing GET here would let the
// scanner burn the token first and lock the real user out.
function VerifyInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get('token');
  const [status, setStatus] = useState<'idle' | 'verifying' | 'claim-prompt' | 'claiming' | 'error'>(token ? 'idle' : 'error');
  const [error, setError] = useState(token ? '' : 'Missing sign-in token.');
  // Counted BEFORE the session cookie is set (see below) — once a session
  // exists, GET /api/watchlist and /api/positions resolve to the account's
  // own rows instead of this client_id's, so there'd be no way to tell
  // "does the anonymous browser have anything worth claiming" afterward.
  const [anonCounts, setAnonCounts] = useState<{ watchlist: number; positions: number }>({ watchlist: 0, positions: 0 });
  const mountedRef = useRef(true);

  useEffect(() => {
    return () => { mountedRef.current = false; };
  }, []);

  async function handleConfirm() {
    if (!token) return;
    setStatus('verifying');

    const clientId = getClientId();
    // Best-effort, run before verify() below — a failure here just means
    // the claim prompt never appears, not that sign-in itself fails.
    const [wlCount, posCount] = await Promise.all([
      fetch(`/api/watchlist?client_id=${encodeURIComponent(clientId)}`, { cache: 'no-store' })
        .then(res => (res.ok ? res.json() : null))
        .then((data: { items?: WatchlistItem[] } | null) => data?.items?.length ?? 0)
        .catch(() => 0),
      fetch(`/api/positions?client_id=${encodeURIComponent(clientId)}`, { cache: 'no-store' })
        .then(res => (res.ok ? res.json() : null))
        .then((data: { items?: Position[] } | null) => data?.items?.length ?? 0)
        .catch(() => 0),
    ]);

    try {
      const res = await fetch(`/api/auth/verify?token=${encodeURIComponent(token)}`, { cache: 'no-store' });
      const data = await res.json();
      if (!mountedRef.current) return;
      if (!res.ok) {
        setStatus('error');
        setError(data.detail || data.error || 'This sign-in link is invalid or expired.');
        return;
      }
      await refreshAuth();
      // The watchlist's and positions' own module-level caches have no way
      // to know the caller's identity just changed to this account —
      // without this they'd keep showing whatever the anonymous client_id
      // had.
      refreshWatchlist();
      refreshPositions();
      if (!mountedRef.current) return;

      if (wlCount > 0 || posCount > 0) {
        setAnonCounts({ watchlist: wlCount, positions: posCount });
        setStatus('claim-prompt');
      } else {
        router.replace('/');
      }
    } catch {
      if (mountedRef.current) {
        setStatus('error');
        setError('Something went wrong. Please try again.');
      }
    }
  }

  async function handleClaim() {
    setStatus('claiming');
    const clientId = getClientId();
    // Both run regardless of whether that specific count was 0 — a 0 count
    // just means that claim is a no-op server-side, simpler than branching.
    await Promise.all([claimWatchlist(clientId), claimPositions(clientId)]);
    if (mountedRef.current) router.replace('/');
  }

  function handleSkip() {
    router.replace('/');
  }

  return (
    <main className="min-h-screen bg-bg text-tx flex items-center justify-center px-4">
      <div className="w-full max-w-sm text-center">
        <Link href="/" className="block mb-8 text-xl font-black tracking-tight text-tx">
          Alpha<span className="text-accent">Pulse</span>
        </Link>

        {status === 'error' ? (
          <div className="px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sm">
            <p className="text-sell font-semibold mb-1">Sign-in failed</p>
            <p className="text-muted mb-3">{error}</p>
            <Link href="/login" className="text-accent text-xs font-semibold hover:underline">
              Request a new link →
            </Link>
          </div>
        ) : status === 'claim-prompt' || status === 'claiming' ? (
          <div className="px-5 py-4 rounded-xl bg-card border border-border text-sm">
            <p className="text-tx font-semibold mb-1">You&apos;re signed in</p>
            <p className="text-muted mb-4">
              This browser has{' '}
              {[
                anonCounts.watchlist > 0 ? `${anonCounts.watchlist} watchlist item${anonCounts.watchlist === 1 ? '' : 's'}` : null,
                anonCounts.positions > 0 ? `${anonCounts.positions} tracked position${anonCounts.positions === 1 ? '' : 's'}` : null,
              ].filter(Boolean).join(' and ')}{' '}
              from browsing anonymously. Claim {anonCounts.watchlist > 0 && anonCounts.positions > 0 ? 'them' : 'it'} onto your account
              so they follow you to other devices?
            </p>
            <div className="flex items-center justify-center gap-2">
              <button
                onClick={handleClaim}
                disabled={status === 'claiming'}
                className="px-4 py-2 rounded-lg bg-accent text-bg text-sm font-semibold
                           hover:bg-accent/90 transition-colors disabled:opacity-50"
              >
                {status === 'claiming' ? 'Claiming…' : 'Claim my data'}
              </button>
              <button
                onClick={handleSkip}
                disabled={status === 'claiming'}
                className="px-4 py-2 rounded-lg border border-border text-muted text-sm font-semibold
                           hover:text-tx transition-colors disabled:opacity-50"
              >
                Skip
              </button>
            </div>
          </div>
        ) : (
          <div className="px-5 py-4 rounded-xl bg-card border border-border text-sm">
            <p className="text-tx font-semibold mb-1">Finish signing in</p>
            <p className="text-muted mb-4">Click below to complete sign-in to AlphaPulse.</p>
            <button
              onClick={handleConfirm}
              disabled={status === 'verifying'}
              className="px-4 py-2 rounded-lg bg-accent text-bg text-sm font-semibold
                         hover:bg-accent/90 transition-colors disabled:opacity-50"
            >
              {status === 'verifying' ? 'Signing you in…' : 'Complete sign-in'}
            </button>
          </div>
        )}
      </div>
    </main>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={null}>
      <VerifyInner />
    </Suspense>
  );
}
