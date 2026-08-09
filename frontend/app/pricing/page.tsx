'use client';

import Link from 'next/link';
import PageShell from '@/components/page-shell';

// Purely informational — there is no self-serve checkout or payment
// processing anywhere in this app (see CLAUDE.md's "Tiers + usage
// dashboard" section). This page exists to close the "no pricing page
// anywhere" gap a design review flagged, honestly: it explains what Free
// and Pro actually unlock today, and says plainly that Pro is granted by
// whoever operates this deployment, not purchased through this page.
const TIERS = [
  {
    name: 'Free',
    tag: null,
    calls: '100',
    blurb: 'The default for every account — no setup required.',
    features: [
      'Full web app: stock analysis, Market Picks, SME Signals, Screener, Watchlist, Portfolio',
      'API access to GET /api/v1/consolidated/{symbol}',
      '100 calls / hour, per account',
    ],
  },
  {
    name: 'Pro',
    tag: 'Higher limits',
    calls: '1,000',
    blurb: 'For scripts and integrations that outgrow the free rate limit.',
    features: [
      'Everything in Free',
      'API access to GET /api/v1/consolidated/{symbol}',
      '1,000 calls / hour, per account — 10× the free tier',
    ],
  },
] as const;

export default function PricingPage() {
  return (
    <PageShell extraLabel="Pricing" wrap maxWidth="max-w-3xl">

        <div className="mb-8">
          <h1 className="text-xl font-black tracking-tight text-tx mb-1.5">Pricing</h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            AlphaPulse&apos;s web app is free to use. The only tiered limit today is the
            programmatic API&apos;s hourly rate cap.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
          {TIERS.map(t => (
            <div key={t.name} className="rounded-xl border border-border bg-card px-5 py-5">
              <div className="flex items-center gap-2 mb-1">
                <h2 className="text-base font-bold text-tx">{t.name}</h2>
                {t.tag && (
                  <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded border
                                     text-accent border-accent/40 bg-accent/10">
                    {t.tag}
                  </span>
                )}
              </div>
              <p className="text-xs text-muted mb-4">{t.blurb}</p>
              <p className="mb-4">
                <span className="font-mono font-black text-2xl text-tx">{t.calls}</span>
                <span className="text-muted text-sm"> calls / hour</span>
              </p>
              <ul className="space-y-1.5">
                {t.features.map(f => (
                  <li key={f} className="flex gap-2 text-xs text-tx">
                    <span className="text-buy shrink-0">✓</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="rounded-xl border border-border bg-card px-5 py-4 mb-8">
          <p className="text-xs font-bold uppercase tracking-wider text-muted mb-2">How to get Pro</p>
          <p className="text-sm text-tx leading-relaxed">
            There&apos;s no self-serve checkout here — this is a self-hosted deployment, and
            Pro access is granted by whoever operates it (a database update, nothing more). If
            you need higher API limits, reach out to whoever runs this instance for you.
          </p>
        </div>

        <p className="text-xs text-muted">
          Manage your keys and see your current usage on the{' '}
          <Link href="/api-keys" className="text-accent hover:underline font-medium">API Keys</Link> page.
        </p>
    </PageShell>
  );
}
