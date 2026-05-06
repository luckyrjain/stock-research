'use client';

import { useState, useRef, useCallback } from 'react';
import Link from 'next/link';
import type { TaskName, TaskStatus, Phase, SSEMessage, Report } from '@/types';
import TickerSearch     from '@/components/ticker-search';
import ProgressTracker  from '@/components/progress-tracker';
import ResultsDashboard from '@/components/results-dashboard';

const ALL_TASKS: TaskName[] = ['stock_info', 'research', 'news', 'shareholding', 'mf_holdings'];

function initStatus(): Record<TaskName, TaskStatus> {
  return Object.fromEntries(ALL_TASKS.map(t => [t, 'idle'])) as Record<TaskName, TaskStatus>;
}

export default function HomePage() {
  const [phase, setPhase]               = useState<Phase>('idle');
  const [taskStatus, setTaskStatus]     = useState<Record<TaskName, TaskStatus>>(initStatus());
  const [report, setReport]             = useState<Report | null>(null);
  const [error, setError]               = useState<string | null>(null);
  const [currentSymbol, setCurrentSymbol] = useState<string | null>(null);
  const esRef   = useRef<EventSource | null>(null);
  const doneRef = useRef(false);

  const handleAnalyse = useCallback((symbol: string, force = false) => {
    // Close any previous stream
    esRef.current?.close();

    setCurrentSymbol(symbol);
    setPhase('fetching');
    setTaskStatus(initStatus());
    setReport(null);
    setError(null);
    doneRef.current = false;

    const es = new EventSource(`/api/analyse/${symbol}?force=${force}`);
    esRef.current = es;

    es.onmessage = (e) => {
      let msg: SSEMessage;
      try { msg = JSON.parse(e.data); } catch { return; }

      switch (msg.event) {
        case 'start': {
          const next = initStatus();
          // cached tasks stay marked cached; everything else (stale + fresh) is running
          ALL_TASKS.forEach(t => {
            next[t] = msg.cached.includes(t) ? 'cached' : 'running';
          });
          setTaskStatus(next);
          break;
        }
        case 'task_done': {
          setTaskStatus(prev => ({
            ...prev,
            [msg.task as TaskName]: msg.ok ? 'ok' : 'fail',
          }));
          break;
        }
        case 'analysing': {
          setPhase('analysing');
          break;
        }
        case 'done': {
          doneRef.current = true;
          setReport(msg.report);
          setPhase('done');
          es.close();
          break;
        }
        case 'error': {
          setError(msg.message);
          setPhase('error');
          es.close();
          break;
        }
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

  const handleHardRefresh = useCallback(() => {
    if (currentSymbol) handleAnalyse(currentSymbol, true);
  }, [currentSymbol, handleAnalyse]);

  const isRunning = phase === 'fetching' || phase === 'analysing';
  const isIdle    = phase === 'idle';

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className={`max-w-5xl mx-auto px-4 ${isIdle ? 'py-16' : 'pt-8 pb-16'}`}>

        {isIdle ? (
          <div className="max-w-2xl mx-auto">
            <div className="mb-12 text-center">
              <h1 className="text-4xl font-black tracking-tight text-tx mb-2">
                Stock<span className="text-accent">Research</span> AI
              </h1>
              <p className="text-muted text-sm">AI-powered equity research for Indian markets</p>
              <Link
                href="/market-picks"
                className="inline-flex items-center gap-1.5 mt-4 px-4 py-1.5 rounded-full
                           bg-accent/10 border border-accent/20 text-accent text-xs font-semibold
                           hover:bg-accent/20 transition-colors"
              >
                📈 See this week's top picks →
              </Link>
            </div>
            <TickerSearch onAnalyse={handleAnalyse} disabled={isRunning} />
          </div>
        ) : (
          <>
            <div className="flex items-center gap-4 mb-5 pb-4 border-b border-border">
              <span className="text-base font-black tracking-tight text-tx">
                Stock<span className="text-accent">Research</span> AI
              </span>
              <Link
                href="/market-picks"
                className="text-xs font-semibold text-muted hover:text-accent transition-colors"
              >
                Market Picks →
              </Link>
            </div>

            <TickerSearch onAnalyse={handleAnalyse} disabled={isRunning} compact />

            {(phase === 'fetching' || phase === 'analysing') && (
              <ProgressTracker taskStatus={taskStatus} phase={phase} />
            )}

            {phase === 'error' && error && (
              <div className="mb-8 px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm flex items-start justify-between gap-4">
                <span>{error}</span>
                {currentSymbol && (
                  <button
                    onClick={() => handleAnalyse(currentSymbol)}
                    className="shrink-0 px-3 py-1 rounded-lg text-xs font-semibold
                      border border-sell/40 text-sell hover:bg-sell/10
                      transition-colors duration-150"
                  >
                    Try Again
                  </button>
                )}
              </div>
            )}

            {phase === 'done' && report && (
              <ResultsDashboard report={report} onHardRefresh={handleHardRefresh} />
            )}
          </>
        )}

      </div>
    </main>
  );
}
