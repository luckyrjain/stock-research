'use client';

import Link from 'next/link';
import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { refreshAuth } from '@/lib/auth';

function VerifyInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<'verifying' | 'error'>('verifying');
  const [error, setError] = useState('');

  useEffect(() => {
    const token = searchParams.get('token');
    if (!token) {
      setStatus('error');
      setError('Missing sign-in token.');
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/auth/verify?token=${encodeURIComponent(token)}`, { cache: 'no-store' });
        const data = await res.json();
        if (!res.ok) {
          if (!cancelled) {
            setStatus('error');
            setError(data.detail || data.error || 'This sign-in link is invalid or expired.');
          }
          return;
        }
        await refreshAuth();
        if (!cancelled) router.replace('/');
      } catch {
        if (!cancelled) {
          setStatus('error');
          setError('Something went wrong. Please try again.');
        }
      }
    })();

    return () => { cancelled = true; };
  }, [searchParams, router]);

  return (
    <main className="min-h-screen bg-bg text-tx flex items-center justify-center px-4">
      <div className="w-full max-w-sm text-center">
        {status === 'verifying' ? (
          <p className="text-muted text-sm">Signing you in…</p>
        ) : (
          <div className="px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sm">
            <p className="text-sell font-semibold mb-1">Sign-in failed</p>
            <p className="text-muted mb-3">{error}</p>
            <Link href="/login" className="text-accent text-xs font-semibold hover:underline">
              Request a new link →
            </Link>
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
