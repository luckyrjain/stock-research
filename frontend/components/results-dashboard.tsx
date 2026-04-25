import type { Report, StockInfo } from '@/types';

interface Props { report: Report }

type FactorValue = string | number | null | undefined;
type FactorShape = string | Record<string, FactorValue>;

function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return '—';
  return n.toLocaleString('en-IN', { maximumFractionDigits: decimals });
}

function fmtCr(n: number | null | undefined) {
  if (n == null) return '—';
  if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(2)}L Cr`;
  if (n >= 1_000)    return `₹${(n / 1_000).toFixed(2)}K Cr`;
  return `₹${fmt(n)} Cr`;
}

const REC_CONFIG = {
  BUY:  { bg: 'bg-buy/10',  border: 'border-buy/30',  text: 'text-buy',  badge: 'bg-buy  text-white' },
  SELL: { bg: 'bg-sell/10', border: 'border-sell/30', text: 'text-sell', badge: 'bg-sell text-white' },
  HOLD: { bg: 'bg-hold/10', border: 'border-hold/30', text: 'text-hold', badge: 'bg-hold text-white' },
};

const CONF_COLOR: Record<string, string> = {
  HIGH: 'text-buy', MEDIUM: 'text-hold', LOW: 'text-sell',
};

const SENT_COLOR: Record<string, string> = {
  Positive: 'text-buy', Neutral: 'text-muted', Negative: 'text-sell',
};

function Card({ title, children, className = '' }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-card border border-border rounded-xl p-5 ${className}`}>
      <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-3">{title}</p>
      {children}
    </div>
  );
}

function MetricRow({ label, value, colorClass = 'text-tx' }: { label: string; value: string; colorClass?: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className={`text-sm font-semibold font-mono ${colorClass}`}>{value}</span>
    </div>
  );
}

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

function ExchangeQuoteCard({
  exchange,
  price,
  changePct,
  active = false,
}: {
  exchange: string;
  price: number | null | undefined;
  changePct: number | null | undefined;
  active?: boolean;
}) {
  const change = changePct ?? 0;
  const changeCls = change > 0 ? 'text-buy' : change < 0 ? 'text-sell' : 'text-muted';
  const changeStr = `${change > 0 ? '+' : ''}${fmt(change)}%`;

  return (
    <div className={`min-w-[132px] rounded-xl border p-3 ${active ? 'border-accent/40 bg-accent/5' : 'border-border bg-card'}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-mono font-semibold text-muted">{exchange}</span>
        {active && (
          <span className="text-[10px] font-semibold uppercase tracking-[1px] text-accent">Primary</span>
        )}
      </div>
      <p className="mt-2 text-xl font-bold font-mono text-tx">
        {price != null ? `₹${fmt(price)}` : '—'}
      </p>
      <p className={`text-sm font-mono ${changeCls}`}>{changeStr}</p>
    </div>
  );
}

export default function ResultsDashboard({ report }: Props) {
  const { analysis: a, stock_info: s, research: r, news, holdings: h } = report;

  const rec = (a?.recommendation ?? 'HOLD') as 'BUY' | 'SELL' | 'HOLD';
  const cfg = REC_CONFIG[rec];
  const exchangeQuotes: Array<[string, Partial<StockInfo>]> =
    s?.prices_by_exchange && Object.keys(s.prices_by_exchange).length > 0
      ? Object.entries(s.prices_by_exchange) as Array<[string, NonNullable<typeof s.prices_by_exchange>[string]]>
      : (s ? [[s.exchange ?? 'NSE', s]] : []);
  const primaryExchange = s?.primary_exchange ?? s?.exchange ?? 'NSE';

  return (
    <div className="animate-fade-up space-y-6">

      {/* Header — company + price */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-tx">{s?.company_name ?? report.symbol}</h2>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
              {exchangeQuotes.length > 1 ? 'NSE + BSE' : (s?.exchange ?? 'NSE')}
            </span>
            {s?.sector && (
              <span className="text-xs text-muted">{s.sector}</span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-3">
          {exchangeQuotes.map(([exchange, quote]) => (
            <ExchangeQuoteCard
              key={exchange}
              exchange={exchange}
              price={quote?.current_price}
              changePct={quote?.change_pct}
              active={exchange === primaryExchange}
            />
          ))}
        </div>
      </div>

      {/* Recommendation card */}
      <div className={`rounded-xl border p-5 ${cfg.bg} ${cfg.border}`}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-2">AI Recommendation</p>
            <p className="text-sm text-tx leading-relaxed">{a?.summary ?? 'Analysis pending.'}</p>
          </div>
          <div className="flex flex-col items-end gap-2 shrink-0">
            <span className={`text-2xl font-black px-5 py-2 rounded-lg ${cfg.badge}`}>{rec}</span>
            {a?.confidence && (
              <span className={`text-xs font-semibold ${CONF_COLOR[a.confidence]}`}>
                {a.confidence} confidence
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Market metrics + Valuation */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card title="Market Metrics">
          {exchangeQuotes.length > 1 && (
            <MetricRow label="Primary Exchange" value={primaryExchange} />
          )}
          <MetricRow label="Market Cap"   value={fmtCr(s?.market_cap_cr)} />
          <MetricRow label="P/E Ratio"    value={s?.pe_ratio != null ? fmt(s.pe_ratio) : '—'} />
          <MetricRow label="EPS"          value={s?.eps != null ? `₹${fmt(s.eps)}` : '—'} />
          <MetricRow label="Book Value"   value={s?.book_value != null ? `₹${fmt(s.book_value)}` : '—'} />
          <MetricRow label="52W High"     value={s?.['52w_high'] != null ? `₹${fmt(s['52w_high'])}` : '—'} />
          <MetricRow label="52W Low"      value={s?.['52w_low']  != null ? `₹${fmt(s['52w_low'])}` : '—'} />
        </Card>

        <div className="flex flex-col gap-4">
          <Card title="Valuation">
            <p className={`text-sm font-semibold mb-1 ${
              a?.valuation?.verdict === 'Undervalued' ? 'text-buy' :
              a?.valuation?.verdict === 'Overvalued'  ? 'text-sell' : 'text-hold'
            }`}>{a?.valuation?.verdict ?? '—'}</p>
            <p className="text-sm text-muted leading-relaxed">{a?.valuation?.comment ?? '—'}</p>
          </Card>
          <Card title="Business Quality">
            <p className="text-sm text-tx leading-relaxed">{a?.business_quality ?? '—'}</p>
          </Card>
        </div>
      </div>

      {/* Fundamentals ratios */}
      {r?.ratios && Object.keys(r.ratios).length > 0 && (
        <Card title="Key Ratios">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-6">
            {Object.entries(r.ratios).map(([k, v]) => (
              <MetricRow key={k} label={k} value={String(v)} />
            ))}
          </div>
        </Card>
      )}

      {/* Bull / Bear */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card title="Bull Case">
          <ul className="space-y-2">
            {(a?.bull_factors ?? []).map((f, i) => (
              <li key={i} className="flex gap-2 text-sm text-tx">
                <span className="text-buy mt-0.5 shrink-0">▲</span>
                <span>{formatFactor(f)}</span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Bear Case">
          <ul className="space-y-2">
            {(a?.bear_factors ?? []).map((f, i) => (
              <li key={i} className="flex gap-2 text-sm text-tx">
                <span className="text-sell mt-0.5 shrink-0">▼</span>
                <span>{formatFactor(f)}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      {/* Key Risks */}
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

      {/* Institutional + Shareholding */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {h?.shareholding_pattern && Object.keys(h.shareholding_pattern).length > 0 && (
          <Card title="Shareholding Pattern">
            {Object.entries(h.shareholding_pattern).map(([k, v]) => (
              <div key={k} className="mb-2 last:mb-0">
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-muted">{k}</span>
                  <span className="font-mono font-semibold text-tx">{fmt(v, 1)}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-border overflow-hidden">
                  <div
                    className="h-full rounded-full bg-accent"
                    style={{ width: `${Math.min(v, 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </Card>
        )}

        <div className="flex flex-col gap-4">
          <Card title="Institutional Trend">
            <p className="text-sm text-tx leading-relaxed">{a?.institutional_trend ?? '—'}</p>
          </Card>
          {a?.news_sentiment && (
            <Card title="News Sentiment">
              <p className={`text-sm font-semibold mb-1 ${SENT_COLOR[a.news_sentiment]}`}>
                {a.news_sentiment}
              </p>
              <p className="text-sm text-muted leading-relaxed">{formatNewsHighlights(a?.news_highlights)}</p>
            </Card>
          )}
        </div>
      </div>

      {/* MF Holdings */}
      {h?.mutual_funds && h.mutual_funds.length > 0 && (
        <Card title="Mutual Fund Holdings">
          <div className="divide-y divide-border">
            {h.mutual_funds.slice(0, 8).map((mf, i) => (
              <div key={i} className="flex items-center justify-between py-2">
                <span className="text-sm text-tx">{mf.fund}</span>
                <span className="text-sm font-mono font-semibold text-accent">{fmt(mf.holding_pct, 2)}%</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* News headlines */}
      {news && news.length > 0 && (
        <Card title="Recent News">
          <div className="space-y-3">
            {news.slice(0, 5).map((n, i) => (
              <a
                key={i}
                href={n.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex flex-col gap-0.5 group"
              >
                <span className="text-sm text-tx group-hover:text-accent transition-colors leading-snug">
                  {n.title}
                </span>
                <span className="text-[11px] text-muted">
                  {n.source} · {n.published_at}
                </span>
              </a>
            ))}
          </div>
        </Card>
      )}

      {/* About */}
      {r?.about && (
        <Card title="About">
          <p className="text-sm text-muted leading-relaxed">{r.about}</p>
        </Card>
      )}
    </div>
  );
}
