// Shared fixture builders for the E2E suite. Every backend response used
// here is a hand-built fixture matching the shapes in frontend/types/index.ts
// — this suite never talks to a real FastAPI backend (see playwright.config.ts's
// own comment on why).

export function sseAnalysisBody(symbol: string): string {
  const events = [
    { event: 'start', stale: ['stock_info', 'research', 'news', 'shareholding', 'mf_holdings'], cached: [] },
    { event: 'task_done', task: 'stock_info', ok: true },
    { event: 'task_done', task: 'research', ok: true },
    { event: 'task_done', task: 'news', ok: true },
    { event: 'task_done', task: 'shareholding', ok: true },
    { event: 'task_done', task: 'mf_holdings', ok: true },
    { event: 'analysing' },
    {
      event: 'done',
      report: {
        symbol,
        generated_at: new Date().toISOString(),
        analysis: {
          bull_factors: ['Strong earnings growth', 'Market leadership'],
          bear_factors: ['Valuation is stretched'],
          key_risks: ['Sector-wide slowdown'],
          symbol,
          recommendation: 'BUY',
          confidence: 'HIGH',
          summary: 'Fixture summary for E2E testing.',
          valuation: { verdict: 'Fairly valued', comment: 'Trading in line with historical average.' },
          business_quality: 'Strong moat and consistent execution.',
          news_sentiment: 'Positive',
          news_highlights: 'Recent coverage has been favorable.',
          institutional_trend: 'FII holding steady.',
        },
        signals: {
          final_score: 0.6,
          verdict: 'BUY',
          signals: {},
        },
        stock_info: {
          symbol,
          exchange: 'NSE',
          primary_exchange: 'NSE',
          company_name: `${symbol} Limited`,
          current_price: 1234.5,
          change_pct: 1.2,
          volume: 100000,
          market_cap_cr: 50000,
          pe_ratio: 22.5,
          sector: 'Technology',
        },
        research: { ratios: { 'P/E': '22.5', ROE: '18%' }, about: 'Fixture company description.' },
        news: [],
        holdings: {},
        filings: [],
      },
    },
  ];
  return events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('');
}

export const SSE_HEADERS = {
  'content-type': 'text/event-stream',
  'cache-control': 'no-cache',
};

export function validationResult(symbol: string) {
  return {
    found: true,
    valid: true,
    symbol,
    company: `${symbol} Limited`,
    exchange: 'NSE',
    suggestions: [],
  };
}
