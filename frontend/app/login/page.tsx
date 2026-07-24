'use client';

import Link from 'next/link';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useAuth } from '@/lib/auth';

export default function LoginPage() {
  const { requestLink } = useAuth();
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [error, setError] = useState('');

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setStatus('sending');
    setError('');
    try {
      await requestLink(email);
      setStatus('sent');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.');
      setStatus('error');
    }
  }

  return (
    <main className="min-h-screen bg-bg text-tx flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <Link href="/" className="block text-center mb-8 text-xl font-black tracking-tight text-tx">
          Alpha<span className="text-accent">Pulse</span>
        </Link>

        {status === 'sent' ? (
          <div className="text-center px-5 py-4 rounded-xl bg-buy/10 border border-buy/30 text-sm">
            <p className="text-tx font-semibold mb-1">Check your email</p>
            <p className="text-muted">
              We sent a sign-in link to <span className="text-tx">{email}</span>. It expires in 15 minutes.
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="block text-xs font-semibold text-muted mb-1.5" htmlFor="email">
                Email address
              </label>
              <input
                id="email"
                type="email"
                required
                autoFocus
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full px-4 py-2.5 rounded-lg bg-surface border border-border text-tx text-sm
                           placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-accent/50"
              />
            </div>
            {status === 'error' && (
              <p role="alert" className="text-sell text-xs">{error}</p>
            )}
            <button
              type="submit"
              disabled={status === 'sending'}
              className="w-full px-4 py-2.5 rounded-lg bg-accent text-bg text-sm font-semibold
                         hover:bg-accent/90 transition-colors disabled:opacity-50"
            >
              {status === 'sending' ? 'Sending…' : 'Send sign-in link'}
            </button>
            <p className="text-muted/60 text-xs text-center">
              No password needed — we&apos;ll email you a one-time link.
            </p>
          </form>
        )}
      </div>
    </main>
  );
}
