'use client';

import { useEffect, useMemo, useState } from 'react';
import type { InsiderActivity, PeerComparison, PriceHistory, QuarterlyTrend, Report, StockInfo, StreetConsensus, VerdictHistoryEntry, VerdictHistoryResponse } from '@/types';
import InfoTooltip from './info-tooltip';
import Sparkline from './sparkline';
import WatchlistButton from './watchlist-button';

// Bridges ratio labels between two independent sources — the analyst-facing
// "research" task's own ratio names (e.g. "P/E", "ROCE") and Screener's peer
// table's column headers (e.g. "P/E", "ROCE %") — so a percentile computed
// against one set of labels can still be looked up against the other.
function normalizeRatioKey(key: string): string {
  return key.toLowerCase().replace(/[^a-z0-9]/g, '');
}

function usePeerComparison(symbol: string): PeerComparison | null {
  const [peers, setPeers] = useState<PeerComparison | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPeers(null);
    fetch(`/api/peers/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: PeerComparison | null) => { if (!cancelled) setPeers(data); })
      .catch(() => { if (!cancelled) setPeers(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return peers;
}

function PercentileBadge({ value }: { value: number }) {
  return (
    <span
      title={`${Math.round(value)}th percentile among sector peers`}
      className="ml-1.5 text-[9px] font-semibold px-1.5 py-0.5 rounded-full
                 bg-accent/10 text-accent border border-accent/20 whitespace-nowrap"
    >
      {Math.round(value)}th pct
    </span>
  );
}

// Where the stock's current P/E sits within its OWN last 3-5 years of
// Screener-published P/E — an absolute anchor alongside PercentileBadge's
// peer-relative reading above. Low percentile (cheap vs. its own history)
// reads as buy-toned, high (expensive vs. its own history) as sell-toned,
// same color convention as the BUY/HOLD/SELL badges elsewhere in this file.
function ValuationAnchorBadge({ anchor }: { anchor: PeerComparison['absolute_anchor'] }) {
  if (!anchor || anchor.years.length === 0) return null;
  const tone = anchor.percentile <= 33 ? 'text-buy border-buy/25 bg-buy/10'
    : anchor.percentile >= 67 ? 'text-sell border-sell/25 bg-sell/10'
    : 'text-hold border-hold/25 bg-hold/10';
  return (
    <div
      className={`mt-3 rounded-lg border px-3 py-2 text-xs ${tone}`}
      title={`Current P/E ${anchor.current_pe} vs. its own ${anchor.years[0]}–${anchor.years[anchor.years.length - 1]} range (low ${anchor.low}, median ${anchor.median}, high ${anchor.high})`}
    >
      <span className="font-semibold">P/E {anchor.current_pe}</span>
      {' — '}
      {Math.round(anchor.percentile)}th percentile of its own {anchor.years.length}-year range
      {' '}
      <span className="text-muted">({anchor.low}–{anchor.high}, median {anchor.median})</span>
    </div>
  );
}

function PeerTable({ peers }: { peers: PeerComparison | null }) {
  if (!peers || (!peers.self && peers.peers.length === 0)) return null;

  const rows = [
    ...(peers.self ? [{ ...peers.self, kind: 'self' as const }] : []),
    ...peers.peers.map(p => ({ ...p, kind: 'peer' as const })),
    ...(peers.sector_median ? [{ ...peers.sector_median, kind: 'median' as const }] : []),
  ];
  if (rows.length === 0) return null;

  // Column set is sector-dependent (Screener's own table drives it) — derive it
  // from whichever rows actually have values, in first-seen order, skipping the
  // row-label column ("Name") and any blank header (Screener's leading S.No. column).
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row.values)) {
      if (key && key !== 'Name' && !columns.includes(key)) columns.push(key);
    }
  }
  if (columns.length === 0) return null;

  return (
    <Card title="Peer Comparison">
      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left font-semibold text-muted px-1 py-1.5 whitespace-nowrap">Name</th>
              {columns.map(col => (
                <th key={col} className="text-right font-semibold text-muted px-1 py-1.5 whitespace-nowrap">{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={`${row.kind}-${row.name}-${i}`}
                className={`border-b border-border/60 last:border-0 ${row.kind === 'self' ? 'bg-accent/5' : ''}`}
              >
                <td className={`px-1 py-1.5 whitespace-nowrap ${row.kind === 'self' ? 'font-semibold text-accent' : row.kind === 'median' ? 'text-muted italic' : 'text-tx'}`}>
                  {row.name}
                </td>
                {columns.map(col => (
                  <td key={col} className="text-right px-1 py-1.5 font-mono text-tx whitespace-nowrap">
                    {row.values[col] ?? '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {peers.peers.length === 0 && (
        <p className="text-xs text-muted mt-2">
          Screener doesn&apos;t list sector peers for this stock, or the peer comparison table wasn&apos;t available.
        </p>
      )}
      <ValuationAnchorBadge anchor={peers.absolute_anchor} />
    </Card>
  );
}

// Raw-rupee formatter for insider-trade/bulk-deal values (L/Cr), distinct
// from fmtCr() above which expects a value already denominated in crores
// (e.g. market_cap_cr) — these come off the wire as plain rupee amounts.
function fmtInr(n: number): string {
  if (n >= 1_00_00_000) return `₹${(n / 1_00_00_000).toFixed(1)} Cr`;
  if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(1)}L`;
  return `₹${n.toLocaleString('en-IN')}`;
}

function fmtActivityDate(dateIso: string | null, fallback: string): string {
  if (!dateIso) return fallback;
  return new Date(dateIso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function ActionBadge({ action }: { action: 'BUY' | 'SELL' }) {
  return (
    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border whitespace-nowrap ${
      action === 'BUY' ? 'bg-buy/12 text-buy border-buy/25' : 'bg-sell/12 text-sell border-sell/25'
    }`}>
      {action}
    </span>
  );
}

function useInsiderActivity(symbol: string): InsiderActivity | null {
  const [activity, setActivity] = useState<InsiderActivity | null>(null);

  useEffect(() => {
    let cancelled = false;
    setActivity(null);
    fetch(`/api/insider-activity/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: InsiderActivity | null) => { if (!cancelled) setActivity(data); })
      .catch(() => { if (!cancelled) setActivity(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return activity;
}

// Promoter/director insider trades and institutional bulk/block deals — the
// same NSE feeds Market Picks already scrapes for discovery, surfaced here
// directly for whichever one stock a researcher is looking at, instead of
// only ever showing up (via LLM extraction) when a stock happens to make the
// weekly picks list. Renders nothing when NSE has nothing for this symbol —
// most stocks won't have recent activity, which is the expected common case.
function InsiderActivityCard({ symbol }: { symbol: string }) {
  const activity = useInsiderActivity(symbol);
  if (!activity) return null;
  const { insider_trades: trades, bulk_block_deals: deals } = activity;
  if (trades.length === 0 && deals.length === 0) return null;

  return (
    <Card title={<>
      Insider &amp; Institutional Activity
      <InfoTooltip title="Insider & Institutional Activity" align="left">
        <p>Promoter/director trades disclosed to NSE (PIT filings) and large institutional bulk/block deals for this stock.</p>
        <p>Most stocks have no recent activity — that&apos;s the common case, not missing data.</p>
      </InfoTooltip>
    </>}>
      {trades.length > 0 && (
        <div className={deals.length > 0 ? 'mb-4' : ''}>
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2">Insider Trades</p>
          <div className="space-y-2">
            {trades.map((t, i) => (
              <div key={i} className="flex items-center justify-between gap-2 text-xs">
                <div className="min-w-0 flex items-center gap-1.5">
                  <ActionBadge action={t.action} />
                  <span className="text-tx truncate" title={`${t.person} (${t.category})`}>{t.person}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0 font-mono text-muted">
                  <span>{fmtInr(t.value)}</span>
                  <span className="text-muted/60">{fmtActivityDate(t.date_iso, t.date)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {deals.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2">Bulk &amp; Block Deals</p>
          <div className="space-y-2">
            {deals.map((d, i) => (
              <div key={i} className="flex items-center justify-between gap-2 text-xs">
                <div className="min-w-0 flex items-center gap-1.5">
                  <ActionBadge action={d.action} />
                  <span className="text-tx truncate" title={`${d.client} (${d.deal_type})`}>{d.client}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0 font-mono text-muted">
                  <span>{fmtInr(d.price * d.quantity)}</span>
                  <span className="text-muted/60">{fmtActivityDate(d.date_iso, d.date)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function fmtConsensusDate(publishedAt: string | null): string | null {
  if (!publishedAt) return null;
  const d = new Date(publishedAt);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function useStreetConsensus(symbol: string): StreetConsensus | null {
  const [consensus, setConsensus] = useState<StreetConsensus | null>(null);

  useEffect(() => {
    let cancelled = false;
    setConsensus(null);
    fetch(`/api/street-consensus/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: StreetConsensus | null) => { if (!cancelled) setConsensus(data); })
      .catch(() => { if (!cancelled) setConsensus(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return consensus;
}

// Recent Trendlyne-cited analyst commentary for this stock — real article
// titles/links/dates, deliberately never a fabricated consensus rating or
// target price number (this module searches GNews for articles that cite
// Trendlyne, it doesn't scrape trendlyne.com's own numeric estimates).
// Renders nothing when there's no recent coverage — the expected common
// case for most stocks on most days, not missing data.
function StreetConsensusCard({ symbol }: { symbol: string }) {
  const consensus = useStreetConsensus(symbol);
  if (!consensus || consensus.articles.length === 0) return null;

  return (
    <Card title={<>
      Street Consensus
      <InfoTooltip title="Street Consensus" align="left">
        <p>Recent news coverage that cites Trendlyne&apos;s aggregated analyst consensus for this stock — upgrades, initiations, and target-price moves.</p>
        <p>This is real article coverage, not a scraped numeric rating or target price — AlphaPulse never invents a benchmark it doesn&apos;t actually have.</p>
      </InfoTooltip>
    </>}>
      <div className="space-y-2.5">
        {consensus.articles.slice(0, 6).map((a, i) => {
          const date = fmtConsensusDate(a.published_at);
          return (
            <a
              key={i}
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block text-xs hover:bg-card-hi/60 rounded-lg px-2 py-1.5 -mx-2 transition-colors"
            >
              <p className="text-tx line-clamp-2">{a.title}</p>
              {date && <p className="text-muted/60 mt-0.5">{date}</p>}
            </a>
          );
        })}
      </div>
    </Card>
  );
}

function PriceSparkline({ symbol }: { symbol: string }) {
  const [history, setHistory] = useState<PriceHistory | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/prices/history/${encodeURIComponent(symbol)}?days=180`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: PriceHistory | null) => { if (!cancelled) setHistory(data); })
      .catch(() => { if (!cancelled) setHistory(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  if (!history || history.closes.length < 2) return null;

  return (
    <div className="flex flex-col items-end gap-1 shrink-0">
      <span className="text-[9px] font-semibold text-muted uppercase tracking-wider">6M trend</span>
      <Sparkline closes={history.closes} width={110} height={30} />
    </div>
  );
}

function useVerdictHistory(symbol: string): VerdictHistoryResponse | null {
  const [data, setData] = useState<VerdictHistoryResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    fetch(`/api/verdict-history/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: VerdictHistoryResponse | null) => { if (!cancelled) setData(data); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return data;
}

const TIMELINE_REC_CLS: Record<string, string> = {
  BUY:  'bg-buy/12 text-buy border-buy/25',
  HOLD: 'bg-hold/12 text-hold border-hold/25',
  SELL: 'bg-sell/12 text-sell border-sell/25',
};

// How today's call compares to past ones for the same stock — a strip of the
// daily verdict snapshots verdict_history.save_snapshot() writes after every
// analysis run. Needs at least two points to be a "timeline" at all, so a
// symbol analysed for the first time today renders nothing here. Each badge
// also carries a win/loss mark scored against today's live price (BUY/SELL
// only — a HOLD makes no directional claim, so it's never graded), the
// single-stock analogue of the win-rate Market Picks already tracks for
// itself.
function VerdictTimeline({ symbol }: { symbol: string }) {
  const data = useVerdictHistory(symbol);
  const history = data?.history ?? [];
  if (history.length < 2) return null;

  return (
    <div className="px-6 py-2.5 border-t border-border/60">
      <div className="flex items-center gap-3 overflow-x-auto">
        <span className="text-[10px] font-semibold text-muted uppercase tracking-wider shrink-0">
          Verdict Timeline
          {data?.scored_count ? (
            <span className="ml-1.5 font-normal normal-case text-muted/70">
              &middot; {data.win_rate}% right so far ({data.scored_count} scored)
            </span>
          ) : null}
        </span>
        <div className="flex items-center gap-1.5 min-w-0">
          {history.map((h, i) => {
            const isLast = i === history.length - 1;
            const cls = (h.recommendation && TIMELINE_REC_CLS[h.recommendation]) || 'bg-card-hi text-muted border-border';
            const outcomeMark = h.outcome === 'win' ? '✓' : h.outcome === 'loss' ? '✗' : null;
            const tooltip = [
              h.date,
              h.current_price != null ? `₹${fmt(h.current_price)}` : null,
              h.confidence ? `${h.confidence} confidence` : null,
              h.return_since_pct != null
                ? `${h.return_since_pct >= 0 ? '+' : ''}${h.return_since_pct}% since`
                : null,
            ].filter(Boolean).join(' · ');
            return (
              <div key={h.date} className="flex items-center gap-1.5 shrink-0">
                {i > 0 && <span className="text-muted/30 text-xs">→</span>}
                <span
                  title={tooltip}
                  className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold border whitespace-nowrap ${cls} ${
                    isLast ? 'ring-1 ring-accent/40' : ''
                  }`}
                >
                  {h.recommendation ?? '—'}
                  <span className="font-normal opacity-70">
                    {new Date(h.date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}
                  </span>
                  {outcomeMark && (
                    <span
                      aria-label={h.outcome === 'win' ? 'Correct so far' : 'Incorrect so far'}
                      className={h.outcome === 'win' ? 'text-buy' : 'text-sell'}
                    >
                      {outcomeMark}
                    </span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// Two mini-sparklines (Revenue, EPS) over the last few quarters — reuses the
// Sparkline component built for the price-history strip in the hero, just
// fed a different numeric series each time.
function QuarterlyTrendCard({ trend }: { trend: QuarterlyTrend | undefined }) {
  if (!trend || trend.quarters.length < 2) return null;
  const latest = trend.quarters[trend.quarters.length - 1];

  return (
    <Card title="Quarterly Trend">
      <p className="text-[11px] text-muted mb-3">Last {trend.quarters.length} quarters, through {latest}</p>
      <div className="space-y-4">
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-muted">Revenue</span>
            <span className="font-mono font-semibold text-tx text-sm">
              ₹{fmt(trend.revenue[trend.revenue.length - 1], 0)} Cr
            </span>
          </div>
          <Sparkline
            closes={trend.revenue}
            width={220}
            height={32}
            ariaLabel={`Quarterly revenue trend over the last ${trend.quarters.length} quarters`}
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-muted">EPS</span>
            <span className="font-mono font-semibold text-tx text-sm">
              ₹{fmt(trend.eps[trend.eps.length - 1], 2)}
            </span>
          </div>
          <Sparkline
            closes={trend.eps}
            width={220}
            height={32}
            ariaLabel={`Quarterly EPS trend over the last ${trend.quarters.length} quarters`}
          />
        </div>
        {trend.operating_margin && trend.operating_margin.length === trend.quarters.length && (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-sm text-muted">Operating Margin</span>
              <span className="font-mono font-semibold text-tx text-sm">
                {fmt(trend.operating_margin[trend.operating_margin.length - 1], 1)}%
              </span>
            </div>
            <Sparkline
              closes={trend.operating_margin}
              width={220}
              height={32}
              ariaLabel={`Quarterly operating margin trend over the last ${trend.quarters.length} quarters`}
            />
          </div>
        )}
      </div>
    </Card>
  );
}

interface Props {
  report: Report;
  onHardRefresh?: () => void;
}

type FactorValue = string | number | null | undefined;
type FactorShape = string | Record<string, FactorValue>;

function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return '—';
  return n.toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtCr(n: number | null | undefined) {
  if (n == null) return '—';
  if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(2)}L Cr`;
  if (n >= 1_000)    return `₹${(n / 1_000).toFixed(2)}K Cr`;
  return `₹${fmt(n)} Cr`;
}

const REC_CONFIG = {
  BUY:  { bg: 'bg-buy/10',  border: 'border-buy/30',  text: 'text-buy',  badge: 'bg-buy  text-white', strip: 'bg-buy'  },
  SELL: { bg: 'bg-sell/10', border: 'border-sell/30', text: 'text-sell', badge: 'bg-sell text-white', strip: 'bg-sell' },
  HOLD: { bg: 'bg-hold/10', border: 'border-hold/30', text: 'text-hold', badge: 'bg-hold text-white', strip: 'bg-hold' },
};

const CONF_COLOR: Record<string, string> = {
  HIGH: 'text-buy', MEDIUM: 'text-hold', LOW: 'text-sell',
};

const SENT_COLOR: Record<string, string> = {
  Positive: 'text-buy', Neutral: 'text-muted', Negative: 'text-sell',
};

function Card({ title, children, className = '' }: { title: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-card border border-border rounded-xl p-5 ${className}`}>
      <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-3 flex items-center gap-1.5">{title}</p>
      {children}
    </div>
  );
}

function MetricRow({ label, value, colorClass = 'text-tx', percentile }: {
  label: string; value: string; colorClass?: string; percentile?: number;
}) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className="flex items-center">
        <span className={`text-sm font-semibold font-mono ${colorClass}`}>{value}</span>
        {percentile != null && <PercentileBadge value={percentile} />}
      </span>
    </div>
  );
}

function formatScalar(value: FactorValue) {
  if (value == null || value === '') return null;
  return typeof value === 'number' ? fmt(value) : value;
}

function formatFactor(factor: FactorShape) {
  if (typeof factor === 'string') return factor;

  const primaryKeys = ['comment', 'metric', 'factor', 'factor1', 'factor2', 'factor3', 'data point', 'value'];
  const seen = new Set<string>();
  const parts: string[] = [];

  for (const key of primaryKeys) {
    const value = formatScalar(factor[key]);
    if (value) {
      parts.push(String(value));
      seen.add(key);
    }
  }

  for (const [key, rawValue] of Object.entries(factor)) {
    if (seen.has(key)) continue;
    const value = formatScalar(rawValue);
    if (!value) continue;
    parts.push(`${key}: ${value}`);
  }

  return parts.join(' • ') || '—';
}

function formatAge(dateStr: string): string {
  const today     = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  if (dateStr === today)     return 'Updated today';
  if (dateStr === yesterday) return 'Updated yesterday';
  return `Updated ${new Date(dateStr).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}`;
}

function formatNewsHighlights(highlights: Report['analysis']['news_highlights']) {
  if (Array.isArray(highlights)) return highlights.join(' • ');
  return highlights ?? '';
}

function ExchangeTable({
  quotes,
  primaryExchange,
}: {
  quotes: Array<[string, Partial<StockInfo>]>;
  primaryExchange: string;
}) {
  if (quotes.length === 1) {
    const [exchange, q] = quotes[0];
    const change    = q?.change_pct ?? 0;
    const changeCls = change > 0 ? 'text-buy' : change < 0 ? 'text-sell' : 'text-muted';
    return (
      <div className="text-right shrink-0">
        <p className="text-2xl font-bold font-mono text-tx">
          {q?.current_price != null ? `₹${fmt(q.current_price)}` : '—'}
        </p>
        <p className={`text-sm font-mono ${changeCls}`}>
          {`${change > 0 ? '+' : ''}${fmt(change)}%`}
        </p>
        <p className="text-[11px] text-muted mt-0.5">{exchange}</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden text-sm min-w-[260px] shrink-0">
      {quotes.map(([exchange, q]) => {
        const change    = q?.change_pct ?? 0;
        const changeCls = change > 0 ? 'text-buy' : change < 0 ? 'text-sell' : 'text-muted';
        const isPrimary = exchange === primaryExchange;
        return (
          <div
            key={exchange}
            className={`flex items-center justify-between px-4 py-2.5 border-b border-border last:border-0 ${
              isPrimary ? 'bg-accent/5' : ''
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-[11px] font-semibold text-muted">{exchange}</span>
              {isPrimary && (
                <span className="text-[10px] font-semibold uppercase tracking-wide text-accent">Primary</span>
              )}
            </div>
            <div className="text-right">
              <span className="font-mono font-bold text-tx">
                {q?.current_price != null ? `₹${fmt(q.current_price)}` : '—'}
              </span>
              <span className={`ml-3 font-mono text-xs ${changeCls}`}>
                {`${change > 0 ? '+' : ''}${fmt(change)}%`}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RangeBar({ low, current, high }: { low: number; current: number; high: number }) {
  const pct = high === low ? 50 : Math.max(0, Math.min(100, ((current - low) / (high - low)) * 100));
  return (
    <div className="mt-3">
      <div className="relative h-1.5 rounded-full bg-border">
        <div className="absolute inset-0 rounded-full bg-gradient-to-r from-sell/25 via-hold/25 to-buy/25" />
        <div
          className="absolute top-1/2 w-2 h-2 rounded-full bg-tx border border-bg"
          style={{ left: `${pct}%`, transform: 'translate(-50%, -50%)' }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-muted/60 mt-1">
        <span>₹{fmt(low, 0)}</span>
        <span>52W Range</span>
        <span>₹{fmt(high, 0)}</span>
      </div>
    </div>
  );
}

function fmtRatio(raw: string): string {
  const s = String(raw).trim();
  if (/[a-zA-Z%]/.test(s)) return s;
  const n = parseFloat(s.replace(/,/g, ''));
  if (!isNaN(n) && isFinite(n)) return fmt(n, s.includes('.') ? 2 : 0);
  return s;
}

function summaryBullets(text: string): string[] {
  // Split only where a sentence ends: [.!?] followed by whitespace + uppercase letter.
  // This avoids splitting on decimal numbers (5.04) or mid-sentence abbreviations.
  const sentences = text.split(/(?<=[.!?])\s+(?=[A-Z])/);
  return sentences.map(s => s.trim()).filter(s => s.length > 5);
}

export default function ResultsDashboard({ report, onHardRefresh }: Props) {
  const { analysis: a, signals: sig, stock_info: s, research: r, news, holdings: h, filings } = report;

  const peers = usePeerComparison(report.symbol);
  const percentileByNormalizedKey = useMemo(() => {
    const map: Record<string, number> = {};
    for (const [key, value] of Object.entries(peers?.percentiles ?? {})) {
      map[normalizeRatioKey(key)] = value;
    }
    return map;
  }, [peers]);

  const rec = (a?.recommendation ?? 'HOLD') as 'BUY' | 'SELL' | 'HOLD';
  const cfg = REC_CONFIG[rec];
  const exchangeQuotes: Array<[string, Partial<StockInfo>]> =
    s?.prices_by_exchange && Object.keys(s.prices_by_exchange).length > 0
      ? Object.entries(s.prices_by_exchange) as Array<[string, NonNullable<typeof s.prices_by_exchange>[string]]>
      : (s ? [[s.exchange ?? 'NSE', s]] : []);
  const primaryExchange = s?.primary_exchange ?? s?.exchange ?? 'NSE';

  return (
    <div className="animate-fade-up space-y-5">

      {/* ── 1. Hero strip: identity · verdict · price ── */}
      <div className={`rounded-xl border overflow-hidden ${cfg.border} ${cfg.bg}`}>
        <div className={`h-0.5 ${cfg.strip}`} />
        <div className="px-6 py-5 flex flex-wrap items-center justify-between gap-6">

          {/* Identity */}
          <div className="min-w-0">
            <div className="flex items-baseline gap-2.5 flex-wrap">
              <WatchlistButton
                symbol={report.symbol}
                company={s?.company_name ?? report.symbol}
                exchange={s?.exchange ?? 'NSE'}
              />
              <h2 className="text-xl font-bold text-tx">{s?.company_name ?? report.symbol}</h2>
              {s?.company_name && (
                <span className="font-mono text-sm font-semibold text-muted">{report.symbol}</span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
                {exchangeQuotes.length > 1 ? 'NSE + BSE' : (s?.exchange ?? 'NSE')}
              </span>
              {s?.industry && <span className="text-xs text-muted">{s.industry}</span>}
              {s?.sector && s.sector !== s.industry && (
                <span className="text-xs text-muted/50">· {s.sector}</span>
              )}
              {report.generated_at && (
                <span className="text-xs text-muted/60">· {formatAge(report.generated_at)}</span>
              )}
            </div>
          </div>

          {/* Verdict */}
          <div className="flex flex-col items-center gap-2 shrink-0">
            <div className="flex items-center gap-1.5">
              <span className={`text-3xl font-black px-8 py-2.5 rounded-xl ${cfg.badge}`}>{rec}</span>
              <InfoTooltip title="What does this mean?">
                <p><span className="text-buy font-semibold">BUY</span> — bullish thesis, favorable valuation and signals.</p>
                <p><span className="text-hold font-semibold">HOLD</span> — mixed or fairly-valued; no strong edge either way.</p>
                <p><span className="text-sell font-semibold">SELL</span> — bearish thesis, unfavorable valuation or signals.</p>
              </InfoTooltip>
            </div>
            <div className="flex items-center gap-3">
              {a?.confidence && (
                <span className={`text-[11px] font-semibold tracking-widest uppercase ${CONF_COLOR[a.confidence]}`}>
                  {a.confidence} confidence
                </span>
              )}
              {onHardRefresh && (
                <button
                  onClick={onHardRefresh}
                  className="flex items-center gap-1 text-[11px] font-medium text-muted
                    hover:text-tx transition-colors duration-150"
                >
                  <span>↺</span><span>Refresh</span>
                </button>
              )}
            </div>
          </div>

          {/* Price */}
          <div className="flex items-center gap-4">
            <ExchangeTable quotes={exchangeQuotes} primaryExchange={primaryExchange} />
            <PriceSparkline symbol={report.symbol} />
          </div>
        </div>
        <VerdictTimeline symbol={report.symbol} />
      </div>

      {/* ── 2. Main grid: thesis (60%) + metrics (40%) ── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">

        {/* Investment Thesis — summary + bull/bear as one card */}
        <div className="lg:col-span-3">
          <Card title="Investment Thesis" className="h-full">
            {(() => {
              const text    = a?.summary ?? '';
              const bullets = summaryBullets(text);
              return bullets.length > 1 ? (
                <ul className="space-y-2 mb-5">
                  {bullets.map((b, i) => (
                    <li key={i} className="flex gap-2 text-sm text-tx leading-relaxed">
                      <span className={`${cfg.text} shrink-0 mt-px`}>›</span>
                      <span>{b}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-tx leading-relaxed mb-5">{text || 'Analysis pending.'}</p>
              );
            })()}

            <div className="grid grid-cols-2 gap-5 pt-4 border-t border-border">
              <div>
                <p className="text-[11px] font-semibold text-buy tracking-[1px] uppercase mb-3">Bull Case</p>
                <ul className="space-y-2">
                  {(a?.bull_factors ?? []).map((f, i) => (
                    <li key={i} className="flex gap-2 text-sm text-tx">
                      <span className="text-buy mt-0.5 shrink-0">▲</span>
                      <span>{formatFactor(f)}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-[11px] font-semibold text-sell tracking-[1px] uppercase mb-3">Bear Case</p>
                <ul className="space-y-2">
                  {(a?.bear_factors ?? []).map((f, i) => (
                    <li key={i} className="flex gap-2 text-sm text-tx">
                      <span className="text-sell mt-0.5 shrink-0">▼</span>
                      <span>{formatFactor(f)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Card>
        </div>

        {/* Key Metrics sidebar */}
        <div className="lg:col-span-2 space-y-4">
          <Card title="Key Metrics">
            <div className="grid grid-cols-3 gap-2 mb-4">
              {[
                { label: 'P/E', value: s?.pe_ratio      != null ? fmt(s.pe_ratio, 1)      : '—' },
                { label: 'P/B', value: s?.price_to_book != null ? fmt(s.price_to_book, 1) : '—' },
                { label: 'EPS', value: s?.eps            != null ? `₹${fmt(s.eps, 0)}`    : '—' },
              ].map(({ label, value }) => (
                <div key={label} className="bg-card-hi rounded-lg p-2.5 text-center">
                  <p className="text-xs text-muted uppercase tracking-wide mb-1">{label}</p>
                  <p className="font-mono font-bold text-lg text-tx leading-tight">{value}</p>
                </div>
              ))}
            </div>
            <MetricRow label="Market Cap" value={fmtCr(s?.market_cap_cr)} />
            <MetricRow label="Book Value" value={s?.book_value != null ? `₹${fmt(s.book_value)}` : '—'} />
            {s?.beta != null && <MetricRow label="Beta" value={fmt(s.beta, 2)} />}
            {s?.dividend_yield_pct != null && (
              <MetricRow label="Div Yield" value={`${fmt(s.dividend_yield_pct, 2)}%`} />
            )}
            <MetricRow label="52W High" value={s?.['52w_high'] != null ? `₹${fmt(s['52w_high'])}` : '—'} />
            <MetricRow label="52W Low"  value={s?.['52w_low']  != null ? `₹${fmt(s['52w_low'])}`  : '—'} />
            {s?.['52w_low'] != null && s?.['52w_high'] != null && s?.current_price != null && (
              <RangeBar low={s['52w_low']!} current={s.current_price} high={s['52w_high']!} />
            )}
          </Card>

          {r?.ratios && Object.keys(r.ratios).length > 0 && (
            <Card title="Fundamentals">
              {Object.entries(r.ratios).map(([k, v]) => (
                <MetricRow
                  key={k}
                  label={k}
                  value={fmtRatio(String(v))}
                  percentile={percentileByNormalizedKey[normalizeRatioKey(k)]}
                />
              ))}
            </Card>
          )}

          {(!r?.ratios || Object.keys(r.ratios).length === 0) && r?.nse_fallback_ratios && (
            <Card title="Fundamentals">
              <p className="text-xs text-muted mb-2">
                Screener.in had no ratios for this stock — showing EPS from NSE&apos;s own filings instead.
              </p>
              <MetricRow label="EPS" value={`₹${fmt(r.nse_fallback_ratios.eps, 2)}`} />
            </Card>
          )}

          <QuarterlyTrendCard trend={r?.quarterly_trend} />

          <PeerTable peers={peers} />

          <InsiderActivityCard symbol={report.symbol} />

          <StreetConsensusCard symbol={report.symbol} />

          {a?.valuation && (
            <Card title="Valuation">
              <p className={`text-sm font-semibold mb-1 ${
                a.valuation.verdict === 'Undervalued' ? 'text-buy' :
                a.valuation.verdict === 'Overvalued'  ? 'text-sell' : 'text-hold'
              }`}>{a.valuation.verdict}</p>
              <p className="text-sm text-muted leading-relaxed">{a.valuation.comment}</p>
            </Card>
          )}

          {sig?.signals && Object.keys(sig.signals).length > 0 && (
            <Card title={<>
              Quant Signals
              <InfoTooltip title="Quant Signals" align="left">
                <p>A composite score from valuation, growth, volume, filings, technical (RSI/EMA trend), and macro (FII/DII flow, RBI rate/inflation) signals, each scored −1 (bearish) to +1 (bullish) and blended into the Final Score.</p>
                <p>This runs independently of the AI analyst and is one input to its recommendation.</p>
              </InfoTooltip>
            </>}>
              {sig.final_score != null && (
                <MetricRow
                  label="Final Score"
                  value={fmt(sig.final_score, 2)}
                  colorClass={sig.final_score > 0 ? 'text-buy' : sig.final_score < 0 ? 'text-sell' : 'text-muted'}
                />
              )}
              {sig.verdict && (
                <MetricRow label="Signal Verdict" value={sig.verdict} />
              )}
              {Object.entries(sig.signals).map(([name, signal]) => (
                <MetricRow
                  key={name}
                  label={`${name} (${signal.value})`}
                  value={fmt(signal.score, 2)}
                  colorClass={signal.score > 0 ? 'text-buy' : signal.score < 0 ? 'text-sell' : 'text-muted'}
                />
              ))}
            </Card>
          )}
        </div>
      </div>

      {/* ── 3. Key Risks ── */}
      {(a?.key_risks ?? []).length > 0 && (
        <Card title="Key Risks">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {(a?.key_risks ?? []).map((risk, i) => (
              <div key={i} className="flex gap-2 text-sm bg-sell/5 border border-sell/15 rounded-lg px-3 py-2">
                <span className="text-sell shrink-0">⚠</span>
                <span className="text-tx">{risk}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* ── 4. Narrative row: context cards ── */}
      {(() => {
        const narrativeCards = [
          a?.business_quality && (
            <Card key="bq" title="Business Quality">
              <p className="text-sm text-tx leading-relaxed">{a.business_quality}</p>
            </Card>
          ),
          <Card key="it" title="Institutional Trend">
            <p className="text-sm text-tx leading-relaxed">{a?.institutional_trend ?? '—'}</p>
          </Card>,
          a?.news_sentiment && (
            <Card key="ns" title="News Sentiment">
              <p className={`text-sm font-semibold mb-1 ${SENT_COLOR[a.news_sentiment]}`}>
                {a.news_sentiment}
              </p>
              <p className="text-sm text-muted leading-relaxed">{formatNewsHighlights(a?.news_highlights)}</p>
            </Card>
          ),
        ].filter(Boolean);
        const colCls = narrativeCards.length === 3 ? 'sm:grid-cols-3' : narrativeCards.length === 2 ? 'sm:grid-cols-2' : '';
        return (
          <div className={`grid grid-cols-1 ${colCls} gap-4`}>{narrativeCards}</div>
        );
      })()}

      {/* ── 5. Data tables ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {h?.shareholding_pattern && Object.keys(h.shareholding_pattern).length > 0 && (
          <Card title="Shareholding Pattern">
            {h.pledge_pct != null && (
              <div className={`flex items-center justify-between text-sm mb-3 pb-3 border-b border-border ${
                h.pledge_pct > 0 ? 'text-sell' : 'text-muted'
              }`}>
                <span className="flex items-center gap-1.5">
                  {h.pledge_pct > 0 && <span aria-hidden="true">⚠</span>}
                  Promoter Pledge
                </span>
                <span className="font-mono font-semibold">{fmt(h.pledge_pct, 1)}%</span>
              </div>
            )}
            {Object.entries(h.shareholding_pattern).map(([k, v]) => (
              <div key={k} className="mb-2 last:mb-0">
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-muted">{k}</span>
                  <span className="font-mono font-semibold text-tx">{fmt(v, 1)}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-border overflow-hidden">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(v, 100)}%` }} />
                </div>
              </div>
            ))}
          </Card>
        )}

        {h?.mutual_funds && h.mutual_funds.length > 0 && (
          <Card title="Mutual Fund Holdings">
            <div className="divide-y divide-border">
              {h.mutual_funds.slice(0, 8).map((mf, i) => (
                <div key={i} className="flex items-center justify-between py-2">
                  <span className="text-sm text-tx">{mf.fund}</span>
                  <span className="text-sm font-mono font-semibold text-accent">{fmt(mf.holding_pct, 2)}%</span>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>

      {/* ── 6. News ── */}
      {news && news.length > 0 && (
        <Card title="Recent News">
          <div className="divide-y divide-border">
            {news.slice(0, 5).map((n, i) => (
              <a key={i} href={n.url} target="_blank" rel="noopener noreferrer"
                className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0 group"
              >
                <span className="text-sm text-tx group-hover:text-accent transition-colors leading-snug">
                  {n.title}
                </span>
                <span className="text-[11px] text-muted">{n.source} · {n.published_at} ↗</span>
              </a>
            ))}
          </div>
        </Card>
      )}

      {/* ── 6b. Filings ── */}
      {filings && filings.length > 0 && (
        <Card title="Corporate Filings">
          <div className="divide-y divide-border">
            {filings.slice(0, 5).map((f, i) => {
              const meta = [f.category, f.date].filter(Boolean).join(' · ');
              const body = (
                <>
                  <span className="text-sm text-tx group-hover:text-accent transition-colors leading-snug">
                    {f.title ?? 'Untitled filing'}
                  </span>
                  {(meta || f.attachment) && (
                    <span className="text-[11px] text-muted">{meta}{f.attachment ? ' ↗' : ''}</span>
                  )}
                </>
              );
              return f.attachment ? (
                <a key={i} href={f.attachment} target="_blank" rel="noopener noreferrer"
                  className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0 group"
                >
                  {body}
                </a>
              ) : (
                <div key={i} className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0">
                  {body}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* ── 7. About ── */}
      {r?.about && (
        <Card title="About">
          <p className="text-sm text-muted leading-relaxed">{r.about}</p>
        </Card>
      )}

      {/* ── 8. Disclaimer ── */}
      <p className="text-[11px] text-muted/60 leading-relaxed px-1">
        This {rec} verdict is generated by an AI model from public data and is for informational
        purposes only — it is not investment advice. Prices and fundamentals may be delayed or
        incomplete. Do your own research and consult a registered financial advisor before trading.
      </p>
    </div>
  );
}
