'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Phase, Report, SSEMessage, TaskName, TaskStatus } from '@/types';
import { useToast } from '@/components/toast';

const ALL_TASKS: TaskName[] = ['stock_info', 'research', 'news', 'shareholding', 'mf_holdings', 'filings'];

function initStatus(): Record<TaskName, TaskStatus> {
  return Object.fromEntries(ALL_TASKS.map(t => [t, 'idle'])) as Record<TaskName, TaskStatus>;
}

// Everything reduceSSEMessage needs to decide the next transition — just the
// refreshingRef-mirrored flag (background-refresh fencing, see STATE-01
// above) and the symbol the stream is for (only used to word the refresh-
// failure toast). Deliberately NOT the full hook state — task_done's status
// merge is expressed as a `taskUpdate` the caller folds into its own prior
// taskStatus, so the reducer never needs to see it.
export interface SSEReducerState {
  refreshing: boolean;
  symbol: string;
}

// A partial update: only the fields a given message actually changes are
// present. The caller (useStockAnalysis's onmessage handler) applies each
// one via its existing setState/ref-mutation calls; this function itself
// never touches state, a ref, or the EventSource.
export interface SSEReducerResult {
  taskStatus?: Record<TaskName, TaskStatus>;           // full replacement (start)
  taskUpdate?: { task: TaskName; status: TaskStatus }; // merge into prior taskStatus (task_done)
  phase?: Phase;
  report?: Report;
  refreshing?: boolean;
  done?: boolean;          // doneRef.current = true
  closeStream?: boolean;   // es.close()
  toastMessage?: string;   // showError(...) — background-refresh failure
  errorMessage?: string;   // setError(...) — foreground failure
}

// Pure "given the current refreshing/symbol and an incoming SSE message,
// what changes" function — see this file's own module comment for why this
// was pulled out of the onmessage switch.
export function reduceSSEMessage(state: SSEReducerState, msg: SSEMessage): SSEReducerResult {
  switch (msg.event) {
    case 'start': {
      if (state.refreshing) return {};
      const next = initStatus();
      // cached tasks stay marked cached; everything else (stale + fresh) is running
      ALL_TASKS.forEach(t => {
        next[t] = msg.cached.includes(t) ? 'cached' : 'running';
      });
      return { taskStatus: next };
    }
    case 'task_done': {
      if (state.refreshing) return {};
      return { taskUpdate: { task: msg.task as TaskName, status: msg.ok ? 'ok' : 'fail' } };
    }
    case 'analysing': {
      if (state.refreshing) return {};
      return { phase: 'analysing' };
    }
    case 'done': {
      return { done: true, report: msg.report, phase: 'done', refreshing: false, closeStream: true };
    }
    case 'error': {
      if (state.refreshing) {
        // Old report stays on screen (STATE-01) — a background-refresh
        // failure is a toast, not a page-blocking error state.
        return {
          toastMessage: `Couldn't refresh ${state.symbol}. ${msg.message}`,
          refreshing: false,
          closeStream: true,
        };
      }
      return { errorMessage: msg.message, phase: 'error', closeStream: true };
    }
    default:
      // An unrecognized event value (a malformed/future payload the JSON.parse
      // above can't catch) is a no-op, same as the pre-extraction inline
      // switch — never crash the SSE handler on an untrusted network payload.
      return {};
  }
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

      // The hook's own job now: own the EventSource, parse the message, ask
      // the pure reducer what changed, apply it via the usual setState/ref
      // calls. See reduceSSEMessage above for the actual transition logic.
      const result = reduceSSEMessage({ refreshing: refreshingRef.current, symbol }, msg);

      if (result.taskStatus) setTaskStatus(result.taskStatus);
      if (result.taskUpdate) {
        const { task, status } = result.taskUpdate;
        setTaskStatus(prev => ({ ...prev, [task]: status }));
      }
      if (result.phase) setPhase(result.phase);
      if (result.report !== undefined) {
        reportRef.current = result.report;
        setReport(result.report);
      }
      if (result.refreshing !== undefined) setRefreshing(result.refreshing);
      if (result.done) doneRef.current = true;
      if (result.toastMessage) showError(result.toastMessage);
      if (result.errorMessage !== undefined) setError(result.errorMessage);
      if (result.closeStream) es.close();
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
