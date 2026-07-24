'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Phase, Report, SSEMessage, TaskName, TaskStatus } from '@/types';

const ALL_TASKS: TaskName[] = ['stock_info', 'research', 'news', 'shareholding', 'mf_holdings'];

function initStatus(): Record<TaskName, TaskStatus> {
  return Object.fromEntries(ALL_TASKS.map(t => [t, 'idle'])) as Record<TaskName, TaskStatus>;
}

// The per-symbol SSE analysis pipeline (open EventSource, track task-by-task
// progress, land on a done/error phase), extracted from the home page so
// /compare can run one of these per column without duplicating the state
// machine. Behavior is unchanged from the original home-page implementation.
export function useStockAnalysis() {
  const [phase, setPhase]                 = useState<Phase>('idle');
  const [taskStatus, setTaskStatus]       = useState<Record<TaskName, TaskStatus>>(initStatus());
  const [report, setReport]               = useState<Report | null>(null);
  const [error, setError]                 = useState<string | null>(null);
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

  // Close the in-flight stream if the component using this hook unmounts
  // mid-stream (e.g. /compare swapping symbols, or navigating away).
  useEffect(() => () => { esRef.current?.close(); }, []);

  const isRunning = phase === 'fetching' || phase === 'analysing';
  const isIdle    = phase === 'idle';

  return {
    phase, taskStatus, report, error, currentSymbol,
    isRunning, isIdle,
    handleAnalyse, handleHardRefresh,
  };
}
