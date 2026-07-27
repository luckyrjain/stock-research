'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import type { ScreenerResponse, ScreenerStock } from '@/types';
import SiteNav from '@/components/site-nav';
import WatchlistButton from '@/components/watchlist-button';
import SectorHeatmap from '@/components/sector-heatmap';
import { Skeleton, FilterChip, SortableTh, fmtMarketCap } from '@/components/data-table-ui';

type EmaTrendFilter = 'all' | 'bullish' | 'bearish';
type SortKey = 'symbol' | 'current_price' | 'pe_ratio' | 'market_cap_cr' | 'avg_volume_10d' | 'rsi14';
type RsiFilter = 'all' | 'oversold' | 'overbought';
type SortDir = 'asc' | 'desc';

const _RSI_OVERSOLD = 30;
const _RSI_OVERBOUGHT = 70;
// GET /api/screener already supports offset/limit + returns a real `total`
// count, but this page previously hardcoded limit=200 and never read
// `total` or offered a way to see past it — a broad, unfiltered sort over
// the full NIFTY 500 universe (>200 stocks) silently showed only the
// top-200 slice with no indication anything was cut off.
const _PAGE_SIZE = 200;

// Filters reset to defaults on every reload otherwise — a screen this
// configurable (7 independent filter/sort dimensions) is worth remembering
// across visits, same instinct as useWatchlist's client_id persistence.
// Client-only (guarded by typeof window checks below), so this never runs
// during Next.js's server render pass.
const _FILTERS_STORAGE_KEY = 'alphapulse_screener_filters';

interface PersistedFilters {
  industry?: string;
  emaTrend?: EmaTrendFilter;
  rsiFilter?: RsiFilter;
  peMax?: string;
  marketCapMin?: string;
  sortKey?: SortKey;
  sortDir?: SortDir;
}

const _EMA_TREND_VALUES: EmaTrendFilter[] = ['all', 'bullish', 'bearish'];
const _RSI_FILTER_VALUES: RsiFilter[] = ['all', 'oversold', 'overbought'];
const _SORT_KEY_VALUES: SortKey[] = ['symbol', 'current_price', 'pe_ratio', 'market_cap_cr', 'avg_volume_10d', 'rsi14'];
const _SORT_DIR_VALUES: SortDir[] = ['asc', 'desc'];

function loadPersistedFilters(): PersistedFilters {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(_FILTERS_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as PersistedFilters) : {};
  } catch {
    return {};
  }
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

function fmtNum(v: number | null, digits = 1): string {
  return v == null ? '—' : v.toFixed(digits);
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <tr key={i} className="border-b border-border/60">
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-6" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-20" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-36" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-20" /></td>
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
  const [rsiFilter,  setRsiFilter]  = useState<RsiFilter>('all');
  const [peMax,      setPeMax]      = useState('');
  const [marketCapMin, setMarketCapMin] = useState('');
  // fetchStocks fires on every change to these — debounce the text inputs
  // (same 420ms as ticker-search.tsx) so a typed value doesn't fire one
  // GET /api/screener request per keystroke against its 60/min rate limit.
  // The filter chips above (industry/trend/RSI) are discrete clicks, not
  // typed text, so they don't need this.
  const [debouncedPeMax, setDebouncedPeMax] = useState('');
  const [debouncedMarketCapMin, setDebouncedMarketCapMin] = useState('');
  const [sortKey,    setSortKey]    = useState<SortKey>('market_cap_cr');
  const [sortDir,    setSortDir]    = useState<SortDir>('desc');
  // Defaults above render identically on the server and on first client
  // paint (avoiding a hydration mismatch); the persisted values, if any,
  // are then applied in one batch right after mount — see the hydration
  // effect below, which also gates the very first fetch until this runs so
  // a restored filter set doesn't cause two requests back to back.
  const [hydrated, setHydrated] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const saved = loadPersistedFilters();
    if (saved.industry !== undefined) setIndustry(saved.industry);
    if (saved.emaTrend && _EMA_TREND_VALUES.includes(saved.emaTrend)) setEmaTrend(saved.emaTrend);
    if (saved.rsiFilter && _RSI_FILTER_VALUES.includes(saved.rsiFilter)) setRsiFilter(saved.rsiFilter);
    if (saved.peMax !== undefined) { setPeMax(saved.peMax); setDebouncedPeMax(saved.peMax); }
    if (saved.marketCapMin !== undefined) { setMarketCapMin(saved.marketCapMin); setDebouncedMarketCapMin(saved.marketCapMin); }
    if (saved.sortKey && _SORT_KEY_VALUES.includes(saved.sortKey)) setSortKey(saved.sortKey);
    if (saved.sortDir && _SORT_DIR_VALUES.includes(saved.sortDir)) setSortDir(saved.sortDir);
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated || typeof window === 'undefined') return;
    const filters: PersistedFilters = { industry, emaTrend, rsiFilter, peMax, marketCapMin, sortKey, sortDir };
    try { window.localStorage.setItem(_FILTERS_STORAGE_KEY, JSON.stringify(filters)); } catch { /* private browsing, etc. */ }
  }, [hydrated, industry, emaTrend, rsiFilter, peMax, marketCapMin, sortKey, sortDir]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedPeMax(peMax), 420);
    return () => clearTimeout(t);
  }, [peMax]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedMarketCapMin(marketCapMin), 420);
    return () => clearTimeout(t);
  }, [marketCapMin]);

  const [offset, setOffset] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);

  const fetchStocks = useCallback(async (opts: { silent?: boolean; targetOffset?: number; append?: boolean } = {}) => {
    const { silent = false, targetOffset = 0, append = false } = opts;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    if (!silent && !append) setLoading(true);
    if (append) setLoadingMore(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        industry,
        ema_trend: emaTrend,
        sort: sortKey,
        order: sortDir,
        limit: String(_PAGE_SIZE),
        offset: String(targetOffset),
      });
      if (rsiFilter === 'oversold') params.set('rsi_max', String(_RSI_OVERSOLD));
      if (rsiFilter === 'overbought') params.set('rsi_min', String(_RSI_OVERBOUGHT));
      if (debouncedPeMax.trim()) params.set('pe_max', debouncedPeMax.trim());
      if (debouncedMarketCapMin.trim()) params.set('market_cap_min', debouncedMarketCapMin.trim());

      const res = await fetch(`/api/screener?${params}`, { signal: ac.signal });
      const json = await res.json() as ScreenerResponse & { error?: string };
      if (!res.ok) {
        setError(json.error ?? `Error ${res.status}`);
        if (!append) setData(null);
      } else if (append) {
        // Appends the next page onto the already-loaded rows rather than
        // replacing them — a filter/sort change always goes through the
        // non-append path below instead, which resets to a clean offset-0 fetch.
        setData(prev => (prev ? { ...json, stocks: [...prev.stocks, ...json.stocks] } : json));
      } else {
        setData(json);
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      setError('Could not reach the backend. Is the server running?');
      if (!append) setData(null);
    } finally {
      if (abortRef.current === ac) { setLoading(false); setLoadingMore(false); }
    }
  }, [industry, emaTrend, rsiFilter, debouncedPeMax, debouncedMarketCapMin, sortKey, sortDir]);

  useEffect(() => {
    if (!hydrated) return;
    setOffset(0);
    fetchStocks({ targetOffset: 0 });
  }, [fetchStocks, hydrated]);

  const loadMore = useCallback(() => {
    const next = offset + _PAGE_SIZE;
    setOffset(next);
    fetchStocks({ targetOffset: next, append: true });
  }, [offset, fetchStocks]);

  useEffect(() => {
    if (data) setRefreshing(data.refreshing);
  }, [data]);

  useEffect(() => {
    if (!refreshing) return;
    // Resets back to the first page — the underlying data is changing
    // server-side while a refresh runs, so preserving a deep "loaded more"
    // offset across polls would just misalign with what's now on the server.
    const t = setInterval(() => { setOffset(0); fetchStocks({ silent: true, targetOffset: 0 }); }, 10000);
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

  // The heatmap needs every monitored stock across every industry to compare
  // sectors against each other — the main `data.stocks` above is scoped to
  // whatever the Industry filter chip currently selects, which would collapse
  // the heatmap to one tile the moment a user picks a specific industry. So
  // this is a separate, filter-independent fetch, made once on mount.
  const [heatmapStocks, setHeatmapStocks] = useState<ScreenerStock[]>([]);
  useEffect(() => {
    fetch('/api/screener?industry=all&ema_trend=all&sort=market_cap_cr&order=desc&limit=500')
      .then(res => (res.ok ? res.json() : null))
      .then((json: ScreenerResponse | null) => { if (json) setHeatmapStocks(json.stocks); })
      .catch(() => { /* heatmap just doesn't render — the table below is unaffected */ });
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

        <SiteNav
          active="screener"
          wrap
          right={<>
            <button
              onClick={startRefresh}
              disabled={refreshing}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-accent/40 text-accent
                         hover:bg-accent/10 transition-colors disabled:opacity-40"
            >
              {refreshing ? 'Refreshing data…' : '⟳ Refresh Data'}
            </button>
            <button
              onClick={() => { setOffset(0); fetchStocks({ targetOffset: 0 }); }}
              disabled={loading}
              className="text-xs text-muted hover:text-tx transition-colors disabled:opacity-40"
            >
              {loading ? 'Loading…' : '↺ Reload'}
            </button>
          </>}
        />

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

        <SectorHeatmap stocks={heatmapStocks} activeIndustry={industry} onSelectIndustry={setIndustry} />

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
                  <th className="text-left px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Sector</th>
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
                    <td colSpan={10} className="px-4 py-10 text-center text-sell text-sm">{error}</td>
                  </tr>
                ) : stocks.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="px-4 py-10 text-center text-muted text-sm">
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
                      <td className="px-4 py-3 text-muted/70 text-xs whitespace-nowrap">{s.sector ?? '—'}</td>
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

        {!loading && !error && data && (
          <div className="flex flex-col items-center gap-2 mt-4">
            <p className="text-xs text-muted">
              Showing {stocks.length} of {data.total} matching stock{data.total === 1 ? '' : 's'}
            </p>
            {stocks.length < data.total && (
              <button
                onClick={loadMore}
                disabled={loadingMore}
                className="px-4 py-1.5 rounded-lg text-xs font-semibold border border-border text-tx
                           hover:bg-surface/60 transition-colors disabled:opacity-40"
              >
                {loadingMore ? 'Loading…' : `Load ${Math.min(_PAGE_SIZE, data.total - stocks.length)} more`}
              </button>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
