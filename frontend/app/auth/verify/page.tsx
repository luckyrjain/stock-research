'use client';

import Link from 'next/link';
import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { refreshAuth } from '@/lib/auth';

// Verification requires an explicit click rather than firing automatically
// on page load. The backend token is single-use, and corporate "safe link"
// pre-fetchers (Outlook Safe Links, Proofpoint, etc.) crawl links in emails
// before a human ever opens them — an auto-firing GET here would let the
// scanner burn the token first and lock the real user out.
function VerifyInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get('token');
  const [status, setStatus] = useState<'idle' | 'verifying' | 'error'>(token ? 'idle' : 'error');
  const [error, setError] = useState(token ? '' : 'Missing sign-in token.');
  const mountedRef = useRef(true);

  useEffect(() => {
    return () => { mountedRef.current = false; };
  }, []);

  async function handleConfirm() {
    if (!token) return;
    setStatus('verifying');
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
      if (!mountedRef.current) return;
      router.replace('/');
    } catch {
      if (mountedRef.current) {
        setStatus('error');
        setError('Something went wrong. Please try again.');
      }
    }
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
