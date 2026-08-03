'use client';

import Link from 'next/link';
import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

const PENDING_BROKER_CONNECT_KEY = 'portfolio_pending_broker_connect';

const BROKER_LABELS: Record<string, string> = {
  zerodha: 'Zerodha',
  paytm_money: 'Paytm Money',
};

// Shared callback destination for every redirect-based broker's own login
// (Zerodha/Paytm Money register this same URL as their app's redirect
// URI) — neither has a way to echo back custom state, so the account +
// broker being connected were stashed in localStorage right before the
// browser left for that broker's login page (see BrokerRow.connect() in
// ../page.tsx), read back here. HDFC Securities never redirects at all
// (see HdfcBrokerRow in ../page.tsx) and never reaches this page.
function BrokerCallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<'connecting' | 'done' | 'error'>('connecting');
  const [error, setError] = useState('');
  const [brokerLabel, setBrokerLabel] = useState('your broker');
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    const requestToken = searchParams.get('request_token');
    // Kite Connect's own convention (`status=success`/`error` alongside
    // request_token) — HDFC Securities/Paytm Money's exact redirect query
    // shape wasn't verified live (see portfolio/hdfc_sync.py's/paytm_sync.py's
    // own disclosed-limitation docstrings), so this check only ever fires
    // for a broker that actually sends it; its absence isn't itself an error.
    const loginStatus = searchParams.get('status');
    const pendingRaw = localStorage.getItem(PENDING_BROKER_CONNECT_KEY);
    localStorage.removeItem(PENDING_BROKER_CONNECT_KEY);

    let pending: { account_id: number; broker: string } | null = null;
    try {
      pending = pendingRaw ? JSON.parse(pendingRaw) : null;
    } catch {
      pending = null;
    }
    if (pending?.broker) setBrokerLabel(BROKER_LABELS[pending.broker] ?? pending.broker);

    if (loginStatus && loginStatus !== 'success') {
      setStatus('error');
      setError('Broker login was cancelled or failed.');
      return;
    }
    if (!requestToken || !pending) {
      setStatus('error');
      setError('Missing login details — please try connecting again.');
      return;
    }

    fetch(`/api/portfolio/broker/${pending.broker}/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: pending.account_id, request_token: requestToken }),
    })
      .then(async res => {
        const body = await res.json();
        if (!res.ok) throw new Error(body?.detail ?? `Connect failed (${res.status})`);
        setStatus('done');
        setTimeout(() => router.replace('/portfolio-aggregator'), 1500);
      })
      .catch(e => {
        setStatus('error');
        setError(e instanceof Error ? e.message : 'Could not complete the broker connection.');
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
              <p className="text-tx font-semibold mb-1">Connecting your {brokerLabel} account…</p>
              <p className="text-muted">Just a moment.</p>
            </>
          )}
          {status === 'done' && (
            <>
              <p className="text-tx font-semibold mb-1">{brokerLabel} connected</p>
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

export default function BrokerCallbackPage() {
  return (
    <Suspense fallback={null}>
      <BrokerCallbackInner />
    </Suspense>
  );
}
