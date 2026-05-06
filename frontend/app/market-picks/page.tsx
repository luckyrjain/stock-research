'use client';

import { useState, useRef, useCallback, useMemo } from 'react';
import Link from 'next/link';
import type {
  MarketPicksPhase,
  MarketPicksSSEMessage,
  MarketPick,
} from '@/types';
import MarketPicksDashboard from '@/components/market-picks-dashboard';

interface SourceState {
  name: string;
  type: string;
  status: 'pending' | 'ok' | 'empty';
  articles: number;
}

interface ResearchState {
  symbol: string;
  ok: boolean | null;
}

const SOURCE_TYPE_ICON: Record<string, string> = {
  news:      '📰',
  brokerage: '🏦',
  platform:  '📊',
};

const PIPELINE_STEPS: { id: MarketPicksPhase; label: string; desc: string }[] = [
  { id: 'scanning',      label: 'Scraping',    desc: 'Fetching articles' },
  { id: 'extracting',    label: 'Extracting',  desc: 'AI reading articles' },
  { id: 'consolidating', label: 'Validating',  desc: 'NSE ticker check' },
  { id: 'researching',   label: 'Researching', desc: 'Due diligence' },
  { id: 'scoring',       label: 'Scoring',     desc: 'Ranking picks' },
];

const PHASE_ORDER: MarketPicksPhase[] = [
  'scanning', 'extracting', 'consolidating', 'researching', 'scoring', 'done',
];

function PipelineStepper({ phase }: { phase: MarketPicksPhase }) {
  const currentIdx = PHASE_ORDER.indexOf(phase);
  return (
    <div className="flex items-center gap-0 mb-8">
      {PIPELINE_STEPS.map((step, i) => {
        const stepIdx  = PHASE_ORDER.indexOf(step.id);
        const isDone   = currentIdx > stepIdx;
        const isActive = currentIdx === stepIdx;
        return (
          <div key={step.id} className="flex items-center flex-1 min-w-0">
            <div className="flex flex-col items-center flex-1 min-w-0">
              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold border-2 transition-all
                ${isDone   ? 'bg-buy border-buy text-white' :
                  isActive ? 'bg-accent border-accent text-white' :
                  'bg-surface border-border text-muted'}`}>
                {isDone ? '✓' : <span className={isActive ? 'text-white' : ''}>{i + 1}</span>}
              </div>
              <div className="mt-1.5 text-center px-1">
                <div className={`text-[11px] font-semibold leading-none
                  ${isDone ? 'text-buy' : isActive ? 'text-accent' : 'text-muted'}`}>
                  {step.label}
                </div>
                <div className="text-[9px] text-muted/70 mt-0.5 hidden sm:block leading-tight">
                  {step.desc}
                </div>
              </div>
            </div>
            {i < PIPELINE_STEPS.length - 1 && (
              <div className={`h-px flex-1 mx-1 transition-all
                ${currentIdx > stepIdx ? 'bg-buy/50' : 'bg-border'}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function SourceCard({ s }: { s: SourceState }) {
  return (
    <div className={`rounded-xl border p-3 transition-all duration-300
      ${s.status === 'ok'    ? 'border-buy/30 bg-buy/5' :
        s.status === 'empty' ? 'border-border bg-surface opacity-50' :
        'border-border bg-card'}`}>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-base">{SOURCE_TYPE_ICON[s.type] ?? '🔍'}</span>
        {s.status === 'ok' ? (
          <span className="text-buy text-xs font-bold">✓ {s.articles}</span>
        ) : s.status === 'empty' ? (
          <span className="text-muted text-xs">—</span>
        ) : (
          <div className="w-3 h-3 rounded-full border border-accent border-t-transparent animate-spin-slow" />
        )}
      </div>
      <div className="text-xs font-semibold text-tx leading-tight">{s.name}</div>
      <div className="text-[10px] text-muted mt-0.5 capitalize">{s.type}</div>
    </div>
  );
}

function ShimmerPill() {
  return (
    <div className="h-8 w-24 rounded-lg bg-surface border border-border overflow-hidden relative">
      <div className="absolute inset-0 -translate-x-full animate-[shimmer_1.5s_infinite]
                      bg-gradient-to-r from-transparent via-accent/10 to-transparent" />
    </div>
  );
}

export default function MarketPicksPage() {
  const [phase, setPhase]       = useState<MarketPicksPhase>('idle');
  const [sources, setSources]   = useState<SourceState[]>([]);
  const [research, setResearch] = useState<ResearchState[]>([]);
  const [articleCount, setArticleCount]   = useState(0);
  const [totalBatches, setTotalBatches]   = useState(0);
  const [batchDone, setBatchDone]         = useState(0);
  const [extractFound, setExtractFound]   = useState(0);
  const [uniquePicks, setUniquePicks]     = useState(0);
  const [validated, setValidated]         = useState<{ symbol: string; ok: boolean }[]>([]);
  const [picks, setPicks]         = useState<MarketPick[]>([]);
  const [generatedAt, setGeneratedAt] = useState('');
  const [fromCache, setFromCache] = useState(false);
  const [error, setError]         = useState<string | null>(null);

  const esRef   = useRef<EventSource | null>(null);
  const doneRef = useRef(false);

  const startScan = useCallback((force = false) => {
    esRef.current?.close();
    setPicks([]);
    setSources([]);
    setResearch([]);
    setValidated([]);
    setFromCache(false);
    setArticleCount(0);
    setTotalBatches(0);
    setBatchDone(0);
    setExtractFound(0);
    setUniquePicks(0);
    setError(null);
    doneRef.current = false;
    setPhase('scanning');

    const es = new EventSource(force ? '/api/market-picks?force=true' : '/api/market-picks');
    esRef.current = es;

    es.onmessage = (e) => {
      let msg: MarketPicksSSEMessage;
      try { msg = JSON.parse(e.data); } catch { return; }

      switch (msg.event) {
        case 'picks_start':
          setSources(msg.sources.map(s => ({
            name: s.name, type: s.type, status: 'pending', articles: 0,
          })));
          break;
        case 'source_done':
          setSources(prev => prev.map(s =>
            s.name === msg.source
              ? { ...s, status: msg.status, articles: msg.articles }
              : s
          ));
          break;
        case 'extracting':
          setArticleCount(msg.total_articles);
          setTotalBatches(msg.total_batches);
          setPhase('extracting');
          break;
        case 'extract_progress':
          setBatchDone(msg.batch);
          setExtractFound(msg.found_so_far);
          break;
        case 'consolidating':
          setUniquePicks(msg.unique);
          setValidated([]);
          setPhase('consolidating');
          break;
        case 'validate_progress':
          setValidated(prev => [...prev, { symbol: msg.symbol, ok: msg.ok }]);
          break;
        case 'researching':
          setPhase('researching');
          setResearch(msg.stocks.map(sym => ({ symbol: sym, ok: null })));
          break;
        case 'stock_researched':
          setResearch(prev => prev.map(r =>
            r.symbol === msg.symbol ? { ...r, ok: msg.ok } : r
          ));
          break;
        case 'scoring':
          setPhase('scoring');
          break;
        case 'done':
          doneRef.current = true;
          setPicks(msg.picks);
          setGeneratedAt(msg.generated_at);
          setFromCache(msg.from_cache ?? false);
          setPhase('done');
          es.close();
          break;
        case 'error':
          setError(msg.message);
          setPhase('error');
          es.close();
          break;
      }
    };

    es.onerror = () => {
      if (!doneRef.current) {
        setError('Connection to server lost. Please try again.');
        setPhase('error');
      }
      es.close();
    };
  }, []);

  const isRunning = PHASE_ORDER.indexOf(phase) >= 0 && phase !== 'idle' && phase !== 'done' && phase !== 'error';

  // Article breakdown by type for the extracting panel
  const articlesByType = useMemo(() => {
    const counts: Record<string, number> = { news: 0, brokerage: 0, platform: 0 };
    for (const s of sources) {
      if (s.status === 'ok') counts[s.type] = (counts[s.type] ?? 0) + s.articles;
    }
    return counts;
  }, [sources]);

  // Research progress count
  const researchDone = research.filter(r => r.ok !== null).length;

  return (
    <main className="min-h-screen bg-bg text-tx">
      {/* keyframes for shimmer — injected via style tag */}
      <style>{`
        @keyframes shimmer {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
      `}</style>

      <div className="max-w-5xl mx-auto px-4 pt-8 pb-16">

        {/* Nav */}
        <div className="flex items-center gap-4 mb-8 pb-4 border-b border-border">
          <Link href="/" className="text-base font-black tracking-tight text-tx">
            Stock<span className="text-accent">Research</span> AI
          </Link>
          <span className="text-border-hi">|</span>
          <span className="text-sm font-semibold text-accent">Market Picks</span>
          {isRunning && (
            <button
              onClick={() => { esRef.current?.close(); setPhase('idle'); }}
              className="ml-auto text-xs text-muted hover:text-tx transition-colors"
            >
              Cancel
            </button>
          )}
        </div>

        {/* ── Idle ── */}
        {phase === 'idle' && (
          <div className="max-w-2xl mx-auto text-center animate-fade-up">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full
                            bg-accent/10 border border-accent/20 text-accent text-xs font-semibold mb-6">
              Multi-agent · 10 sources · AI-ranked
            </div>
            <h1 className="text-4xl font-black tracking-tight mb-3">
              Top Picks<span className="text-accent"> This Week</span>
            </h1>
            <p className="text-muted text-sm leading-relaxed mb-8 max-w-lg mx-auto">
              Scans ET Markets, LiveMint, NDTV Profit, Hindu BusinessLine, broker research and more.
              Runs due diligence on every stock and ranks by confidence score.
            </p>
            <div className="grid grid-cols-3 gap-4 mb-10 text-left">
              {[
                { icon: '🔍', title: 'Scan 10 sources', desc: 'News, brokerages, analyst picks' },
                { icon: '🤖', title: 'AI due diligence', desc: 'Signals, fundamentals, momentum' },
                { icon: '📈', title: 'Confidence ranked', desc: 'Sorted by score · firms count' },
              ].map(({ icon, title, desc }) => (
                <div key={title} className="bg-card border border-border rounded-xl p-4">
                  <div className="text-2xl mb-2">{icon}</div>
                  <div className="text-sm font-semibold text-tx">{title}</div>
                  <div className="text-xs text-muted mt-0.5">{desc}</div>
                </div>
              ))}
            </div>
            <button
              onClick={() => startScan()}
              className="px-8 py-3.5 rounded-xl bg-accent text-white font-bold text-sm
                         hover:bg-accent/90 active:scale-95 transition-all shadow-lg shadow-accent/20"
            >
              Scan This Week's Picks
            </button>
          </div>
        )}

        {/* ── Active pipeline phases ── */}
        {isRunning && (
          <div className="animate-fade-up">
            <PipelineStepper phase={phase} />

            {/* Scanning */}
            {phase === 'scanning' && (
              <div>
                <p className="text-sm text-muted mb-4">Fetching articles from all sources in parallel…</p>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
                  {sources.map(s => <SourceCard key={s.name} s={s} />)}
                </div>
              </div>
            )}

            {/* Extracting: parallel batches with live stock count */}
            {phase === 'extracting' && (
              <div>
                {/* Stats row */}
                <div className="grid grid-cols-3 gap-3 mb-5">
                  {[
                    { label: 'Articles',        value: articleCount,   color: 'text-accent' },
                    { label: 'News',            value: articlesByType.news ?? 0,  color: 'text-tx' },
                    { label: 'Broker research', value: (articlesByType.brokerage ?? 0) + (articlesByType.platform ?? 0), color: 'text-tx' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-card border border-border rounded-xl px-4 py-3">
                      <div className={`text-2xl font-black ${color}`}>{value}</div>
                      <div className="text-[11px] text-muted mt-0.5">{label}</div>
                    </div>
                  ))}
                </div>

                {/* Batch progress bar */}
                <div className="bg-card border border-accent/20 rounded-xl px-5 py-4 mb-5">
                  <div className="flex items-center justify-between mb-3">
                    <div className="text-sm font-semibold text-tx">
                      {batchDone < totalBatches
                        ? `Running ${totalBatches} parallel AI batches…`
                        : 'Extraction complete'}
                    </div>
                    <div className="text-right">
                      <span className="text-lg font-black text-accent">{extractFound}</span>
                      <span className="text-xs text-muted ml-1">stocks found</span>
                    </div>
                  </div>
                  {/* Per-batch cells */}
                  <div className="flex gap-2 mb-3">
                    {Array.from({ length: totalBatches || 3 }).map((_, i) => (
                      <div key={i} className={`flex-1 rounded-md h-2 transition-all duration-500
                        ${i < batchDone   ? 'bg-buy' :
                          i === batchDone ? 'bg-accent animate-pulse' :
                          'bg-border'}`} />
                    ))}
                  </div>
                  <div className="text-[11px] text-muted">
                    {batchDone < totalBatches
                      ? `Batch ${batchDone + 1} of ${totalBatches} · identifying buy calls, analyst upgrades, top-pick lists`
                      : `All ${totalBatches} batches done · ${extractFound} candidates found`}
                  </div>
                </div>

                {/* Faded source cards */}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 opacity-25 pointer-events-none">
                  {sources.map(s => <SourceCard key={s.name} s={s} />)}
                </div>
              </div>
            )}

            {/* Consolidating: live per-symbol validation results */}
            {phase === 'consolidating' && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <div className="text-sm font-semibold text-tx">
                      Validating <span className="text-accent">{uniquePicks} stocks</span> on NSE / BSE
                    </div>
                    <div className="text-xs text-muted mt-0.5">
                      Confirming tickers, deduplicating, resolving company names — 8 in parallel
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <span className="text-lg font-black text-buy">{validated.filter(v => v.ok).length}</span>
                    <span className="text-xs text-muted ml-1">confirmed</span>
                    {validated.filter(v => !v.ok).length > 0 && (
                      <div className="text-[10px] text-muted/60">
                        {validated.filter(v => !v.ok).length} unresolved
                      </div>
                    )}
                  </div>
                </div>

                {/* Validation progress bar */}
                <div className="h-1 rounded-full bg-border overflow-hidden mb-5">
                  <div
                    className="h-full bg-accent rounded-full transition-all duration-300"
                    style={{ width: uniquePicks > 0 ? `${(validated.length / uniquePicks) * 100}%` : '0%' }}
                  />
                </div>

                {/* Live ticker pills: resolved ones replace shimmer */}
                <div className="flex flex-wrap gap-2">
                  {validated.map(v => (
                    <div key={v.symbol}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                                  border transition-all duration-200
                        ${v.ok
                          ? 'bg-buy/10 border-buy/30 text-buy'
                          : 'bg-surface border-border text-muted/50 line-through'}`}>
                      {v.ok ? '✓' : '✗'} {v.symbol}
                    </div>
                  ))}
                  {/* Remaining shimmer slots */}
                  {Array.from({ length: Math.max(0, uniquePicks - validated.length) }).map((_, i) => (
                    <ShimmerPill key={`shimmer-${i}`} />
                  ))}
                </div>
              </div>
            )}

            {/* Researching */}
            {phase === 'researching' && (
              <div>
                {/* Progress header */}
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <div className="text-sm font-semibold text-tx">
                      Running due diligence on {research.length} stocks
                    </div>
                    <div className="text-xs text-muted mt-0.5">
                      Fetching live price, fundamentals, and running signal engine in parallel
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-2xl font-black text-accent">{researchDone}</div>
                    <div className="text-[10px] text-muted">of {research.length} done</div>
                  </div>
                </div>

                {/* Progress bar */}
                <div className="h-1 rounded-full bg-border overflow-hidden mb-5">
                  <div
                    className="h-full bg-accent rounded-full transition-all duration-500"
                    style={{ width: research.length > 0 ? `${(researchDone / research.length) * 100}%` : '0%' }}
                  />
                </div>

                {/* Stock badges */}
                <div className="flex flex-wrap gap-2">
                  {research.map(r => (
                    <div
                      key={r.symbol}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                                  border transition-all duration-300
                        ${r.ok === true  ? 'bg-buy/10 border-buy/30 text-buy' :
                          r.ok === false ? 'bg-sell/10 border-sell/30 text-sell/70' :
                          'bg-card border-border text-muted'}`}
                    >
                      {r.ok === null ? (
                        <div className="w-2.5 h-2.5 rounded-full border border-current border-t-transparent animate-spin-slow" />
                      ) : r.ok ? '✓' : '✗'}
                      {r.symbol}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Scoring */}
            {phase === 'scoring' && (
              <div>
                <div className="bg-card border border-accent/20 rounded-xl px-5 py-4 flex items-start gap-4 mb-6">
                  <div className="w-8 h-8 rounded-full bg-accent/15 border border-accent/30
                                  flex items-center justify-center shrink-0 mt-0.5">
                    <div className="w-3 h-3 rounded-full border-2 border-accent border-t-transparent animate-spin-slow" />
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-tx">
                      AI analyst generating buy thesis for {research.length} stocks
                    </div>
                    <div className="text-xs text-muted mt-1 leading-relaxed">
                      Computing bull/bear factors, entry price, target, stop-loss, and confidence scores
                      from signal engine results and fundamentals.
                    </div>
                  </div>
                </div>

                {/* Skeleton preview of incoming table */}
                <div className="rounded-xl border border-border overflow-hidden opacity-30 pointer-events-none">
                  <div className="bg-surface px-4 py-3 border-b border-border flex gap-6">
                    {['#', 'Stock', 'Confidence', 'Signal', 'Price'].map(h => (
                      <div key={h} className="h-3 w-12 bg-border rounded" />
                    ))}
                  </div>
                  {[...Array(5)].map((_, i) => (
                    <div key={i} className="px-4 py-3 border-b border-border flex items-center gap-6">
                      <div className="h-4 w-4 bg-border rounded" />
                      <div className="h-4 w-20 bg-border rounded" />
                      <div className="h-2 w-28 bg-border rounded-full" />
                      <div className="h-5 w-10 bg-border rounded" />
                      <div className="h-4 w-16 bg-border rounded" />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Error ── */}
        {phase === 'error' && error && (
          <div className="max-w-2xl mx-auto">
            <div className="px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm
                            flex items-start justify-between gap-4">
              <span>{error}</span>
              <button
                onClick={() => startScan()}
                className="shrink-0 px-3 py-1 rounded-lg text-xs font-semibold border border-sell/40
                           hover:bg-sell/10 transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {/* ── Done ── */}
        {phase === 'done' && picks.length > 0 && (
          <MarketPicksDashboard
            picks={picks}
            generatedAt={generatedAt}
            fromCache={fromCache}
            onRescan={() => startScan(true)}
          />
        )}

        {phase === 'done' && picks.length === 0 && (
          <div className="text-center py-16 text-muted">
            <p className="text-lg">No picks found this week.</p>
            <button onClick={() => startScan()} className="mt-4 text-accent text-sm hover:underline">
              Try again
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
