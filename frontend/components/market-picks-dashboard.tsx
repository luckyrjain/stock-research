'use client';

import React, { useState, useMemo, useEffect, useId } from 'react';
import type { MarketPick, PickSource } from '@/types';
import InfoTooltip from './info-tooltip';
import WatchlistButton from './watchlist-button';
import PositionButton from './position-button';
import { usePositions } from '@/lib/positions';

type SortKey    = 'confidence_score' | 'change_pct' | 'pe_ratio' | 'valuation_percentile';
type ConfFilter = 'all' | 'high' | 'medium' | 'low';
type HorizonFilter = 'all' | 'short' | 'medium' | 'long';
type CapFilter  = 'all' | 'large' | 'mid' | 'small';

// Standard Indian market-cap conventions, in ₹ Cr.
const CAP_THRESHOLDS = { large: 20_000, mid: 5_000 } as const;

function capBucket(marketCapCr: number | null): 'large' | 'mid' | 'small' | null {
  if (marketCapCr == null) return null;
  if (marketCapCr >= CAP_THRESHOLDS.large) return 'large';
  if (marketCapCr >= CAP_THRESHOLDS.mid)   return 'mid';
  return 'small';
}

interface Props {
  picks: MarketPick[];
  generatedAt: string;
  fromCache?: boolean;
  onRescan: () => void;
  pricesLastUpdated?: Date | null;
}

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtPrice(n: number | null | undefined): string {
  if (n == null) return '—';
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ConfidenceBar({ score }: { score: number }) {
  const [bar, text] =
    score >= 70 ? ['bg-buy',  'text-buy']  :
    score >= 45 ? ['bg-hold', 'text-hold'] :
                  ['bg-sell', 'text-sell'];
  return (
    <div className="flex items-center gap-2.5 min-w-[108px]">
      <div className="flex-1 h-1.5 rounded-full bg-muted/15 overflow-hidden">
        <div className={`h-full rounded-full ${bar} transition-all duration-500`}
             style={{ width: `${score}%` }} />
      </div>
      <span className={`text-[11px] font-bold font-mono tabular-nums w-7 text-right ${text}`}>
        {score.toFixed(0)}
      </span>
    </div>
  );
}

function SignalBadge({ verdict }: { verdict: MarketPick['recommendation'] }) {
  const cfg: Record<MarketPick['recommendation'], string> = {
    BUY:       'bg-buy/12 text-buy border-buy/25',
    WATCHLIST: 'bg-buy/8 text-buy/75 border-buy/15',
    HOLD:      'bg-hold/12 text-hold border-hold/25',
    SELL:      'bg-sell/12 text-sell border-sell/25',
  };
  const labels: Record<MarketPick['recommendation'], string> = {
    BUY: 'BUY', WATCHLIST: 'WATCH', HOLD: 'HOLD', SELL: 'SELL',
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold
                      border tracking-wide ${cfg[verdict]}`}>
      {labels[verdict]}
    </span>
  );
}

function HorizonBadge({ horizon }: { horizon?: 'short' | 'medium' | 'long' }) {
  if (!horizon) return null;
  const cfg = {
    short:  { cls: 'text-hold/80 border-hold/20 bg-hold/8',   label: 'Short',  title: '< 3 months' },
    medium: { cls: 'text-accent/80 border-accent/20 bg-accent/8', label: 'Mid', title: '3–12 months' },
    long:   { cls: 'text-buy/80 border-buy/20 bg-buy/8',      label: 'Long',   title: '1+ years' },
  }[horizon];
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold
                      border ${cfg.cls}`} title={cfg.title}>
      {cfg.label}
    </span>
  );
}

function IpoBadge({ isRecent }: { isRecent: boolean }) {
  if (!isRecent) return null;
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold
                     bg-accent/15 text-accent border border-accent/30"
          title="Recently listed (IPO < 8 months)">
      IPO
    </span>
  );
}

function TrendBadge({ trend, delta }: { trend: MarketPick['trend']; delta: number | null }) {
  if (trend === 'new' || trend === 'stable') return null;
  const rising = trend === 'rising';
  return (
    <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold
      ${rising ? 'bg-buy/10 text-buy border border-buy/20' : 'bg-sell/10 text-sell border border-sell/20'}`}
          title={delta != null ? `${rising ? '+' : ''}${delta} vs recent avg` : undefined}>
      {rising ? '↑' : '↓'} {trend}
    </span>
  );
}

function RankBadge({ rank }: { rank: number }) {
  if (rank === 1) {
    return (
      <div className="w-6 h-6 rounded-full bg-accent flex items-center justify-center
                      text-[10px] font-black text-white shadow-sm shadow-accent/30">
        1
      </div>
    );
  }
  if (rank <= 3) {
    return (
      <div className="w-6 h-6 rounded-full bg-accent/20 flex items-center justify-center
                      text-[10px] font-black text-accent">
        {rank}
      </div>
    );
  }
  return <span className="text-xs font-semibold text-muted/50 tabular-nums pl-1">{rank}</span>;
}

function SourcesPopover({ sources }: { sources: PickSource[] }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open]);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${sources.length} source${sources.length !== 1 ? 's' : ''} reported this pick`}
        className="flex items-center gap-1 px-2.5 py-1 rounded-full
                   bg-accent/10 text-accent text-[11px] font-semibold
                   border border-accent/20 hover:bg-accent/20 transition-colors"
      >
        {sources.length}
        <span className="text-accent/60 font-normal">firm{sources.length !== 1 ? 's' : ''}</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            id={panelId}
            role="dialog"
            aria-label="Reported by"
            className="absolute left-0 top-full mt-2 z-20 w-64 bg-card border border-border
                          rounded-xl shadow-2xl shadow-black/60 overflow-hidden">
            <div className="px-3 py-2.5 border-b border-border">
              <span className="text-[10px] font-bold text-muted uppercase tracking-wider">Reported by</span>
            </div>
            <ul className="divide-y divide-border max-h-52 overflow-y-auto">
              {sources.map((s, i) => (
                <li key={i} className="px-3 py-2.5 hover:bg-surface transition-colors">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider
                      ${s.type === 'brokerage' ? 'bg-accent/15 text-accent' :
                        s.type === 'platform'  ? 'bg-hold/15 text-hold' :
                        'bg-muted/15 text-muted'}`}>
                      {s.type}
                    </span>
                    {s.direction && s.direction !== 'BUY' && (
                      <span className={`text-[9px] font-bold px-1 py-0.5 rounded border
                        ${s.direction === 'SELL' ? 'text-sell border-sell/30 bg-sell/8' : 'text-muted border-muted/20'}`}>
                        {s.direction}
                      </span>
                    )}
                    <span className="text-xs text-tx font-medium leading-tight">{s.name}</span>
                  </div>
                  {s.headline && (
                    <p className="text-[10px] text-muted leading-snug line-clamp-2">{s.headline}</p>
                  )}
                  {s.url && (
                    <a href={s.url} target="_blank" rel="noopener noreferrer"
                       className="text-[10px] text-accent hover:underline mt-0.5 block">
                      View article →
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function TradeBox({ pick }: { pick: MarketPick }) {
  const p = (n: number | null) =>
    n != null ? `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}` : '—';

  const upside =
    pick.target_price != null && pick.current_price != null && pick.current_price > 0
      ? (((pick.target_price - pick.current_price) / pick.current_price) * 100).toFixed(1)
      : null;

  const riskReward =
    pick.target_price != null && pick.stop_loss != null && pick.entry_price != null &&
    pick.entry_price > pick.stop_loss
      ? ((pick.target_price - pick.entry_price) / (pick.entry_price - pick.stop_loss)).toFixed(1)
      : null;

  return (
    <div className="rounded-xl border border-border bg-bg overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-surface/50 flex items-center justify-between gap-2">
        <span className="text-[10px] font-bold text-muted uppercase tracking-widest">Trade Setup</span>
        <div className="flex items-center gap-2">
          {riskReward && (
            <span className="text-[10px] font-semibold text-accent bg-accent/10 px-2 py-0.5 rounded-full border border-accent/20">
              R:R = 1 : {riskReward}
            </span>
          )}
          <PositionButton
            symbol={pick.symbol}
            company={pick.company}
            exchange={pick.exchange}
            entry_price={pick.entry_price}
            target_price={pick.target_price}
            stop_loss={pick.stop_loss}
          />
        </div>
      </div>
      <div className="grid grid-cols-3 divide-x divide-border">
        <div className="px-4 py-3.5">
          <div className="text-[9px] text-muted/70 mb-1.5 uppercase tracking-widest">Buy at</div>
          <div className="text-sm font-black text-tx tabular-nums">{p(pick.entry_price)}</div>
          <div className="text-[10px] text-muted/60 mt-0.5">entry</div>
        </div>
        <div className="px-4 py-3.5">
          <div className="text-[9px] text-muted/70 mb-1.5 uppercase tracking-widest">Target</div>
          <div className="text-sm font-black text-buy tabular-nums">{p(pick.target_price)}</div>
          {upside && <div className="text-[10px] text-buy/60 mt-0.5">+{upside}% upside</div>}
        </div>
        <div className="px-4 py-3.5">
          <div className="text-[9px] text-muted/70 mb-1.5 uppercase tracking-widest">Stop Loss</div>
          <div className="text-sm font-black text-sell tabular-nums">{p(pick.stop_loss)}</div>
          {pick.stop_loss != null && pick.entry_price != null && pick.entry_price > 0 && (
            <div className="text-[10px] text-sell/60 mt-0.5">
              −{(((pick.entry_price - pick.stop_loss) / pick.entry_price) * 100).toFixed(1)}% risk
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ExpandedRow({ pick }: { pick: MarketPick }) {
  return (
    <tr>
      <td colSpan={10} className="border-b border-border">
        <div className="px-6 py-5 bg-card/60 space-y-5">
          <TradeBox pick={pick} />

          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {/* Summary */}
            <div className="md:col-span-3">
              <p className="text-sm text-tx/85 leading-relaxed">{pick.summary || '—'}</p>
            </div>

            {/* Bull case */}
            <div>
              <h4 className="text-[10px] font-bold text-buy/80 uppercase tracking-widest mb-2.5">
                Bull case
              </h4>
              <div className="space-y-1.5">
                {pick.bull_factors.length ? pick.bull_factors.map((f, i) => (
                  <div key={i} className="flex gap-2 text-xs text-tx/75 leading-snug">
                    <span className="text-buy mt-0.5 shrink-0 text-[10px]">▲</span>
                    <span>{f}</span>
                  </div>
                )) : <span className="text-xs text-muted">—</span>}
              </div>
            </div>

            {/* Bear case */}
            <div>
              <h4 className="text-[10px] font-bold text-sell/80 uppercase tracking-widest mb-2.5">
                Bear case
              </h4>
              <div className="space-y-1.5">
                {pick.bear_factors.length ? pick.bear_factors.map((f, i) => (
                  <div key={i} className="flex gap-2 text-xs text-tx/75 leading-snug">
                    <span className="text-sell mt-0.5 shrink-0 text-[10px]">▼</span>
                    <span>{f}</span>
                  </div>
                )) : <span className="text-xs text-muted">—</span>}
              </div>
            </div>

            {/* Metrics + ranking */}
            <div className="space-y-4">
              {pick.ranking_reasons?.length > 0 && (
                <div>
                  <h4 className="text-[10px] font-bold text-accent/80 uppercase tracking-widest mb-2.5">
                    Why ranked here
                  </h4>
                  <div className="space-y-1">
                    {pick.ranking_reasons.map((r, i) => (
                      <div key={i} className="flex gap-2 text-xs text-tx/75 leading-snug">
                        <span className="text-accent/60 shrink-0">›</span>
                        <span>{r}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <h4 className="text-[10px] font-bold text-muted/60 uppercase tracking-widest mb-2.5">
                  Key metrics
                </h4>
                <div className="grid grid-cols-2 gap-1.5">
                  {[
                    ['Conviction',   (pick.action_score * 100).toFixed(0) + '%'],
                    ['Quant signal', pick.signal_verdict],
                    ['Market cap',   pick.market_cap_cr != null ? `₹${pick.market_cap_cr.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr` : '—'],
                    ['P/E ratio',    pick.pe_ratio != null ? pick.pe_ratio.toFixed(1) : '—'],
                    ['Valuation',    pick.valuation_percentile != null
                                       ? `${pick.valuation_percentile.toFixed(0)}th pctl${pick.valuation_percentile <= 33 ? ' (cheap)' : pick.valuation_percentile >= 67 ? ' (rich)' : ''}`
                                       : '—'],
                    ['Exchange',     pick.exchange],
                    ['Sector',       pick.sector],
                    ['Signal score', pick.signal_score.toFixed(2)],
                  ].map(([label, value]) => (
                    <div key={label} className="bg-surface rounded-lg px-2.5 py-2 border border-border/40">
                      <div className="text-[9px] text-muted/60 uppercase tracking-wider">{label}</div>
                      <div className="text-xs font-semibold text-tx mt-0.5 tabular-nums">{value}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="flex justify-end pt-1">
            <a href={`/?symbol=${pick.symbol}`}
               className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold
                          bg-accent/10 text-accent border border-accent/25 hover:bg-accent/20 transition-colors">
              Full deep-dive analysis →
            </a>
          </div>
        </div>
      </td>
    </tr>
  );
}

function SortableHeader({ label, sortK, currentKey, currentDir, onSort, tooltip }: {
  label: string;
  sortK: SortKey;
  currentKey: SortKey | null;
  currentDir: 'asc' | 'desc';
  onSort: (k: SortKey) => void;
  tooltip?: React.ReactNode;
}) {
  const active = currentKey === sortK;
  return (
    <th
      aria-sort={active ? (currentDir === 'desc' ? 'descending' : 'ascending') : 'none'}
      className="p-0 text-left text-[10px] font-bold text-muted uppercase tracking-wider whitespace-nowrap"
    >
      <div className="flex items-center gap-1">
        {/* Button owns the original cell padding so the full header area stays
            clickable/hoverable, not just the label+arrow's own tight bounding box. */}
        <button
          type="button"
          onClick={() => onSort(sortK)}
          className="flex items-center gap-1 px-4 py-3 uppercase tracking-wider
                     cursor-pointer hover:text-tx transition-colors select-none group"
        >
          {label}
          <span className={`text-[9px] transition-colors ${active ? 'text-accent' : 'text-muted/25 group-hover:text-muted/60'}`}>
            {active ? (currentDir === 'desc' ? '↓' : '↑') : '↕'}
          </span>
        </button>
        {tooltip && <span className="pr-4">{tooltip}</span>}
      </div>
    </th>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function MarketPicksDashboard({ picks, generatedAt, fromCache, onRescan, pricesLastUpdated }: Props) {
  const { isPositioned } = usePositions();
  const [expanded,   setExpanded]   = useState<Set<string>>(new Set());
  const [sortKey,    setSortKey]    = useState<SortKey | null>(null);
  const [sortDir,    setSortDir]    = useState<'desc' | 'asc'>('desc');
  const [search,     setSearch]     = useState('');
  const [confFilter,    setConfFilter]    = useState<ConfFilter>('all');
  const [sectorFilter,  setSectorFilter]  = useState<string>('all');
  const [horizonFilter, setHorizonFilter] = useState<HorizonFilter>('all');
  const [capFilter,     setCapFilter]     = useState<CapFilter>('all');

  const sectors = useMemo(
    () => Array.from(new Set(picks.map(p => p.sector).filter(Boolean))).sort(),
    [picks],
  );

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(k); setSortDir('desc'); }
  }

  const displayed = useMemo(() => {
    let out = picks;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      out = out.filter(p => p.symbol.toLowerCase().includes(q) || p.company.toLowerCase().includes(q));
    }
    if (confFilter === 'high')   out = out.filter(p => p.confidence_score >= 70);
    if (confFilter === 'medium') out = out.filter(p => p.confidence_score >= 45 && p.confidence_score < 70);
    if (confFilter === 'low')    out = out.filter(p => p.confidence_score < 45);
    if (sectorFilter !== 'all')  out = out.filter(p => p.sector === sectorFilter);
    if (horizonFilter !== 'all') out = out.filter(p => p.horizon === horizonFilter);
    if (capFilter !== 'all')     out = out.filter(p => capBucket(p.market_cap_cr) === capFilter);
    if (sortKey) {
      out = [...out].sort((a, b) => {
        const av = (a[sortKey] ?? -Infinity) as number;
        const bv = (b[sortKey] ?? -Infinity) as number;
        return sortDir === 'desc' ? bv - av : av - bv;
      });
    }
    return out;
  }, [picks, search, confFilter, sectorFilter, horizonFilter, capFilter, sortKey, sortDir]);

  function toggle(sym: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(sym) ? next.delete(sym) : next.add(sym);
      return next;
    });
  }

  const genDate = new Date(generatedAt).toLocaleString('en-IN', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true,
  });

  const isFiltered = search.trim() !== '' || confFilter !== 'all'
    || sectorFilter !== 'all' || horizonFilter !== 'all' || capFilter !== 'all';

  function clearFilters() {
    setSearch('');
    setConfFilter('all');
    setSectorFilter('all');
    setHorizonFilter('all');
    setCapFilter('all');
  }

  return (
    <div className="animate-fade-up">

      {/* ── Header ── */}
      <div className="flex items-start justify-between mb-5 gap-4">
        <div>
          <h2 className="text-xl font-black text-tx tracking-tight">
            {isFiltered
              ? <>{displayed.length} <span className="text-muted font-normal text-base">of {picks.length} stocks</span></>
              : <>{picks.length} stocks found this week</>}
          </h2>
          <div className="flex items-center gap-2.5 mt-1 flex-wrap">
            <p className="text-xs text-muted">
              {fromCache ? 'Cached · ' : 'Last scan: '}{genDate}
            </p>
            {fromCache && (
              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full uppercase tracking-wider
                               bg-hold/10 text-hold border border-hold/20">
                cached
              </span>
            )}
            {pricesLastUpdated && (
              <span className="flex items-center gap-1 text-[10px] text-buy/70">
                <span className="w-1.5 h-1.5 rounded-full bg-buy animate-pulse inline-block" />
                LTP {pricesLastUpdated.toLocaleTimeString('en-IN', {
                  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
                })}
              </span>
            )}
          </div>
        </div>
        <div className="shrink-0">
          <button
            onClick={onRescan}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors
              ${fromCache
                ? 'border-accent/30 text-accent hover:bg-accent/10'
                : 'border-border text-muted hover:text-tx hover:border-border-hi'}`}
          >
            ↺ {fromCache ? 'Fresh scan' : 'Rescan'}
          </button>
        </div>
      </div>

      {/* ── Filters: search + confidence chips ── */}
      <div className="flex flex-col sm:flex-row gap-3 mb-5">
        {/* Search */}
        <div className="relative flex-1 max-w-[280px]">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted/60 pointer-events-none"
               fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            placeholder="Ticker or company…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-card border border-border rounded-xl pl-9 pr-8 py-2 text-sm text-tx
                       placeholder:text-muted/50 focus:outline-none focus:border-accent/40 transition-colors"
          />
          {search && (
            <button onClick={() => setSearch('')}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted/50 hover:text-muted text-xs">
              ✕
            </button>
          )}
        </div>

        {/* Confidence filter chips */}
        <div className="flex items-center gap-2 flex-wrap">
          {([
            { id: 'all',    label: 'All',      dot: null,      active: 'bg-accent/10 border-accent/30 text-accent' },
            { id: 'high',   label: '>70',      dot: 'bg-buy',  active: 'bg-buy/10 border-buy/30 text-buy' },
            { id: 'medium', label: '45–70',    dot: 'bg-hold', active: 'bg-hold/10 border-hold/30 text-hold' },
            { id: 'low',    label: '<45',      dot: 'bg-sell', active: 'bg-sell/10 border-sell/30 text-sell' },
          ] as const).map(({ id, label, dot, active }) => (
            <button
              key={id}
              onClick={() => setConfFilter(id)}
              aria-pressed={confFilter === id}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-semibold
                          border transition-colors
                ${confFilter === id
                  ? active
                  : 'bg-transparent border-border text-muted hover:text-tx hover:border-border-hi'}`}
            >
              {dot && <span className={`w-1.5 h-1.5 rounded-full inline-block
                ${dot} ${confFilter === id ? '' : 'opacity-50'}`} />}
              {label}
            </button>
          ))}
        </div>

        {/* Horizon filter chips */}
        <div className="flex items-center gap-2 flex-wrap">
          {([
            { id: 'all',    label: 'Any horizon' },
            { id: 'short',  label: 'Short' },
            { id: 'medium', label: 'Medium' },
            { id: 'long',   label: 'Long' },
          ] as const).map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setHorizonFilter(id)}
              aria-pressed={horizonFilter === id}
              className={`px-3 py-1.5 rounded-full text-[11px] font-semibold border transition-colors
                ${horizonFilter === id
                  ? 'bg-accent/10 border-accent/30 text-accent'
                  : 'bg-transparent border-border text-muted hover:text-tx hover:border-border-hi'}`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Market-cap filter chips */}
        <div className="flex items-center gap-2 flex-wrap">
          {([
            { id: 'all',   label: 'Any cap' },
            { id: 'large', label: 'Large' },
            { id: 'mid',   label: 'Mid' },
            { id: 'small', label: 'Small' },
          ] as const).map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setCapFilter(id)}
              aria-pressed={capFilter === id}
              className={`px-3 py-1.5 rounded-full text-[11px] font-semibold border transition-colors
                ${capFilter === id
                  ? 'bg-accent/10 border-accent/30 text-accent'
                  : 'bg-transparent border-border text-muted hover:text-tx hover:border-border-hi'}`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Sector filter — dropdown rather than chips since the sector set is
            dynamic and can run to a dozen+ distinct values in a given scan */}
        {sectors.length > 0 && (
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="bg-card border border-border rounded-xl px-3 py-2 text-xs text-tx
                       focus:outline-none focus:border-accent/40 transition-colors"
          >
            <option value="all">All sectors</option>
            {sectors.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
      </div>

      {/* ── Table ── */}
      <div className="rounded-xl border border-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface sticky top-0 z-10">
                <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider w-8">#</th>
                <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">Stock</th>
                <SortableHeader
                  label="Confidence" sortK="confidence_score" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort}
                  tooltip={
                    <InfoTooltip title="Confidence Score" align="left">
                      <p>50% signal engine + 30% cross-source consensus + 20% recency, 0–100.</p>
                      <p><span className="text-buy font-semibold">≥70</span> high · <span className="text-hold font-semibold">45–69</span> medium · <span className="text-sell font-semibold">&lt;45</span> low.</p>
                    </InfoTooltip>
                  }
                />
                <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">Firms</th>
                <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">
                  <span className="inline-flex items-center gap-1">
                    Rating
                    <InfoTooltip title="Rating" align="left">
                      <p><span className="text-buy font-semibold">BUY</span> — high conviction.</p>
                      <p><span className="text-buy/75 font-semibold">WATCHLIST</span> — lower-conviction bullish, worth tracking.</p>
                      <p><span className="text-hold font-semibold">HOLD</span> — neutral.</p>
                      <p><span className="text-sell font-semibold">SELL</span> — bearish.</p>
                    </InfoTooltip>
                  </span>
                </th>
                <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">LTP</th>
                <SortableHeader label="±%" sortK="change_pct" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
                <SortableHeader label="P/E" sortK="pe_ratio"  currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
                <SortableHeader
                  label="Val." sortK="valuation_percentile" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort}
                  tooltip={
                    <InfoTooltip title="Valuation Percentile" align="left">
                      <p>Where the stock&apos;s current P/E sits within its own 3–5yr Screener-published P/E history.</p>
                      <p><span className="text-buy font-semibold">≤33rd pctl</span> cheap vs. its own history · <span className="text-sell font-semibold">≥67th pctl</span> rich. Blank when Screener doesn&apos;t have a usable band.</p>
                    </InfoTooltip>
                  }
                />
                <th className="px-4 py-3 w-8" />
              </tr>
            </thead>
            <tbody>
              {displayed.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-4 py-12 text-center">
                    <p className="text-sm text-muted">No stocks match your filter.</p>
                    <button onClick={clearFilters}
                            className="mt-2 text-xs text-accent hover:underline">
                      Clear filters
                    </button>
                  </td>
                </tr>
              )}
              {displayed.map(pick => {
                const isOpen    = expanded.has(pick.symbol);
                const changePos = (pick.change_pct ?? 0) >= 0;
                return (
                  <React.Fragment key={pick.symbol}>
                    <tr
                      onClick={() => toggle(pick.symbol)}
                      onKeyDown={e => {
                        // Ignore keydowns bubbled up from a nested interactive element
                        // (e.g. the sources popover button) — only the row itself should toggle.
                        if (e.target !== e.currentTarget) return;
                        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(pick.symbol); }
                      }}
                      role="button"
                      tabIndex={0}
                      aria-expanded={isOpen}
                      className={`border-b border-border/60 cursor-pointer transition-colors
                        ${isOpen ? 'bg-card-hi' : 'hover:bg-surface/60'}`}
                    >
                      {/* Rank + watch */}
                      <td className="px-4 py-4">
                        <div className="flex flex-col items-center gap-1">
                          <RankBadge rank={pick.rank} />
                          <WatchlistButton
                            symbol={pick.symbol}
                            company={pick.company}
                            exchange={pick.exchange}
                            size="sm"
                          />
                          {isPositioned(pick.symbol) && (
                            <span
                              className="text-[8px] font-bold uppercase tracking-wider px-1.5 py-0.5
                                         rounded-full bg-buy/12 text-buy border border-buy/25"
                              title="You've marked this as bought — see Trade Setup for live P&L"
                            >
                              Bought
                            </span>
                          )}
                        </div>
                      </td>

                      {/* Stock — name + ticker on first line, why-reason on second */}
                      <td className="px-4 py-4 max-w-[240px]">
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <span className="font-semibold text-tx text-sm truncate min-w-0">
                              {pick.company}
                            </span>
                            <span className="font-mono text-[10px] text-muted/60 bg-muted/10
                                             px-1.5 py-0.5 rounded border border-muted/15
                                             shrink-0 whitespace-nowrap">
                              {pick.symbol}
                            </span>
                            {pick.trend === 'rising'  && (
                              <span className="text-[10px] text-buy font-bold shrink-0"
                                    title="Confidence rising vs recent avg">↑</span>
                            )}
                            {pick.trend === 'falling' && (
                              <span className="text-[10px] text-sell font-bold shrink-0"
                                    title="Confidence falling vs recent avg">↓</span>
                            )}
                            {pick.is_recent_ipo && (
                              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0
                                               bg-accent/15 text-accent border border-accent/30"
                                    title="IPO < 8 months ago">
                                IPO
                              </span>
                            )}
                          </div>
                          {pick.ranking_reasons?.[0] && (
                            <div className="text-[10px] text-muted/45 mt-1 truncate leading-tight">
                              {pick.ranking_reasons[0]}
                            </div>
                          )}
                        </div>
                      </td>

                      {/* Confidence */}
                      <td className="px-4 py-4">
                        <ConfidenceBar score={pick.confidence_score} />
                      </td>

                      {/* Firms */}
                      <td className="px-4 py-4" onClick={e => e.stopPropagation()}>
                        <SourcesPopover sources={pick.sources} />
                      </td>

                      {/* Rating + Horizon — side by side */}
                      <td className="px-4 py-4">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <SignalBadge verdict={pick.recommendation} />
                          <HorizonBadge horizon={pick.horizon} />
                        </div>
                      </td>

                      {/* LTP */}
                      <td className="px-4 py-4 font-mono text-xs text-tx whitespace-nowrap tabular-nums">
                        {fmtPrice(pick.current_price)}
                      </td>

                      {/* Change % */}
                      <td className="px-4 py-4 font-mono text-xs whitespace-nowrap tabular-nums">
                        <span className={`inline-flex items-center gap-0.5 ${changePos ? 'text-buy' : 'text-sell'}`}>
                          {changePos ? '↑' : '↓'}
                          {Math.abs(pick.change_pct ?? 0).toFixed(2)}%
                        </span>
                      </td>

                      {/* P/E */}
                      <td className="px-4 py-4 font-mono text-xs text-muted tabular-nums">
                        {pick.pe_ratio != null ? pick.pe_ratio.toFixed(1) : '—'}
                      </td>

                      {/* Valuation percentile — cheap/rich vs. the stock's own P/E history */}
                      <td className="px-4 py-4 font-mono text-xs tabular-nums">
                        {pick.valuation_percentile != null ? (
                          <span className={
                            pick.valuation_percentile <= 33 ? 'text-buy'
                              : pick.valuation_percentile >= 67 ? 'text-sell'
                              : 'text-muted'
                          }>
                            {pick.valuation_percentile.toFixed(0)}th
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>

                      {/* Expand chevron */}
                      <td className="px-4 py-4 text-right">
                        <svg
                          className={`w-4 h-4 text-muted/50 inline-block transition-transform duration-200
                            ${isOpen ? 'rotate-180' : ''}`}
                          fill="none" stroke="currentColor" viewBox="0 0 24 24"
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                      </td>
                    </tr>

                    {isOpen && <ExpandedRow pick={pick} />}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-[11px] text-muted/60 leading-relaxed mt-4 px-1">
        These picks, ratings, and trade levels are generated by an AI model from public news and
        market data and are for informational purposes only — not investment advice. Do your own
        research and consult a registered financial advisor before trading.
      </p>
    </div>
  );
}
