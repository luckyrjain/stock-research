'use client';

import { useMemo } from 'react';
import type { MfHoldingsStakeDelta, Report, StockInfo } from '@/types';
import InfoTooltip from './info-tooltip';
import WatchlistButton from './watchlist-button';
import { Card, MetricRow, ExchangeTable, RangeBar } from './dashboard-primitives';
import { fmt, fmtCr, fmtVolume, fmtRatio, formatAge, humanizeMetaKey, formatMetaValue, normalizeRatioKey } from './dashboard-format';
import { usePeerComparison, PeerTable, SimilarStocksRail } from './peer-comparison-card';
import { useFinancials, FinancialStatementsCard, ConcallsCard } from './financial-statements-card';
import { InsiderActivityCard } from './insider-activity-card';
import { useStreetConsensus, StreetConsensusCard } from './street-consensus-card';
import ValuationSummaryStrip from './valuation-summary-strip';
import PriceSparkline from './price-sparkline';
import VerdictTimeline from './verdict-timeline';
import QuarterlyTrendCard from './quarterly-trend-card';

interface Props {
  report: Report;
  onHardRefresh?: () => void;
}

type FactorValue = string | number | null | undefined;
type FactorShape = string | Record<string, FactorValue>;

const REC_CONFIG = {
  BUY:  { bg: 'bg-buy/10',  border: 'border-buy/30',  text: 'text-buy',  badge: 'bg-buy  text-white', strip: 'bg-buy'  },
  SELL: { bg: 'bg-sell/10', border: 'border-sell/30', text: 'text-sell', badge: 'bg-sell text-white', strip: 'bg-sell' },
  HOLD: { bg: 'bg-hold/10', border: 'border-hold/30', text: 'text-hold', badge: 'bg-hold text-white', strip: 'bg-hold' },
};

const CONF_COLOR: Record<string, string> = {
  HIGH: 'text-buy', MEDIUM: 'text-hold', LOW: 'text-sell',
};

const SENT_COLOR: Record<string, string> = {
  Positive: 'text-buy', Neutral: 'text-muted', Negative: 'text-sell',
};

function formatScalar(value: FactorValue) {
  if (value == null || value === '') return null;
  return typeof value === 'number' ? fmt(value) : value;
}

function formatFactor(factor: FactorShape) {
  if (typeof factor === 'string') return factor;

  const primaryKeys = ['comment', 'metric', 'factor', 'factor1', 'factor2', 'factor3', 'data point', 'value'];
  const seen = new Set<string>();
  const parts: string[] = [];

  for (const key of primaryKeys) {
    const value = formatScalar(factor[key]);
    if (value) {
      parts.push(String(value));
      seen.add(key);
    }
  }

  for (const [key, rawValue] of Object.entries(factor)) {
    if (seen.has(key)) continue;
    const value = formatScalar(rawValue);
    if (!value) continue;
    parts.push(`${key}: ${value}`);
  }

  return parts.join(' • ') || '—';
}

function formatNewsHighlights(highlights: Report['analysis']['news_highlights']) {
  if (Array.isArray(highlights)) return highlights.join(' • ');
  return highlights ?? '';
}

function summaryBullets(text: string): string[] {
  // Split only where a sentence ends: [.!?] followed by whitespace + uppercase letter.
  // This avoids splitting on decimal numbers (5.04) or mid-sentence abbreviations.
  const sentences = text.split(/(?<=[.!?])\s+(?=[A-Z])/);
  return sentences.map(s => s.trim()).filter(s => s.length > 5);
}

export default function ResultsDashboard({ report, onHardRefresh }: Props) {
  const { analysis: a, signals: sig, stock_info: s, research: r, news, holdings: h, filings, filings_summary: fs, mf_holdings_trend: mfTrend } = report;

  const peers = usePeerComparison(report.symbol);
  const financials = useFinancials(report.symbol);
  const streetConsensus = useStreetConsensus(report.symbol);
  const percentileByNormalizedKey = useMemo(() => {
    const map: Record<string, number> = {};
    for (const [key, value] of Object.entries(peers?.percentiles ?? {})) {
      map[normalizeRatioKey(key)] = value;
    }
    return map;
  }, [peers]);

  const mfDeltaByFund = useMemo(() => {
    const map: Record<string, MfHoldingsStakeDelta> = {};
    for (const d of mfTrend ?? []) {
      if (d.delta_pct != null) map[d.fund] = d;
    }
    return map;
  }, [mfTrend]);

  const rec = (a?.recommendation ?? 'HOLD') as 'BUY' | 'SELL' | 'HOLD';
  const cfg = REC_CONFIG[rec];
  const exchangeQuotes: Array<[string, Partial<StockInfo>]> =
    s?.prices_by_exchange && Object.keys(s.prices_by_exchange).length > 0
      ? Object.entries(s.prices_by_exchange) as Array<[string, NonNullable<typeof s.prices_by_exchange>[string]]>
      : (s ? [[s.exchange ?? 'NSE', s]] : []);
  const primaryExchange = s?.primary_exchange ?? s?.exchange ?? 'NSE';

  return (
    <div className="animate-fade-up space-y-5">

      {/* Every configured LLM provider failed (or returned unparseable
          output past its guardrail retry) — this is crew.py's generic
          safe-fallback HOLD, not a real analyst call. Previously
          indistinguishable from a genuine HOLD anywhere in this report;
          see the `degraded` field's own comment in types/index.ts. */}
      {report.degraded && (
        <div className="px-5 py-3 rounded-xl bg-hold/10 border border-hold/30 text-sm flex items-start gap-2">
          <span className="text-hold shrink-0" aria-hidden="true">⚠</span>
          <span className="text-tx">
            <span className="font-semibold text-hold">Analysis degraded — </span>
            the AI analyst couldn&apos;t return a valid response, so this is a neutral safe fallback,
            not a genuine call. Market data below is real; try refreshing later for a full analysis.
          </span>
        </div>
      )}

      {/* ── 1. Hero strip: identity · verdict · price ── */}
      <div className={`rounded-xl border overflow-hidden ${cfg.border} ${cfg.bg}`}>
        <div className={`h-0.5 ${cfg.strip}`} />
        <div className="px-6 py-5 flex flex-wrap items-center justify-between gap-6">

          {/* Identity */}
          <div className="min-w-0">
            <div className="flex items-baseline gap-2.5 flex-wrap">
              <WatchlistButton
                symbol={report.symbol}
                company={s?.company_name ?? report.symbol}
                exchange={s?.exchange ?? 'NSE'}
              />
              <h2 className="text-xl font-bold text-tx">{s?.company_name ?? report.symbol}</h2>
              {s?.company_name && (
                <span className="font-mono text-sm font-semibold text-muted">{report.symbol}</span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
                {exchangeQuotes.length > 1 ? 'NSE + BSE' : (s?.exchange ?? 'NSE')}
              </span>
              {s?.industry && <span className="text-xs text-muted">{s.industry}</span>}
              {s?.sector && s.sector !== s.industry && (
                <span className="text-xs text-muted/50">· {s.sector}</span>
              )}
              {report.generated_at && (
                <span className="text-xs text-muted/60">· {formatAge(report.generated_at)}</span>
              )}
            </div>
          </div>

          {/* Verdict */}
          <div className="flex flex-col items-center gap-2 shrink-0">
            <div className="flex items-center gap-1.5">
              <span className={`text-3xl font-black px-8 py-2.5 rounded-xl ${cfg.badge}`}>{rec}</span>
              <InfoTooltip title="What does this mean?">
                <p><span className="text-buy font-semibold">BUY</span> — bullish thesis, favorable valuation and signals.</p>
                <p><span className="text-hold font-semibold">HOLD</span> — mixed or fairly-valued; no strong edge either way.</p>
                <p><span className="text-sell font-semibold">SELL</span> — bearish thesis, unfavorable valuation or signals.</p>
              </InfoTooltip>
            </div>
            <div className="flex items-center gap-3">
              {a?.confidence && (
                <span className={`text-[11px] font-semibold tracking-widest uppercase ${CONF_COLOR[a.confidence]}`}>
                  {a.confidence} confidence
                </span>
              )}
              {onHardRefresh && (
                <button
                  onClick={onHardRefresh}
                  className="flex items-center gap-1 text-[11px] font-medium text-muted
                    hover:text-tx transition-colors duration-150"
                >
                  <span>↺</span><span>Refresh</span>
                </button>
              )}
            </div>
          </div>

          {/* Price */}
          <div className="flex items-center gap-4">
            <ExchangeTable quotes={exchangeQuotes} primaryExchange={primaryExchange} />
            <PriceSparkline symbol={report.symbol} />
          </div>
        </div>
        <VerdictTimeline symbol={report.symbol} />
      </div>

      <ValuationSummaryStrip
        llmVerdict={a?.valuation?.verdict}
        dcf={financials?.dcf}
        peerPePercentile={percentileByNormalizedKey['pe']}
        absoluteAnchor={peers?.absolute_anchor ?? null}
        streetUpsidePct={streetConsensus?.numeric_consensus?.target_upside_pct}
      />

      {/* ── 2. Main grid: thesis (60%) + metrics (40%) ── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">

        {/* Investment Thesis — summary + bull/bear as one card */}
        <div className="lg:col-span-3">
          <Card title="Investment Thesis" className="h-full">
            {(() => {
              const text    = a?.summary ?? '';
              const bullets = summaryBullets(text);
              return bullets.length > 1 ? (
                <ul className="space-y-2 mb-5">
                  {bullets.map((b, i) => (
                    <li key={i} className="flex gap-2 text-sm text-tx leading-relaxed">
                      <span className={`${cfg.text} shrink-0 mt-px`}>›</span>
                      <span>{b}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-tx leading-relaxed mb-5">{text || 'Analysis pending.'}</p>
              );
            })()}

            <div className="grid grid-cols-2 gap-5 pt-4 border-t border-border">
              <div>
                <p className="text-[11px] font-semibold text-buy tracking-[1px] uppercase mb-3">Bull Case</p>
                <ul className="space-y-2">
                  {(a?.bull_factors ?? []).map((f, i) => (
                    <li key={i} className="flex gap-2 text-sm text-tx">
                      <span className="text-buy mt-0.5 shrink-0">▲</span>
                      <span>{formatFactor(f)}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-[11px] font-semibold text-sell tracking-[1px] uppercase mb-3">Bear Case</p>
                <ul className="space-y-2">
                  {(a?.bear_factors ?? []).map((f, i) => (
                    <li key={i} className="flex gap-2 text-sm text-tx">
                      <span className="text-sell mt-0.5 shrink-0">▼</span>
                      <span>{formatFactor(f)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Card>
        </div>

        {/* Key Metrics sidebar */}
        <div className="lg:col-span-2 space-y-4">
          <Card title="Key Metrics">
            <div className="grid grid-cols-3 gap-2 mb-4">
              {[
                { label: 'P/E', value: s?.pe_ratio      != null ? fmt(s.pe_ratio, 1)      : '—' },
                { label: 'P/B', value: s?.price_to_book != null ? fmt(s.price_to_book, 1) : '—' },
                { label: 'EPS', value: s?.eps            != null ? `₹${fmt(s.eps, 0)}`    : '—' },
              ].map(({ label, value }) => (
                <div key={label} className="bg-card-hi rounded-lg p-2.5 text-center">
                  <p className="text-xs text-muted uppercase tracking-wide mb-1">{label}</p>
                  <p className="font-mono font-bold text-lg text-tx leading-tight">{value}</p>
                </div>
              ))}
            </div>
            <MetricRow label="Market Cap" value={fmtCr(s?.market_cap_cr)} />
            <MetricRow label="Book Value" value={s?.book_value != null ? `₹${fmt(s.book_value)}` : '—'} />
            {s?.volume != null && (
              <MetricRow
                label="Volume"
                value={
                  s.avg_volume_10d != null
                    ? `${fmtVolume(s.volume)} (avg ${fmtVolume(s.avg_volume_10d)})`
                    : fmtVolume(s.volume)
                }
                colorClass={
                  // Elevated volume is a neutral "worth noting" signal, not
                  // inherently bullish or bearish — text-hold (amber) is this
                  // design system's "attention" tone; text-accent is reserved
                  // for interactive elements, never data labels (design.md).
                  s.avg_volume_10d != null && s.avg_volume_10d > 0 && s.volume > s.avg_volume_10d * 1.5
                    ? 'text-hold'
                    : 'text-tx'
                }
              />
            )}
            {s?.beta != null && <MetricRow label="Beta" value={fmt(s.beta, 2)} />}
            {s?.dividend_yield_pct != null && (
              <MetricRow label="Div Yield" value={`${fmt(s.dividend_yield_pct, 2)}%`} />
            )}
            <MetricRow label="52W High" value={s?.['52w_high'] != null ? `₹${fmt(s['52w_high'])}` : '—'} />
            <MetricRow label="52W Low"  value={s?.['52w_low']  != null ? `₹${fmt(s['52w_low'])}`  : '—'} />
            {s?.['52w_low'] != null && s?.['52w_high'] != null && s?.current_price != null && (
              <RangeBar low={s['52w_low']!} current={s.current_price} high={s['52w_high']!} />
            )}
          </Card>

          {r?.ratios && Object.keys(r.ratios).length > 0 && (
            <Card title="Fundamentals">
              {Object.entries(r.ratios).map(([k, v]) => (
                <MetricRow
                  key={k}
                  label={k}
                  value={fmtRatio(String(v))}
                  percentile={percentileByNormalizedKey[normalizeRatioKey(k)]}
                />
              ))}
            </Card>
          )}

          {(!r?.ratios || Object.keys(r.ratios).length === 0) && r?.nse_fallback_ratios && (
            <Card title="Fundamentals">
              <p className="text-xs text-muted mb-2">
                Screener.in had no ratios for this stock — showing EPS from NSE&apos;s own filings instead.
              </p>
              <MetricRow label="EPS" value={`₹${fmt(r.nse_fallback_ratios.eps, 2)}`} />
            </Card>
          )}

          <QuarterlyTrendCard trend={r?.quarterly_trend} />

          <FinancialStatementsCard data={financials} />

          <ConcallsCard concalls={financials?.concalls} />

          <PeerTable peers={peers} />

          <SimilarStocksRail peers={peers} />

          <InsiderActivityCard symbol={report.symbol} />

          <StreetConsensusCard consensus={streetConsensus} />

          {a?.valuation && (
            <Card title="Valuation">
              <p className={`text-sm font-semibold mb-1 ${
                a.valuation.verdict === 'Undervalued' ? 'text-buy' :
                a.valuation.verdict === 'Overvalued'  ? 'text-sell' : 'text-hold'
              }`}>{a.valuation.verdict}</p>
              <p className="text-sm text-muted leading-relaxed">{a.valuation.comment}</p>
              {financials?.dcf && (
                <div className={`mt-3 pt-3 border-t border-border text-xs ${
                  financials.dcf.verdict === 'Undervalued' ? 'text-buy' :
                  financials.dcf.verdict === 'Overvalued'  ? 'text-sell' : 'text-hold'
                }`}>
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="font-semibold">DCF Estimate: {financials.dcf.verdict}</span>
                    <InfoTooltip title="DCF Estimate" align="left">
                      <p>A simple two-stage discounted-cash-flow model off the Cash Flow statement&apos;s Operating Activity row — a different lens from the peer/history views above: &quot;cheap vs. what its cash flows are worth&quot;, not &quot;cheap vs. peers&quot;.</p>
                      <p>Assumes {financials.dcf.discount_rate}% discount rate, {financials.dcf.terminal_growth}% terminal growth, and projects at {financials.dcf.growth_rate_used}% (clamped historical OCF growth). Operating Cash Flow is used as a Free-Cash-Flow proxy since Screener&apos;s cash-flow table doesn&apos;t cleanly separate Capex — a simplification, not a full DCF.</p>
                      <p>Deterministic, computed — not LLM-generated. Not investment advice.</p>
                    </InfoTooltip>
                  </div>
                  <p className="text-tx">
                    Fair value ≈ <span className="font-mono font-semibold">₹{fmt(financials.dcf.fair_value_per_share, 2)}</span>
                    {' '}vs. current <span className="font-mono">₹{fmt(financials.dcf.current_price, 2)}</span>
                    {' '}({financials.dcf.upside_pct >= 0 ? '+' : ''}{fmt(financials.dcf.upside_pct, 1)}%)
                  </p>
                  <p className="text-muted mt-0.5">
                    Projected off ₹{fmt(financials.dcf.latest_ocf_cr, 0)} Cr latest operating cash flow
                  </p>
                </div>
              )}
            </Card>
          )}

          {sig?.signals && Object.keys(sig.signals).length > 0 && (
            <Card title={<>
              Quant Signals
              <InfoTooltip title="Quant Signals" align="left">
                <p>A composite score from valuation, growth, volume, filings, technical (RSI/EMA trend), and macro (FII/DII flow, RBI rate/inflation) signals, each scored −1 (bearish) to +1 (bullish) and blended into the Final Score.</p>
                <p>This runs independently of the AI analyst and is one input to its recommendation.</p>
              </InfoTooltip>
            </>}>
              {sig.final_score != null && (
                <MetricRow
                  label="Final Score"
                  value={fmt(sig.final_score, 2)}
                  colorClass={sig.final_score > 0 ? 'text-buy' : sig.final_score < 0 ? 'text-sell' : 'text-muted'}
                />
              )}
              {sig.verdict && (
                <MetricRow label="Signal Verdict" value={sig.verdict} />
              )}
              {Object.entries(sig.signals).map(([name, signal]) => {
                const metaEntries = Object.entries(signal.meta ?? {}).filter(([, v]) => v != null);
                return (
                  <MetricRow
                    key={name}
                    label={<>
                      {name} ({signal.value})
                      {metaEntries.length > 0 && (
                        <InfoTooltip title={`${name} — details`} align="left">
                          {metaEntries.map(([k, v]) => (
                            <p key={k} className="flex justify-between gap-3">
                              <span>{humanizeMetaKey(k)}</span>
                              <span className="font-mono text-tx">{formatMetaValue(v)}</span>
                            </p>
                          ))}
                        </InfoTooltip>
                      )}
                    </>}
                    value={fmt(signal.score, 2)}
                    colorClass={signal.score > 0 ? 'text-buy' : signal.score < 0 ? 'text-sell' : 'text-muted'}
                  />
                );
              })}
            </Card>
          )}
        </div>
      </div>

      {/* ── 3. Key Risks ── */}
      {(a?.key_risks ?? []).length > 0 && (
        <Card title="Key Risks">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {(a?.key_risks ?? []).map((risk, i) => (
              <div key={i} className="flex gap-2 text-sm bg-sell/5 border border-sell/15 rounded-lg px-3 py-2">
                <span className="text-sell shrink-0">⚠</span>
                <span className="text-tx">{risk}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* ── 4. Narrative row: context cards ── */}
      {(() => {
        const narrativeCards = [
          a?.business_quality && (
            <Card key="bq" title="Business Quality">
              <p className="text-sm text-tx leading-relaxed">{a.business_quality}</p>
            </Card>
          ),
          <Card key="it" title="Institutional Trend">
            <p className="text-sm text-tx leading-relaxed">{a?.institutional_trend ?? '—'}</p>
          </Card>,
          a?.news_sentiment && (
            <Card key="ns" title="News Sentiment">
              <p className={`text-sm font-semibold mb-1 ${SENT_COLOR[a.news_sentiment]}`}>
                {a.news_sentiment}
              </p>
              <p className="text-sm text-muted leading-relaxed">{formatNewsHighlights(a?.news_highlights)}</p>
            </Card>
          ),
        ].filter(Boolean);
        const colCls = narrativeCards.length === 3 ? 'sm:grid-cols-3' : narrativeCards.length === 2 ? 'sm:grid-cols-2' : '';
        return (
          <div className={`grid grid-cols-1 ${colCls} gap-4`}>{narrativeCards}</div>
        );
      })()}

      {/* ── 5. Data tables ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {h?.shareholding_pattern && Object.keys(h.shareholding_pattern).length > 0 && (
          <Card title="Shareholding Pattern">
            {h.pledge_pct != null && (
              <div className={`flex items-center justify-between text-sm mb-3 pb-3 border-b border-border ${
                h.pledge_pct > 0 ? 'text-sell' : 'text-muted'
              }`}>
                <span className="flex items-center gap-1.5">
                  {h.pledge_pct > 0 && <span aria-hidden="true">⚠</span>}
                  Promoter Pledge
                </span>
                <span className="font-mono font-semibold">{fmt(h.pledge_pct, 1)}%</span>
              </div>
            )}
            {Object.entries(h.shareholding_pattern).map(([k, v]) => (
              <div key={k} className="mb-2 last:mb-0">
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-muted">{k}</span>
                  <span className="font-mono font-semibold text-tx">{fmt(v, 1)}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-border overflow-hidden">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(v, 100)}%` }} />
                </div>
              </div>
            ))}
          </Card>
        )}

        {h?.mutual_funds && h.mutual_funds.length > 0 && (
          <Card title="Mutual Fund Holdings">
            <div className="divide-y divide-border">
              {h.mutual_funds.slice(0, 8).map((mf, i) => {
                const delta = mfDeltaByFund[mf.fund];
                return (
                  <div key={i} className="flex items-center justify-between py-2">
                    <span className="text-sm text-tx">{mf.fund}</span>
                    <span className="flex items-center gap-1.5">
                      <span className="text-sm font-mono font-semibold text-accent">{fmt(mf.holding_pct, 2)}%</span>
                      {delta != null && (
                        <span
                          className={`text-[11px] font-mono ${
                            delta.delta_pct! > 0 ? 'text-buy' : delta.delta_pct! < 0 ? 'text-sell' : 'text-muted'
                          }`}
                          title={delta.prior_as_of_date ? `vs. ${delta.prior_as_of_date} → ${delta.as_of_date}` : undefined}
                        >
                          {delta.delta_pct! > 0 ? '▲' : delta.delta_pct! < 0 ? '▼' : '–'} {Math.abs(delta.delta_pct!).toFixed(2)}%
                        </span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          </Card>
        )}
      </div>

      {/* ── 6. News ── */}
      {news && news.length > 0 && (
        <Card title="Recent News">
          <div className="divide-y divide-border">
            {news.slice(0, 5).map((n, i) => (
              <a key={i} href={n.url} target="_blank" rel="noopener noreferrer"
                className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0 group"
              >
                <span className="text-sm text-tx group-hover:text-accent transition-colors leading-snug">
                  {n.title}
                </span>
                <span className="text-[11px] text-muted">{n.source} · {n.published_at} ↗</span>
              </a>
            ))}
          </div>
        </Card>
      )}

      {/* ── 6b. Filings ── */}
      {filings && filings.length > 0 && (
        <Card title="Corporate Filings">
          {fs && (fs.corporate_actions.length > 0 || fs.rating_action || fs.next_results_date) && (
            <div className="flex flex-wrap gap-2 mb-3">
              {fs.corporate_actions.slice(0, 3).map((ca, i) => (
                <span
                  key={i}
                  className="text-[11px] px-2 py-1 rounded-full bg-surface border border-border text-tx capitalize"
                  title={ca.title ?? undefined}
                >
                  {ca.type}{ca.date ? ` · ${ca.date}` : ''}
                </span>
              ))}
              {fs.rating_action && (
                <span
                  className={`text-[11px] px-2 py-1 rounded-full border capitalize ${
                    fs.rating_action.action === 'upgrade'
                      ? 'text-buy border-buy/40 bg-buy/10'
                      : fs.rating_action.action === 'downgrade'
                      ? 'text-sell border-sell/40 bg-sell/10'
                      : 'text-hold border-hold/40 bg-hold/10'
                  }`}
                  title={fs.rating_action.title ?? undefined}
                >
                  {fs.rating_action.agency} {fs.rating_action.action}
                  {fs.rating_action.from_rating && fs.rating_action.to_rating
                    ? ` (${fs.rating_action.from_rating} → ${fs.rating_action.to_rating})`
                    : ''}
                  {fs.rating_action.date ? ` · ${fs.rating_action.date}` : ''}
                </span>
              )}
              {fs.next_results_date && (
                <span className="text-[11px] px-2 py-1 rounded-full bg-surface border border-border text-tx">
                  Next results: {fs.next_results_date}
                </span>
              )}
            </div>
          )}
          <div className="divide-y divide-border">
            {filings.slice(0, 5).map((f, i) => {
              const meta = [f.category, f.date].filter(Boolean).join(' · ');
              const titleRow = (
                <>
                  <span className="text-sm text-tx group-hover:text-accent transition-colors leading-snug">
                    {f.title ?? 'Untitled filing'}
                  </span>
                  {(meta || f.attachment) && (
                    <span className="text-[11px] text-muted">{meta}{f.attachment ? ' ↗' : ''}</span>
                  )}
                </>
              );
              return (
                <div key={i} className="py-3 first:pt-0 last:pb-0">
                  {f.attachment ? (
                    <a href={f.attachment} target="_blank" rel="noopener noreferrer" className="flex flex-col gap-1 group">
                      {titleRow}
                    </a>
                  ) : (
                    <div className="flex flex-col gap-1">{titleRow}</div>
                  )}
                  {f.desc && (
                    <details className="mt-1.5 group/desc">
                      <summary className="text-[11px] text-accent cursor-pointer select-none list-none
                                           [&::-webkit-details-marker]:hidden">
                        <span className="group-open/desc:hidden">Show details</span>
                        <span className="hidden group-open/desc:inline">Hide details</span>
                      </summary>
                      <p className="text-xs text-muted leading-relaxed mt-1.5">{f.desc}</p>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* ── 7. About ── */}
      {r?.about && (
        <Card title="About">
          <p className="text-sm text-muted leading-relaxed">{r.about}</p>
        </Card>
      )}

      {/* ── 8. Disclaimer ── */}
      <p className="text-[11px] text-muted/60 leading-relaxed px-1">
        This {rec} verdict is generated by an AI model from public data and is for informational
        purposes only — it is not investment advice. Prices and fundamentals may be delayed or
        incomplete. Do your own research and consult a registered financial advisor before trading.
      </p>
    </div>
  );
}
