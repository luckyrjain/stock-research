'use client';

import Link from 'next/link';
import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

const PENDING_KITE_ACCOUNT_KEY = 'portfolio_pending_kite_account_id';
const ZERODHA_BROKER = 'zerodha';

// Kite Connect's own login flow redirects here with `request_token` (and
// `status=success`/`error`) in the query string — it has no way to echo
// back custom state, so the account being connected was stashed in
// localStorage right before the browser left for kite.trade (see
// BrokerConnectControls.connect() in ../page.tsx), read back here.
function KiteCallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<'connecting' | 'done' | 'error'>('connecting');
  const [error, setError] = useState('');
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    const requestToken = searchParams.get('request_token');
    const kiteStatus = searchParams.get('status');
    const accountId = localStorage.getItem(PENDING_KITE_ACCOUNT_KEY);
    localStorage.removeItem(PENDING_KITE_ACCOUNT_KEY);

    if (kiteStatus && kiteStatus !== 'success') {
      setStatus('error');
      setError('Zerodha login was cancelled or failed.');
      return;
    }
    if (!requestToken || !accountId) {
      setStatus('error');
      setError('Missing login details — please try connecting again.');
      return;
    }

    fetch(`/api/portfolio/broker/${ZERODHA_BROKER}/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: Number(accountId), request_token: requestToken }),
    })
      .then(async res => {
        const body = await res.json();
        if (!res.ok) throw new Error(body?.detail ?? `Connect failed (${res.status})`);
        setStatus('done');
        setTimeout(() => router.replace('/portfolio-aggregator'), 1500);
      })
      .catch(e => {
        setStatus('error');
        setError(e instanceof Error ? e.message : 'Could not complete the Zerodha connection.');
      });
  }, [searchParams, router]);

  return (
    <main className="min-h-screen bg-bg text-tx flex items-center justify-center px-4">
      <div className="w-full max-w-sm text-center">
        <Link href="/portfolio-aggregator" className="block mb-8 text-xl font-black tracking-tight text-tx">
          Alpha<span className="text-accent">Pulse</span>
        </Link>
        <div className="px-5 py-4 rounded-xl bg-card border border-border text-sm">
          {status === 'connecting' && (
            <>
              <p className="text-tx font-semibold mb-1">Connecting your Zerodha account…</p>
              <p className="text-muted">Just a moment.</p>
            </>
          )}
          {status === 'done' && (
            <>
              <p className="text-tx font-semibold mb-1">Zerodha connected</p>
              <p className="text-muted">Taking you back to Net Worth…</p>
            </>
          )}
          {status === 'error' && (
            <>
              <p className="text-sell font-semibold mb-1">Connection failed</p>
              <p className="text-muted mb-3">{error}</p>
              <Link href="/portfolio-aggregator" className="text-accent text-xs font-semibold hover:underline">
                ← Back to Net Worth
              </Link>
            </>
          )}
        </div>
      </div>
    </main>
  );
}

export default function KiteCallbackPage() {
  return (
    <Suspense fallback={null}>
      <KiteCallbackInner />
    </Suspense>
  );
}
