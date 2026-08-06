'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@/lib/auth';
import SiteNav from '@/components/site-nav';
import type { ApiKey, ApiKeysResponse, ApiUsage, CreatedApiKey } from '@/types';

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export default function ApiKeysPage() {
  const { user, loading: authLoading } = useAuth();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [tier, setTier] = useState<'free' | 'pro'>('free');
  const [usage, setUsage] = useState<ApiUsage | null>(null);
  const [keysLoading, setKeysLoading] = useState(true);
  const [label, setLabel] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const [justCreated, setJustCreated] = useState<CreatedApiKey | null>(null);
  const [copied, setCopied] = useState(false);

  const loadKeys = useCallback(async () => {
    setKeysLoading(true);
    try {
      const res = await fetch('/api/api-keys', { cache: 'no-store' });
      if (!res.ok) { setKeys([]); setUsage(null); return; }
      const data = await res.json() as Partial<ApiKeysResponse>;
      setKeys(data.keys ?? []);
      setTier(data.tier ?? 'free');
      setUsage(data.usage ?? null);
    } catch {
      setKeys([]);
      setUsage(null);
    } finally {
      setKeysLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) loadKeys();
    else setKeysLoading(false);
  }, [user, loadKeys]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError('');
    try {
      const res = await fetch('/api/api-keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: label.trim() || null }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || 'Could not create key.');
      // Only replace the previously-shown secret (if any) once the new one
      // has genuinely arrived — clearing it eagerly at the top of this
      // function would blank an unrelated, not-yet-copied key's box the
      // instant a second creation attempt starts, permanently losing it if
      // that attempt then fails. `copied` is reset alongside it: it's tied
      // to whichever key is currently shown, so a fresh key must not
      // inherit an unrelated "Copied!" state left over from a previous one.
      setJustCreated(data as CreatedApiKey);
      setCopied(false);
      setLabel('');
      await loadKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create key.');
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(id: number) {
    if (!confirm('Revoke this key? Anything using it will stop working immediately.')) return;
    try {
      const res = await fetch(`/api/api-keys/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Could not revoke key.');
      await loadKeys();
    } catch {
      setError('Could not revoke key. Try again.');
    }
  }

  async function copyKey() {
    if (!justCreated) return;
    try {
      await navigator.clipboard.writeText(justCreated.key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard access can fail (permissions, insecure context) — the key
      // is still visible on screen to copy by hand
    }
  }

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-3xl mx-auto px-4 pt-8 pb-16">

        <SiteNav active="api-keys" wrap />

        <div className="mb-6">
          <h1 className="text-xl font-black tracking-tight text-tx mb-1.5">API Keys</h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            Programmatic access for scripts and integrations. Send a key in the{' '}
            <code className="text-tx bg-surface px-1 py-0.5 rounded text-xs">X-API-Key</code> header to{' '}
            <code className="text-tx bg-surface px-1 py-0.5 rounded text-xs">GET /api/v1/consolidated/&#123;symbol&#125;</code>.
            Keys have no expiry — revoke one when you no longer need it.
          </p>
        </div>

        {authLoading ? null : !user ? (
          <div className="rounded-xl border border-border bg-card px-6 py-10 text-center">
            <p className="text-sm text-muted mb-4">Sign in to create and manage API keys.</p>
            <Link href="/login" className="text-xs font-semibold text-accent hover:underline">
              Sign in →
            </Link>
          </div>
        ) : (
          <>
            {usage && (
              <div className="rounded-xl border border-border bg-card px-5 py-4 mb-6">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold uppercase tracking-wider text-muted">Usage</span>
                    <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded border
                      ${tier === 'pro' ? 'text-accent border-accent/40 bg-accent/10' : 'text-muted border-border'}`}>
                      {tier} tier
                    </span>
                  </div>
                  <span className="font-mono text-xs text-muted tabular-nums">
                    {usage.calls} / {usage.limit} calls this hour
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-surface overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${usage.calls >= usage.limit ? 'bg-sell' : 'bg-accent'}`}
                    style={{ width: `${Math.min(100, (usage.calls / Math.max(1, usage.limit)) * 100)}%` }}
                  />
                </div>
                <p className="text-[10px] text-muted/70 mt-2">
                  Applies across every key on this account, to{' '}
                  <code className="text-tx bg-surface px-1 py-0.5 rounded">GET /api/v1/*</code> calls only —
                  a sliding one-hour window, not a calendar-hour reset.
                  {tier === 'free' && (
                    <> Higher-tier limits exist — see{' '}
                      <Link href="/pricing" className="text-accent hover:underline">Pricing</Link>.
                    </>
                  )}
                </p>
              </div>
            )}

            <form onSubmit={handleCreate} className="flex items-end gap-2 mb-6">
              <div className="flex-1">
                <label className="block text-xs font-semibold text-muted mb-1.5" htmlFor="label">
                  Label (optional)
                </label>
                <input
                  id="label"
                  type="text"
                  maxLength={120}
                  value={label}
                  onChange={e => setLabel(e.target.value)}
                  placeholder="e.g. my trading bot"
                  className="w-full px-4 py-2.5 rounded-lg bg-surface border border-border text-tx text-sm
                             placeholder:text-muted/60 focus:ring-2 focus:ring-accent/50"
                />
              </div>
              <button
                type="submit"
                disabled={creating}
                className="px-4 py-2.5 rounded-lg bg-accent text-bg text-sm font-semibold
                           hover:bg-accent/90 transition-colors disabled:opacity-50 shrink-0"
              >
                {creating ? 'Creating…' : 'Create key'}
              </button>
            </form>

            {error && (
              <p role="alert" className="text-sell text-xs mb-4">{error}</p>
            )}

            {justCreated && (
              <div className="mb-6 px-5 py-4 rounded-xl bg-buy/10 border border-buy/30 text-sm">
                <p className="text-tx font-semibold mb-1.5">
                  Copy this key now — it won&apos;t be shown again.
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 px-3 py-2 rounded-lg bg-surface border border-border text-xs
                                    text-tx font-mono break-all">
                    {justCreated.key}
                  </code>
                  <button
                    onClick={copyKey}
                    type="button"
                    className="shrink-0 px-3 py-2 rounded-lg text-xs font-semibold border border-border
                               text-muted hover:text-accent hover:border-accent/40 transition-colors"
                  >
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                </div>
              </div>
            )}

            {keysLoading ? (
              <div className="rounded-xl border border-border overflow-hidden">
                <div className="divide-y divide-border/60">
                  {Array.from({ length: 2 }).map((_, i) => (
                    <div key={i} className="px-4 py-4 flex items-center gap-4">
                      <div className="h-4 w-32 bg-border/60 rounded animate-pulse" />
                      <div className="h-4 w-20 bg-border/60 rounded animate-pulse ml-auto" />
                    </div>
                  ))}
                </div>
              </div>
            ) : keys.length === 0 ? (
              <div className="rounded-xl border border-border bg-card px-6 py-10 text-center">
                <p className="text-sm text-muted">No API keys yet. Create one above to get started.</p>
              </div>
            ) : (
              <div className="rounded-xl border border-border overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-surface">
                        {['Key', 'Label', 'Created', 'Last used', ''].map(label => (
                          <th key={label} className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {keys.map(k => (
                        <tr key={k.id} className="border-b border-border/60 last:border-0">
                          <td className="px-4 py-4 font-mono text-xs text-tx">{k.key_prefix}…</td>
                          <td className="px-4 py-4 text-xs text-muted">{k.label || '—'}</td>
                          <td className="px-4 py-4 text-xs text-muted">{fmtDate(k.created_at)}</td>
                          <td className="px-4 py-4 text-xs text-muted">{fmtDate(k.last_used_at)}</td>
                          <td className="px-4 py-4 text-right">
                            {k.revoked_at ? (
                              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded border text-muted border-border">
                                Revoked
                              </span>
                            ) : (
                              <button
                                onClick={() => handleRevoke(k.id)}
                                aria-label={`Revoke API key ${k.key_prefix}`}
                                className="text-xs text-muted hover:text-sell transition-colors"
                              >
                                Revoke
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
