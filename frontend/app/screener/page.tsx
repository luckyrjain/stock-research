'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import type { ScreenerResponse, ScreenerStock } from '@/types';
import HeaderSearch from '@/components/header-search';
import AuthWidget from '@/components/auth-widget';
import WatchlistButton from '@/components/watchlist-button';

type EmaTrendFilter = 'all' | 'bullish' | 'bearish';
type SortKey = 'symbol' | 'current_price' | 'pe_ratio' | 'market_cap_cr' | 'avg_volume_10d' | 'rsi14';

const _RSI_OVERSOLD = 30;
const _RSI_OVERBOUGHT = 70;

function Skeleton({ className }: { className: string }) {
  return <div className={`bg-border/60 rounded animate-pulse ${className}`} />;
}

function TrendBadge({ trend }: { trend: 'bullish' | 'bearish' | null }) {
  if (trend == null) return <span className="text-muted text-[10px]">—</span>;
  return trend === 'bullish' ? (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
      ↑ Bullish
    </span>
  ) : (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border bg-sell/12 text-sell border-sell/25">
      ↓ Bearish
    </span>
  );
}

function rsiColor(v: number | null): string {
  if (v == null) return 'text-muted';
  if (v >= _RSI_OVERBOUGHT) return 'text-sell';
  if (v <= _RSI_OVERSOLD) return 'text-buy';
  return 'text-tx';
}

function fmtMarketCap(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1_000) return `₹${(v / 1_000).toFixed(2)}k Cr`;
  return `₹${v.toFixed(0)} Cr`;
}

function fmtNum(v: number | null, digits = 1): string {
  return v == null ? '—' : v.toFixed(digits);
}

function FilterChip<T extends string>({
  value, active, onClick, label,
}: { value: T; active: boolean; onClick: (v: T) => void; label: string }) {
  return (
    <button
      onClick={() => onClick(value)}
      aria-pressed={active}
      className={`px-3 py-1.5 rounded-full text-[11px] font-semibold border transition-colors
        ${active
          ? 'bg-accent/15 border-accent/40 text-accent'
          : 'bg-surface border-border text-muted hover:text-tx hover:border-border-hi'}`}
    >
      {label}
    </button>
  );
}

function SortableTh({ label, sortK, currentKey, currentDir, onSort, align = 'left' }: {
  label: string;
  sortK: SortKey;
  currentKey: SortKey;
  currentDir: 'asc' | 'desc';
  onSort: (k: SortKey) => void;
  align?: 'left' | 'right';
}) {
  const active = currentKey === sortK;
  return (
    <th
      aria-sort={active ? (currentDir === 'desc' ? 'descending' : 'ascending') : 'none'}
      className={`p-0 text-[10px] font-bold text-muted uppercase tracking-wider whitespace-nowrap ${align === 'right' ? 'text-right' : 'text-left'}`}
    >
      <button
        type="button"
        onClick={() => onSort(sortK)}
        className={`inline-flex items-center gap-1 px-4 py-3 uppercase tracking-wider
                   cursor-pointer hover:text-tx transition-colors select-none group
                   ${align === 'right' ? 'flex-row-reverse' : ''}`}
      >
        {label}
        <span className={`text-[9px] transition-colors ${active ? 'text-accent' : 'text-muted/25 group-hover:text-muted/60'}`}>
          {active ? (currentDir === 'desc' ? '↓' : '↑') : '↕'}
        </span>
      </button>
    </th>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <tr key={i} className="border-b border-border/60">
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-6" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-20" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-36" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-12 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-10 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-5 w-20" /></td>
        </tr>
      ))}
    </>
  );
}

export default function ScreenerPage() {
  const [data,       setData]       = useState<ScreenerResponse | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [industry,   setIndustry]   = useState('all');
  const [emaTrend,   setEmaTrend]   = useState<EmaTrendFilter>('all');
  const [rsiFilter,  setRsiFilter]  = useState<'all' | 'oversold' | 'overbought'>('all');
  const [peMax,      setPeMax]      = useState('');
  const [marketCapMin, setMarketCapMin] = useState('');
  const [sortKey,    setSortKey]    = useState<SortKey>('market_cap_cr');
  const [sortDir,    setSortDir]    = useState<'asc' | 'desc'>('desc');
  const abortRef = useRef<AbortController | null>(null);

  const fetchStocks = useCallback(async (silent = false) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    if (!silent) setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        industry,
        ema_trend: emaTrend,
        sort: sortKey,
        order: sortDir,
        limit: '200',
      });
      if (rsiFilter === 'oversold') params.set('rsi_max', String(_RSI_OVERSOLD));
      if (rsiFilter === 'overbought') params.set('rsi_min', String(_RSI_OVERBOUGHT));
      if (peMax.trim()) params.set('pe_max', peMax.trim());
      if (marketCapMin.trim()) params.set('market_cap_min', marketCapMin.trim());

      const res = await fetch(`/api/screener?${params}`, { signal: ac.signal });
      const json = await res.json() as ScreenerResponse & { error?: string };
      if (!res.ok) {
        setError(json.error ?? `Error ${res.status}`);
        setData(null);
      } else {
        setData(json);
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      setError('Could not reach the backend. Is the server running?');
      setData(null);
    } finally {
      if (abortRef.current === ac) setLoading(false);
    }
  }, [industry, emaTrend, rsiFilter, peMax, marketCapMin, sortKey, sortDir]);

  useEffect(() => {
    fetchStocks();
  }, [fetchStocks]);

  useEffect(() => {
    if (data) setRefreshing(data.refreshing);
  }, [data]);

  useEffect(() => {
    if (!refreshing) return;
    const t = setInterval(() => fetchStocks(true), 10000);
    return () => clearInterval(t);
  }, [refreshing, fetchStocks]);

  const startRefresh = useCallback(async () => {
    try {
      const res = await fetch('/api/screener/refresh', { method: 'POST' });
      if (res.status === 202 || res.status === 409) setRefreshing(true);
    } catch {
      setError('Could not reach the backend. Is the server running?');
    }
  }, []);

  const toggleSort = useCallback((k: SortKey) => {
    setSortKey(prevKey => {
      if (prevKey === k) {
        setSortDir(prevDir => (prevDir === 'desc' ? 'asc' : 'desc'));
        return k;
      }
      setSortDir('desc');
      return k;
    });
  }, []);

  const stocks: ScreenerStock[] = data?.stocks ?? [];
  const industries = data?.industries ?? [];
  const lastRunLabel = data?.last_run
    ? new Date(data.last_run).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
    : null;

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-6xl mx-auto px-4 pt-8 pb-16">

        {/* Nav */}
        <div className="flex items-center gap-4 mb-8 pb-4 border-b border-border flex-wrap">
          <Link href="/" className="text-base font-black tracking-tight text-tx">
            Alpha<span className="text-accent">Pulse</span>
          </Link>
          <span className="text-border-hi">|</span>
          <Link href="/market-picks" className="text-sm text-muted hover:text-tx transition-colors">
            Market Picks
          </Link>
          <span className="text-border-hi">|</span>
          <Link href="/sme-signals" className="text-sm text-muted hover:text-tx transition-colors">
            SME Signals
          </Link>
          <span className="text-border-hi">|</span>
          <span className="text-sm font-semibold text-accent">Screener</span>
          <span className="text-border-hi">|</span>
          <Link href="/watchlist" className="text-sm text-muted hover:text-tx transition-colors">
            Watchlist
          </Link>
          <span className="text-border-hi">|</span>
          <Link href="/compare" className="text-sm text-muted hover:text-tx transition-colors">
            Compare
          </Link>
          <div className="ml-auto flex items-center gap-3">
            <HeaderSearch />
            <AuthWidget />
            <button
              onClick={startRefresh}
              disabled={refreshing}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-accent/40 text-accent
                         hover:bg-accent/10 transition-colors disabled:opacity-40"
            >
              {refreshing ? 'Refreshing data…' : '⟳ Refresh Data'}
            </button>
            <button
              onClick={() => fetchStocks()}
              disabled={loading}
              className="text-xs text-muted hover:text-tx transition-colors disabled:opacity-40"
            >
              {loading ? 'Loading…' : '↺ Reload'}
            </button>
          </div>
        </div>

        {/* Header */}
        <div className="mb-8 animate-fade-up">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full
                          bg-accent/10 border border-accent/20 text-accent text-xs font-semibold mb-4">
            NIFTY 500 · Custom Screener
          </div>
          <h1 className="text-4xl font-black tracking-tight mb-2">
            Stock <span className="text-accent">Screener</span>
          </h1>
          <p className="text-muted text-sm max-w-2xl leading-relaxed">
            Filter and sort the NIFTY 500 universe by industry, P/E, market cap, and RSI/EMA trend —
            refreshed daily by a background pipeline, not fetched live per request.
          </p>
          <div className="flex items-center gap-3 mt-3 text-xs text-muted">
            {data && <span>{data.total_monitored} stocks monitored</span>}
            {lastRunLabel && <span>· Last refreshed {lastRunLabel}</span>}
          </div>
        </div>

        {/* Filters */}
        <div className="mb-6 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-muted mr-1">Trend</span>
            <FilterChip value="all" active={emaTrend === 'all'} onClick={setEmaTrend} label="All" />
            <FilterChip value="bullish" active={emaTrend === 'bullish'} onClick={setEmaTrend} label="Bullish" />
            <FilterChip value="bearish" active={emaTrend === 'bearish'} onClick={setEmaTrend} label="Bearish" />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-muted mr-1">RSI(14)</span>
            <FilterChip value="all" active={rsiFilter === 'all'} onClick={setRsiFilter} label="All" />
            <FilterChip value="oversold" active={rsiFilter === 'oversold'} onClick={setRsiFilter} label={`Oversold (≤${_RSI_OVERSOLD})`} />
            <FilterChip value="overbought" active={rsiFilter === 'overbought'} onClick={setRsiFilter} label={`Overbought (≥${_RSI_OVERBOUGHT})`} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-muted mr-1">Industry</span>
            <FilterChip value="all" active={industry === 'all'} onClick={setIndustry} label="All" />
            {industries.map(ind => (
              <FilterChip key={ind} value={ind} active={industry === ind} onClick={setIndustry} label={ind} />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-xs text-muted">
              Max P/E
              <input
                type="number"
                min={0}
                value={peMax}
                onChange={e => setPeMax(e.target.value)}
                placeholder="any"
                className="w-20 px-2 py-1 rounded-md border border-border bg-surface text-tx text-xs"
              />
            </label>
            <label className="flex items-center gap-2 text-xs text-muted">
              Min Market Cap (₹ Cr)
              <input
                type="number"
                min={0}
                value={marketCapMin}
                onChange={e => setMarketCapMin(e.target.value)}
                placeholder="any"
                className="w-28 px-2 py-1 rounded-md border border-border bg-surface text-tx text-xs"
              />
            </label>
          </div>
        </div>

        {/* Table */}
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-surface/60">
                  <th className="w-10 px-4 py-3"></th>
                  <SortableTh label="Symbol" sortK="symbol" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
                  <th className="text-left px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Company</th>
                  <SortableTh label="Price" sortK="current_price" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                  <SortableTh label="P/E" sortK="pe_ratio" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                  <SortableTh label="Mkt Cap" sortK="market_cap_cr" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                  <SortableTh label="Avg Vol" sortK="avg_volume_10d" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                  <SortableTh label="RSI14" sortK="rsi14" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                  <th className="text-left px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Trend</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <SkeletonRows />
                ) : error ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-10 text-center text-sell text-sm">{error}</td>
                  </tr>
                ) : stocks.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-10 text-center text-muted text-sm">
                      No stocks match these filters, or the screener hasn&apos;t run yet — try &quot;Refresh Data&quot;.
                    </td>
                  </tr>
                ) : (
                  stocks.map(s => (
                    <tr key={s.symbol} className="border-b border-border/60 last:border-0 hover:bg-surface/40 transition-colors">
                      <td className="px-4 py-3">
                        <WatchlistButton symbol={s.symbol} company={s.company_name ?? s.symbol} exchange={s.exchange ?? 'NSE'} size="sm" />
                      </td>
                      <td className="px-4 py-3">
                        <Link href={`/?symbol=${encodeURIComponent(s.symbol)}`} className="font-semibold text-tx hover:text-accent transition-colors">
                          {s.symbol}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-muted truncate max-w-xs">{s.company_name ?? '—'}</td>
                      <td className="px-4 py-3 text-right font-mono">{s.current_price != null ? `₹${s.current_price.toFixed(2)}` : '—'}</td>
                      <td className="px-4 py-3 text-right font-mono">{fmtNum(s.pe_ratio)}</td>
                      <td className="px-4 py-3 text-right font-mono">{fmtMarketCap(s.market_cap_cr)}</td>
                      <td className="px-4 py-3 text-right font-mono">{s.avg_volume_10d != null ? s.avg_volume_10d.toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—'}</td>
                      <td className={`px-4 py-3 text-right font-mono font-semibold ${rsiColor(s.rsi14)}`}>{fmtNum(s.rsi14)}</td>
                      <td className="px-4 py-3"><TrendBadge trend={s.ema_trend} /></td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </main>
  );
}
