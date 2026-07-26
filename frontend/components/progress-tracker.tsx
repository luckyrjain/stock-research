import type { TaskName, TaskStatus, Phase } from '@/types';

const TASK_LABELS: Record<TaskName, string> = {
  stock_info:   'Market Data',
  research:     'Fundamentals',
  news:         'News',
  shareholding: 'Shareholding',
  mf_holdings:  'MF Holdings',
  filings:      'Filings',
};

const ALL_TASKS: TaskName[] = ['stock_info', 'research', 'news', 'shareholding', 'mf_holdings', 'filings'];

interface Props {
  taskStatus: Record<TaskName, TaskStatus>;
  phase: Phase;
}

const STATUS_LABELS: Record<TaskStatus, string> = {
  idle: 'pending', running: 'fetching…', ok: 'done', fail: 'failed', cached: 'cached',
};

function StepChip({ name, status }: { name: TaskName; status: TaskStatus }) {
  const configs: Record<TaskStatus, { cls: string; icon: React.ReactNode }> = {
    idle:   { cls: 'bg-card border-border text-muted',              icon: '○' },
    running:{ cls: 'bg-accent/10 border-accent text-accent',        icon: <span className="inline-block animate-spin-slow">⟳</span> },
    ok:     { cls: 'bg-buy/10 border-buy/30 text-buy',              icon: '✓' },
    fail:   { cls: 'bg-sell/10 border-sell/30 text-sell',           icon: '✕' },
    cached: { cls: 'bg-card border-border-hi text-muted',           icon: '↺' },
  };
  const { cls, icon } = configs[status];
  return (
    <div
      aria-label={`${TASK_LABELS[name]}: ${STATUS_LABELS[status]}`}
      className={`flex items-center gap-2 px-3.5 py-2 rounded-lg border text-[13px] font-medium transition-all duration-300 ${cls}`}
    >
      <span aria-hidden="true" className="text-[14px]">{icon}</span>
      <span>{TASK_LABELS[name]}</span>
      {status === 'cached' && <span className="text-[11px] text-muted/60">cached</span>}
    </div>
  );
}

export default function ProgressTracker({ taskStatus, phase }: Props) {
  return (
    <div className="mb-12 animate-fade-up" role="status" aria-live="polite">
      <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-4">
        Fetching Data
      </p>

      <div className="flex flex-wrap gap-2.5 mb-4">
        {ALL_TASKS.map(n => (
          <StepChip key={n} name={n} status={taskStatus[n]} />
        ))}
      </div>

      <div className={`flex items-center gap-3 px-4 py-3 rounded-lg border text-[14px] transition-all duration-300 ${
        phase === 'analysing'
          ? 'bg-accent/10 border-accent text-accent'
          : 'bg-card border-border text-muted'
      }`}>
        <span aria-hidden="true" className={phase === 'analysing' ? 'inline-block animate-spin-slow' : 'opacity-40'}>
          ⟳
        </span>
        <span>
          {phase === 'analysing' ? 'AI analysis in progress…' : 'Waiting for data…'}
        </span>
      </div>
    </div>
  );
}
