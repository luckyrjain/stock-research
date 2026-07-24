'use client';

import { useState, useEffect, useCallback, useMemo, useRef, Fragment } from 'react';
import Link from 'next/link';
import type { SmeSignal, SmeCrossEvent, SmeSignalHistoryResponse, SmeSignalsResponse } from '@/types';
import EmaChart from '@/components/ema-chart';
import HeaderSearch from '@/components/header-search';
import AuthWidget from '@/components/auth-widget';
import WatchlistButton from '@/components/watchlist-button';

// ── Filter types ──────────────────────────────────────────────────────────────

type Lookback  = 1 | 3 | 5 | 10;
type Direction = 'all' | 'golden' | 'death';
type ExchangeFilter = 'all' | 'NSE' | 'BSE';
type View = 'crosses' | 'regime';
type RsiFilter = 'all' | 'oversold' | 'overbought';
type SortKey =
  | 'symbol' | 'trade_date' | 'close_price' | 'ema20' | 'ema50'
  | 'avg_turnover_20d' | 'rsi14' | 'market_cap_cr';

// Below this avg daily turnover (last 20 trading days), a cross isn't
// necessarily tradeable at the shown price — SME names routinely trade a
// few thousand rupees a day. ₹5L/day is a common discretionary cutoff for
// "someone could actually build/exit a position here."
const _ILLIQUID_TURNOVER_THRESHOLD = 500_000;

// Standard RSI(14) thresholds.
const _RSI_OVERSOLD   = 30;
const _RSI_OVERBOUGHT = 70;

// ── Helper components ─────────────────────────────────────────────────────────

function Skeleton({ className }: { className: string }) {
  return (
    <div className={`bg-border/60 rounded animate-pulse ${className}`} />
  );
}

function CrossBadge({ cross }: { cross: 'golden' | 'death' | null }) {
  // null in regime view: this stock's latest row usually isn't a cross day.
  if (cross == null) return <span className="text-muted text-[10px]">—</span>;
  return cross === 'golden' ? (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
      ⚡ Golden
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-sell/12 text-sell border-sell/25">
      💀 Death
    </span>
  );
}

function RegimeBadge({ inGolden }: { inGolden: boolean }) {
  return inGolden ? (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
      In Golden Cross
    </span>
  ) : (
    <span className="text-muted text-[10px]">—</span>
  );
}

function ExchangeBadge({ exchange }: { exchange: string }) {
  return (
    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded border text-muted border-border bg-surface">
      {exchange}
    </span>
  );
}

function IlliquidBadge() {
  return (
    <span
      title={`Avg daily turnover below ₹${(_ILLIQUID_TURNOVER_THRESHOLD / 1_00_000).toFixed(0)}L — a cross here may not be tradeable at the shown price`}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[9px] font-bold border bg-hold/12 text-hold border-hold/25"
    >
      ⚠ Illiquid
    </span>
  );
}

function fmtTurnover(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1_00_00_000) return `₹${(v / 1_00_00_000).toFixed(2)}Cr`;
  if (v >= 1_00_000) return `₹${(v / 1_00_000).toFixed(1)}L`;
  return `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function fmtMarketCap(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1_000) return `₹${(v / 1_000).toFixed(2)}k Cr`;
  return `₹${v.toFixed(0)} Cr`;
}

function rsiColor(v: number | null): string {
  if (v == null) return 'text-muted';
  if (v >= _RSI_OVERBOUGHT) return 'text-sell';
  if (v <= _RSI_OVERSOLD) return 'text-buy';
  return 'text-tx';
}

function VolumeSpikeBadge() {
  return (
    <span
      title="Today's volume is more than 2x its trailing 20-day average — a cross with real participation behind it"
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[9px] font-bold border bg-accent/12 text-accent border-accent/25"
    >
      🔥 Spike
    </span>
  );
}

function fmtRet(v: number | null): string {
  if (v == null) return 'pending';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
}

function retColor(v: number | null): string {
  if (v == null) return 'text-muted';
  return v >= 0 ? 'text-buy' : 'text-sell';
}

// "Last 3 golden crosses: +12%, -4%, +22% over 20d" — outcome context for a
// bare "golden cross on date X," using the forward returns the history
// endpoint already computed (api.py's _compute_cross_events).
function CrossOutcomeSummary({ events }: { events: SmeCrossEvent[] }) {
  const golden = events.filter(e => e.cross === 'golden').slice(0, 3);
  const death  = events.filter(e => e.cross === 'death').slice(0, 3);
  if (golden.length === 0 && death.length === 0) return null;

  const renderTrail = (label: string, trail: SmeCrossEvent[], labelColor: string) => (
    <div className="flex items-center gap-1.5 flex-wrap">
      <span className={`text-[10px] font-bold uppercase tracking-wider ${labelColor}`}>
        Last {trail.length} {label} cross{trail.length > 1 ? 'es' : ''} (20d)
      </span>
      {trail.map((e, i) => (
        <span key={e.trade_date} className="inline-flex items-center">
          <span className={`font-mono text-xs font-semibold ${retColor(e.ret_20d_pct)}`}>
            {fmtRet(e.ret_20d_pct)}
          </span>
          {i < trail.length - 1 && <span className="text-muted/40 ml-1">,</span>}
        </span>
      ))}
    </div>
  );

  return (
    <div className="flex flex-wrap gap-x-6 gap-y-2 mb-4 px-1">
      {golden.length > 0 && renderTrail('golden', golden, 'text-buy/80')}
      {death.length > 0 && renderTrail('death', death, 'text-sell/80')}
    </div>
  );
}

function FilterChip<T extends string | number>({
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
  currentKey: SortKey | null;
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

// ── Loading skeleton rows ─────────────────────────────────────────────────────

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <tr key={i} className="border-b border-border/60">
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-20" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-36" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-12" /></td>
          <td className="px-4 py-4"><Skeleton className="h-5 w-20" /></td>
          <td className="px-4 py-4"><Skeleton className="h-5 w-16" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-10 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-10 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
        </tr>
      ))}
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SmeSignalsPage() {
  const [data,       setData]       = useState<SmeSignalsResponse | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState<string | null>(null);
  const [lookback,   setLookback]   = useState<Lookback>(5);
  const [direction,  setDirection]  = useState<Direction>('all');
  const [view,       setView]       = useState<View>('crosses');
  const [refreshing, setRefreshing] = useState(false);
  const [exchangeFilter, setExchangeFilter] = useState<ExchangeFilter>('all');
  const [rsiFilter, setRsiFilter] = useState<RsiFilter>('all');
  const [volumeSpikeOnly, setVolumeSpikeOnly] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);
  const [historyBySymbol, setHistoryBySymbol] = useState<Record<string, SmeSignalHistoryResponse | 'loading' | 'error'>>({});
  const abortRef = useRef<AbortController | null>(null);

  const toggleExpand = useCallback((symbol: string) => {
    setExpandedSymbol(prev => (prev === symbol ? null : symbol));
  }, []);

  // Fetch (and cache) the EMA history series the first time a row is expanded.
  useEffect(() => {
    if (!expandedSymbol || historyBySymbol[expandedSymbol]) return;
    const symbol = expandedSymbol;
    setHistoryBySymbol(prev => ({ ...prev, [symbol]: 'loading' }));
    fetch(`/api/sme-signals/${encodeURIComponent(symbol)}/history`)
      .then(res => (res.ok ? res.json() : Promise.reject()))
      .then((json: SmeSignalHistoryResponse) => {
        setHistoryBySymbol(prev => ({ ...prev, [symbol]: json }));
      })
      .catch(() => {
        setHistoryBySymbol(prev => ({ ...prev, [symbol]: 'error' }));
      });
  }, [expandedSymbol, historyBySymbol]);

  const fetchSignals = useCallback(async (lb: Lookback, dir: Direction, v: View, silent = false) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    if (!silent) setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ lookback: String(lb), direction: dir, view: v });
      const res = await fetch(`/api/sme-signals?${qs}`, { signal: ac.signal });
      const json = await res.json() as SmeSignalsResponse & { error?: string };
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
      // Whoever owns the latest request clears loading — a silent poll that
      // aborted a non-silent fetch must clear it too, or the skeleton sticks.
      if (abortRef.current === ac) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSignals(lookback, direction, view);
  }, [lookback, direction, view, fetchSignals]);

  // Track server-side refresh state; poll while a refresh runs, reload when done.
  useEffect(() => {
    if (data) setRefreshing(data.refreshing);
  }, [data]);

  useEffect(() => {
    if (!refreshing) return;
    const t = setInterval(() => fetchSignals(lookback, direction, view, true), 10000);
    return () => clearInterval(t);
  }, [refreshing, lookback, direction, view, fetchSignals]);

  const startRefresh = useCallback(async () => {
    try {
      const res = await fetch('/api/sme-signals/refresh', { method: 'POST' });
      if (res.status === 202 || res.status === 409) setRefreshing(true);
    } catch {
      setError('Could not reach the backend. Is the server running?');
    }
  }, []);

  const signals = data?.signals ?? [];

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

  // Exchange/RSI/volume-spike filters + sort are applied client-side — the
  // API already returns every matching row for the selected period/direction/
  // view, so there's no pagination to fight and no need for extra server
  // round-trips.
  const displayedSignals = useMemo(() => {
    let out: SmeSignal[] = signals;
    if (exchangeFilter !== 'all') out = out.filter(s => s.exchange === exchangeFilter);
    if (rsiFilter === 'oversold')   out = out.filter(s => s.rsi14 != null && s.rsi14 <= _RSI_OVERSOLD);
    if (rsiFilter === 'overbought') out = out.filter(s => s.rsi14 != null && s.rsi14 >= _RSI_OVERBOUGHT);
    if (volumeSpikeOnly) out = out.filter(s => s.volume_spike === true);

    if (sortKey) {
      out = [...out].sort((a, b) => {
        const av = a[sortKey];
        const bv = b[sortKey];
        let cmp: number;
        if (av == null && bv == null) cmp = 0;
        else if (av == null) cmp = -1;
        else if (bv == null) cmp = 1;
        else if (typeof av === 'string' && typeof bv === 'string') cmp = av.localeCompare(bv);
        else cmp = (av as number) - (bv as number);
        return sortDir === 'desc' ? -cmp : cmp;
      });
    }
    return out;
  }, [signals, exchangeFilter, rsiFilter, volumeSpikeOnly, sortKey, sortDir]);

  // Derived stats
  const deathCount = signals.filter(s => s.cross === 'death').length;
  const deathNow = data ? data.total_monitored - data.golden_now : null;
  const hitRate = data?.golden_hit_rate_90d;

  const lastRunLabel = data?.last_run
    ? new Date(data.last_run).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
    : null;

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-5xl mx-auto px-4 pt-8 pb-16">

        {/* Nav */}
        <div className="flex items-center gap-4 mb-8 pb-4 border-b border-border">
          <Link href="/" className="text-base font-black tracking-tight text-tx">
            Alpha<span className="text-accent">Pulse</span>
          </Link>
          <span className="text-border-hi">|</span>
          <Link href="/market-picks" className="text-sm text-muted hover:text-tx transition-colors">
            Market Picks
          </Link>
          <span className="text-border-hi">|</span>
          <span className="text-sm font-semibold text-accent">SME Signals</span>
          <Link href="/market-picks/history" className="text-sm text-muted hover:text-tx transition-colors">
            Track Record
          </Link>
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
              onClick={() => fetchSignals(lookback, direction, view)}
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
            NSE Emerge · BSE SME · Golden Cross Screener
          </div>
          <h1 className="text-4xl font-black tracking-tight mb-2">
            SME Golden <span className="text-accent">Cross</span> Signals
          </h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            SME-listed stocks (NSE Emerge + BSE SME) whose EMA 20 crossed their EMA 50 (golden/death cross) in the selected window.
            Data is computed by the <code className="text-accent/80 text-[11px] bg-accent/8 px-1.5 py-0.5 rounded">sme_ema_pipeline</code> batch job.
          </p>
        </div>

        {/* Stats strip */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          {[
            { label: 'Stocks Monitored',    value: data?.total_monitored ?? '—',        color: 'text-tx',     sub: 'NSE Emerge + BSE SME' },
            view === 'crosses'
              ? { label: 'Crosses Found',  value: loading ? '—' : signals.length, color: 'text-accent', sub: `last ${lookback} day${lookback > 1 ? 's' : ''}` }
              : { label: 'Stocks Shown',   value: loading ? '—' : signals.length, color: 'text-accent', sub: 'latest regime, every stock' },
            { label: 'In Golden Cross Now', value: data?.golden_now ?? '—',             color: 'text-buy',    sub: 'EMA20 above EMA50 today' },
            view === 'crosses'
              ? { label: 'Death Crosses',       value: loading ? '—' : deathCount, color: 'text-sell', sub: `last ${lookback} day${lookback > 1 ? 's' : ''}` }
              : { label: 'In Death Cross Now',  value: deathNow ?? '—',            color: 'text-sell', sub: 'EMA20 below EMA50 today' },
            {
              label: 'Golden Follow-Through',
              value: hitRate?.win_rate != null ? `${hitRate.win_rate}%` : '—',
              color: 'text-accent',
              sub: hitRate
                ? `${hitRate.sample_size} golden cross${hitRate.sample_size === 1 ? '' : 'es'}, ${hitRate.lookback_days}d, +${hitRate.forward_days}d fwd`
                : `last 90d, +20d fwd`,
            },
          ].map(({ label, value, color, sub }) => (
            <div key={label} className="rounded-xl border border-border bg-card px-4 py-3">
              <div className={`text-2xl font-black font-mono tabular-nums ${color}`}>{value}</div>
              <div className="text-[11px] font-semibold text-tx mt-0.5">{label}</div>
              <div className="text-[10px] text-muted mt-0.5">{sub}</div>
            </div>
          ))}
        </div>

        {/* View mode */}
        <div className="flex flex-wrap items-center gap-4 mb-4">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-muted uppercase tracking-wider">View</span>
            <div className="flex gap-1.5">
              {(
                [
                  { value: 'crosses', label: 'Cross Events' },
                  { value: 'regime',  label: 'Regime (all stocks)' },
                ] as const
              ).map(({ value, label }) => (
                <FilterChip key={value} value={value} active={view === value} onClick={setView} label={label} />
              ))}
            </div>
          </div>
          {view === 'regime' && (
            <span className="text-[10px] text-muted/70">
              Latest EMA20/EMA50 regime for every monitored stock, not just ones that crossed recently.
            </span>
          )}
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-4 mb-6">
          {/* Lookback + Direction only apply to the cross-events view — there's
              no cross-event window to filter by in regime view. */}
          {view === 'crosses' && (
            <>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold text-muted uppercase tracking-wider">Period</span>
                <div className="flex gap-1.5">
                  {([1, 3, 5, 10] as const).map(v => (
                    <FilterChip key={v} value={v} active={lookback === v} onClick={setLookback} label={`${v}d`} />
                  ))}
                </div>
              </div>

              <div className="w-px h-5 bg-border" />

              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold text-muted uppercase tracking-wider">Direction</span>
                <div className="flex gap-1.5">
                  {(
                    [
                      { value: 'all',    label: 'All'      },
                      { value: 'golden', label: '⚡ Golden' },
                      { value: 'death',  label: '💀 Death'  },
                    ] as const
                  ).map(({ value, label }) => (
                    <FilterChip key={value} value={value} active={direction === value} onClick={setDirection} label={label} />
                  ))}
                </div>
              </div>

              <div className="w-px h-5 bg-border" />
            </>
          )}

          {/* Exchange — filtered client-side, the API already returns both exchanges */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-muted uppercase tracking-wider">Exchange</span>
            <div className="flex gap-1.5">
              {(['all', 'NSE', 'BSE'] as const).map(v => (
                <FilterChip key={v} value={v} active={exchangeFilter === v} onClick={setExchangeFilter} label={v === 'all' ? 'All' : v} />
              ))}
            </div>
          </div>

          <div className="w-px h-5 bg-border" />

          {/* RSI(14) — momentum-screener confirmation, filtered client-side */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-muted uppercase tracking-wider">RSI</span>
            <div className="flex gap-1.5">
              {(
                [
                  { value: 'all',        label: 'All' },
                  { value: 'oversold',   label: `Oversold (≤${_RSI_OVERSOLD})` },
                  { value: 'overbought', label: `Overbought (≥${_RSI_OVERBOUGHT})` },
                ] as const
              ).map(({ value, label }) => (
                <FilterChip key={value} value={value} active={rsiFilter === value} onClick={setRsiFilter} label={label} />
              ))}
            </div>
          </div>

          <div className="w-px h-5 bg-border" />

          {/* Volume spike — a cross with no volume confirmation is a weak signal on its own */}
          <button
            onClick={() => setVolumeSpikeOnly(v => !v)}
            aria-pressed={volumeSpikeOnly}
            className={`px-3 py-1.5 rounded-full text-[11px] font-semibold border transition-colors
              ${volumeSpikeOnly
                ? 'bg-accent/15 border-accent/40 text-accent'
                : 'bg-surface border-border text-muted hover:text-tx hover:border-border-hi'}`}
          >
            🔥 Volume-confirmed only
          </button>
        </div>

        {/* Last run label */}
        {lastRunLabel && (
          <div className="text-[10px] text-muted mb-4">
            Pipeline last run: <span className="text-tx/60">{lastRunLabel}</span>
            {' '}·{' '}
            <span className="text-muted/60">
              Run <code className="text-accent/60">python sme_ema_pipeline.py</code> to update
            </span>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm mb-6">
            {error}
          </div>
        )}

        {/* Table */}
        {!error && (
          <div className="rounded-xl border border-border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-surface sticky top-0 z-10">
                    <SortableTh label="Symbol" sortK="symbol" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
                    <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">Company</th>
                    <SortableTh label={view === 'crosses' ? 'Cross Date' : 'Latest Date'} sortK="trade_date" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
                    <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">Cross</th>
                    <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">Regime</th>
                    <SortableTh label="Close" sortK="close_price" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                    <SortableTh label="EMA 20" sortK="ema20" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                    <SortableTh label="EMA 50" sortK="ema50" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                    <SortableTh label="RSI (14)" sortK="rsi14" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                    <th className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider">Vol</th>
                    <SortableTh label="Mkt Cap" sortK="market_cap_cr" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                    <SortableTh label="Turnover (20d)" sortK="avg_turnover_20d" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <SkeletonRows />
                  ) : displayedSignals.length === 0 ? (
                    <tr>
                      <td colSpan={12} className="px-4 py-16 text-center text-muted text-sm">
                        {signals.length === 0
                          ? 'No crossovers found for the selected filters.'
                          : `No ${exchangeFilter} crossovers in the current results.`}
                        {!data?.last_run && (
                          <div className="mt-2 text-xs text-muted/60">
                            Make sure you&apos;ve run{' '}
                            <code className="text-accent/60">python sme_ema_pipeline.py</code> first.
                          </div>
                        )}
                      </td>
                    </tr>
                  ) : (
                    displayedSignals.map((s, i) => {
                      const rowKey = `${s.symbol}-${s.trade_date}-${i}`;
                      const isExpanded = expandedSymbol === s.symbol;
                      const history = historyBySymbol[s.symbol];
                      return (
                      <Fragment key={rowKey}>
                      <tr
                        onClick={() => toggleExpand(s.symbol)}
                        onKeyDown={e => {
                          // Ignore keydowns bubbled up from a nested interactive element
                          // (e.g. the NSE symbol link) — only the row itself should toggle.
                          if (e.target !== e.currentTarget) return;
                          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleExpand(s.symbol); }
                        }}
                        role="button"
                        tabIndex={0}
                        aria-expanded={isExpanded}
                        className="border-b border-border/60 hover:bg-surface/60 transition-colors cursor-pointer"
                      >
                        {/* Symbol */}
                        <td className="px-4 py-4">
                          <div className="flex items-center gap-1.5">
                            <span className={`text-[9px] text-muted/60 transition-transform ${isExpanded ? 'rotate-90' : ''}`}>▸</span>
                            <WatchlistButton
                              symbol={s.symbol}
                              company={s.name ?? s.symbol}
                              exchange={s.exchange}
                              size="sm"
                            />
                            {s.exchange === 'NSE' ? (
                              <Link
                                href={`/?symbol=${s.symbol}`}
                                onClick={e => e.stopPropagation()}
                                className="font-semibold text-tx hover:text-accent transition-colors text-sm"
                              >
                                {s.symbol}
                              </Link>
                            ) : s.isin ? (
                              // BSE's own scrip code (`s.symbol`) isn't a directly
                              // analyzable ticker — deep-link via ISIN instead, which
                              // the home page resolves through /api/validate first
                              // (same resolution ticker-search.tsx already does for
                              // user-typed ISINs).
                              <Link
                                href={`/?symbol=${encodeURIComponent(s.isin)}`}
                                onClick={e => e.stopPropagation()}
                                title={`Resolve via ISIN ${s.isin}`}
                                className="font-semibold text-tx hover:text-accent transition-colors text-sm"
                              >
                                {s.symbol}
                              </Link>
                            ) : (
                              <span className="font-semibold text-tx text-sm" title="No ISIN on record for this listing — can't be resolved to an analyzable ticker">
                                {s.symbol}
                              </span>
                            )}
                            <ExchangeBadge exchange={s.exchange} />
                          </div>
                        </td>

                        {/* Company */}
                        <td className="px-4 py-4 max-w-[200px]">
                          <span className="text-muted text-xs truncate block" title={s.name ?? ''}>
                            {s.name ?? '—'}
                          </span>
                        </td>

                        {/* Cross Date */}
                        <td className="px-4 py-4">
                          <span className="text-xs font-mono text-muted/80 tabular-nums">
                            {s.trade_date}
                          </span>
                        </td>

                        {/* Cross */}
                        <td className="px-4 py-4">
                          <CrossBadge cross={s.cross} />
                        </td>

                        {/* Regime */}
                        <td className="px-4 py-4">
                          <RegimeBadge inGolden={s.in_golden_cross} />
                        </td>

                        {/* Close price */}
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-tx">
                            {s.close_price != null ? `₹${s.close_price.toFixed(2)}` : '—'}
                          </span>
                        </td>

                        {/* EMA 20 */}
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-muted">
                            {s.ema20 != null ? s.ema20.toFixed(2) : '—'}
                          </span>
                        </td>

                        {/* EMA 50 */}
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-muted">
                            {s.ema50 != null ? s.ema50.toFixed(2) : '—'}
                          </span>
                        </td>

                        {/* RSI (14) */}
                        <td className="px-4 py-4 text-right">
                          <span className={`font-mono tabular-nums text-xs font-semibold ${rsiColor(s.rsi14)}`}>
                            {s.rsi14 != null ? s.rsi14.toFixed(1) : '—'}
                          </span>
                        </td>

                        {/* Volume spike */}
                        <td className="px-4 py-4">
                          {s.volume_spike ? <VolumeSpikeBadge /> : <span className="text-muted text-[10px]">—</span>}
                        </td>

                        {/* Market cap */}
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-muted">
                            {fmtMarketCap(s.market_cap_cr)}
                          </span>
                        </td>

                        {/* Turnover (20d) */}
                        <td className="px-4 py-4 text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            {s.avg_turnover_20d != null && s.avg_turnover_20d < _ILLIQUID_TURNOVER_THRESHOLD && (
                              <IlliquidBadge />
                            )}
                            <span className="font-mono tabular-nums text-xs text-muted">
                              {fmtTurnover(s.avg_turnover_20d)}
                            </span>
                          </div>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr className="border-b border-border/60 bg-card/60">
                          <td colSpan={12} className="px-6 py-5">
                            {history === 'loading' || history === undefined ? (
                              <p className="text-xs text-muted py-8 text-center">Loading chart…</p>
                            ) : history === 'error' ? (
                              <p className="text-xs text-sell py-8 text-center">Could not load EMA history for {s.symbol}.</p>
                            ) : (
                              <>
                                <CrossOutcomeSummary events={history.cross_events} />
                                <EmaChart series={history.series} />
                              </>
                            )}
                          </td>
                        </tr>
                      )}
                      </Fragment>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Footer hint */}
        {!loading && !error && signals.length > 0 && (
          <p className="text-[10px] text-muted/50 mt-3">
            Click a symbol to run full analysis. A BSE listing without an ISIN on record can&apos;t be resolved to an analyzable ticker yet.
          </p>
        )}

      </div>
    </main>
  );
}
