'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Phase, Report, SSEMessage, TaskName, TaskStatus } from '@/types';
import { useToast } from '@/components/toast';

const ALL_TASKS: TaskName[] = ['stock_info', 'research', 'news', 'shareholding', 'mf_holdings', 'filings'];

function initStatus(): Record<TaskName, TaskStatus> {
  return Object.fromEntries(ALL_TASKS.map(t => [t, 'idle'])) as Record<TaskName, TaskStatus>;
}

// The per-symbol SSE analysis pipeline (open EventSource, track task-by-task
// progress, land on a done/error phase), extracted from the home page so
// /compare can run one of these per column without duplicating the state
// machine, plus STATE-01 background-refresh support (see `refreshing` below).
export function useStockAnalysis() {
  const { showError } = useToast();
  const [phase, setPhase]                 = useState<Phase>('idle');
  const [taskStatus, setTaskStatus]       = useState<Record<TaskName, TaskStatus>>(initStatus());
  const [report, setReport]               = useState<Report | null>(null);
  const [error, setError]                 = useState<string | null>(null);
  const [currentSymbol, setCurrentSymbol] = useState<string | null>(null);
  // STATE-01 (design.md): a hard refresh of the symbol already on screen
  // keeps that report visible instead of wiping it — `refreshing` is the
  // in-place indicator for that case, distinct from `phase`, which never
  // leaves 'done' during a background refresh.
  const [refreshing, setRefreshing] = useState(false);
  const esRef   = useRef<EventSource | null>(null);
  const doneRef = useRef(false);
  const refreshingRef = useRef(false);
  // Mirror report/currentSymbol in refs so handleAnalyse can read the
  // latest value without depending on that state (which would change its
  // identity every render and needlessly re-fire effects it's passed to,
  // e.g. the home page's deep-link effect).
  const reportRef = useRef<Report | null>(null);
  const currentSymbolRef = useRef<string | null>(null);

  const handleAnalyse = useCallback((symbol: string, force = false) => {
    // Close any previous stream
    esRef.current?.close();

    const isBackgroundRefresh = force && reportRef.current != null && currentSymbolRef.current === symbol;
    refreshingRef.current = isBackgroundRefresh;

    currentSymbolRef.current = symbol;
    setCurrentSymbol(symbol);
    doneRef.current = false;

    if (isBackgroundRefresh) {
      setRefreshing(true);
    } else {
      setPhase('fetching');
      setTaskStatus(initStatus());
      reportRef.current = null;
      setReport(null);
      setError(null);
      setRefreshing(false);
    }

    const es = new EventSource(`/api/analyse/${symbol}?force=${force}`);
    esRef.current = es;

    es.onmessage = (e) => {
      let msg: SSEMessage;
      try { msg = JSON.parse(e.data); } catch { return; }

      switch (msg.event) {
        case 'start': {
          if (!refreshingRef.current) {
            const next = initStatus();
            // cached tasks stay marked cached; everything else (stale + fresh) is running
            ALL_TASKS.forEach(t => {
              next[t] = msg.cached.includes(t) ? 'cached' : 'running';
            });
            setTaskStatus(next);
          }
          break;
        }
        case 'task_done': {
          if (!refreshingRef.current) {
            setTaskStatus(prev => ({
              ...prev,
              [msg.task as TaskName]: msg.ok ? 'ok' : 'fail',
            }));
          }
          break;
        }
        case 'analysing': {
          if (!refreshingRef.current) setPhase('analysing');
          break;
        }
        case 'done': {
          doneRef.current = true;
          reportRef.current = msg.report;
          setReport(msg.report);
          setPhase('done');
          setRefreshing(false);
          es.close();
          break;
        }
        case 'error': {
          if (refreshingRef.current) {
            // Old report stays on screen (STATE-01) — a background-refresh
            // failure is a toast, not a page-blocking error state.
            showError(`Couldn't refresh ${symbol}. ${msg.message}`);
            setRefreshing(false);
          } else {
            setError(msg.message);
            setPhase('error');
          }
          es.close();
          break;
        }
      }
    };

    es.onerror = () => {
      if (!doneRef.current) {
        if (refreshingRef.current) {
          showError(`Couldn't refresh ${symbol} — connection to server lost.`);
          setRefreshing(false);
        } else {
          setError('Connection to server lost. Please try again.');
          setPhase('error');
        }
      }
      es.close();
    };
  }, [showError]);

  const handleHardRefresh = useCallback(() => {
    if (currentSymbol) handleAnalyse(currentSymbol, true);
  }, [currentSymbol, handleAnalyse]);

  // Close the in-flight stream if the component using this hook unmounts
  // mid-stream (e.g. /compare swapping symbols, or navigating away).
  useEffect(() => () => { esRef.current?.close(); }, []);

  const isRunning = phase === 'fetching' || phase === 'analysing' || refreshing;
  const isIdle    = phase === 'idle';

  return {
    phase, taskStatus, report, error, currentSymbol, refreshing,
    isRunning, isIdle,
    handleAnalyse, handleHardRefresh,
  };
}
