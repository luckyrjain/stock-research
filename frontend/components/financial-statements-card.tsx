'use client';

import { useEffect, useState } from 'react';
import type { Concall, FinancialStatement, FinancialStatementsResponse } from '@/types';
import InfoTooltip from './info-tooltip';
import { Card } from './dashboard-primitives';
import { fmt } from './dashboard-format';

export function useFinancials(symbol: string): FinancialStatementsResponse | null {
  const [data, setData] = useState<FinancialStatementsResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    fetch(`/api/financials/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((d: FinancialStatementsResponse | null) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return data;
}

// One collapsible <details> table per statement — up to 10 years x however
// many rows Screener renders for this company, so this stays collapsed by
// default rather than dumping a wide, dense table into the page unasked.
function StatementTable({ title, statement }: { title: string; statement: FinancialStatement | null }) {
  if (!statement || statement.rows.length === 0) return null;
  return (
    <details className="group/stmt py-2">
      <summary className="cursor-pointer select-none list-none flex items-center justify-between
                           text-sm font-semibold text-tx [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-1.5">
          <span className="text-muted text-xs transition-transform group-open/stmt:rotate-90">›</span>
          {title}
        </span>
        <span className="text-[11px] font-normal text-muted">{statement.years.length} years</span>
      </summary>
      <div className="overflow-x-auto mt-2 -mx-1">
        <table className="text-xs w-full">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left px-1.5 py-1.5 text-muted font-semibold whitespace-nowrap">₹ Cr</th>
              {statement.years.map(y => (
                <th key={y} className="text-right px-2 py-1.5 text-muted font-semibold whitespace-nowrap">{y}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {statement.rows.map(row => (
              <tr key={row.label} className="border-b border-border/60 last:border-0">
                <td className="px-1.5 py-1.5 whitespace-nowrap text-tx">{row.label}</td>
                {row.values.map((v, i) => (
                  <td key={i} className="text-right px-2 py-1.5 font-mono text-tx whitespace-nowrap">
                    {v != null ? fmt(v, 0) : '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

// Up to 10 years of Screener's own yearly P&L / Balance Sheet / Cash Flow
// tables — previously this app only ever showed current-year ratios
// (Fundamentals card) and a short quarterly trend, never full statement
// history, the single biggest gap the frontend design review found versus
// Screener.in itself. `data` is lifted from ResultsDashboard (shared with
// the DCF block in the Valuation card below) rather than fetched again here.
export function FinancialStatementsCard({ data }: { data: FinancialStatementsResponse | null }) {
  if (!data || (!data.profit_loss && !data.balance_sheet && !data.cash_flow)) return null;
  return (
    <Card title={<>
      Financial Statements
      <InfoTooltip title="Financial Statements" align="left">
        <p>Up to 10 years of Screener.in&apos;s own yearly Profit &amp; Loss, Balance Sheet, and Cash Flow tables, in ₹ Cr.</p>
        <p>A blank cell is a genuine gap in that year&apos;s reporting for this company (e.g. before IPO), not a missing scrape.</p>
      </InfoTooltip>
    </>}>
      <div className="divide-y divide-border">
        <StatementTable title="Profit & Loss" statement={data.profit_loss} />
        <StatementTable title="Balance Sheet" statement={data.balance_sheet} />
        <StatementTable title="Cash Flow" statement={data.cash_flow} />
      </div>
    </Card>
  );
}

const _CONCALL_LINK_LABELS: { key: keyof Concall; label: string }[] = [
  { key: 'transcript_url', label: 'Transcript' },
  { key: 'ppt_url',        label: 'PPT' },
  { key: 'notes_url',      label: 'Notes' },
  { key: 'audio_url',      label: 'REC' },
];

// Primary-source management commentary — what the company actually said on
// its own quarterly earnings calls — scraped from Screener's own Concalls
// section. Previously this app only ever surfaced third-party news coverage
// and Screener's numeric ratios, never the calls themselves. `data` is
// lifted from ResultsDashboard (same fetch as FinancialStatementsCard/the
// DCF block, no second request) rather than fetched again here.
export function ConcallsCard({ concalls }: { concalls: Concall[] | undefined }) {
  if (!concalls || concalls.length === 0) return null;
  return (
    <Card title={<>
      Concalls
      <InfoTooltip title="Concalls" align="left">
        <p>Screener.in&apos;s own list of this company&apos;s quarterly earnings-call materials — transcript, investor presentation, Screener&apos;s own notes, and the audio recording, whichever of those Screener has published for each quarter.</p>
        <p>Real, primary-source management commentary, not a third-party summary — every link is exactly what Screener links to.</p>
      </InfoTooltip>
    </>}>
      <div className="divide-y divide-border">
        {concalls.map((c, i) => (
          <div key={i} className="flex items-center justify-between gap-2 py-2 first:pt-0 last:pb-0 text-sm">
            <span className="text-tx font-medium">{c.date}</span>
            <div className="flex items-center gap-2 flex-wrap justify-end">
              {_CONCALL_LINK_LABELS.map(({ key, label }) => {
                const url = c[key];
                if (typeof url !== 'string') return null;
                return (
                  <a
                    key={key}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] font-semibold px-2 py-0.5 rounded-full border border-border text-muted hover:text-accent hover:border-accent/40 transition-colors"
                  >
                    {label} ↗
                  </a>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
