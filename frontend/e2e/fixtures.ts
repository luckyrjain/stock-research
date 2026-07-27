// Shared fixture builders for the E2E suite. Every backend response used
// here is a hand-built fixture matching the shapes in frontend/types/index.ts
// — this suite never talks to a real FastAPI backend (see playwright.config.ts's
// own comment on why).

export function sseAnalysisBody(symbol: string, opts: { degraded?: boolean } = {}): string {
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
        data_freshness: {
          stock_info: new Date().toISOString(),
          research: new Date().toISOString(),
          news: new Date().toISOString(),
          shareholding: new Date().toISOString(),
          mf_holdings: new Date().toISOString(),
          filings: new Date().toISOString(),
        },
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
        degraded: opts.degraded ?? false,
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

export function marketPick(symbol: string, overrides: Record<string, unknown> = {}) {
  return {
    rank: 1,
    symbol,
    company: `${symbol} Limited`,
    exchange: 'NSE',
    mention_count: 3,
    sources: [{ name: 'Moneycontrol', type: 'news' }],
    confidence_score: 78,
    signal_score: 0.42,
    signal_verdict: 'BUY',
    recommendation: 'BUY',
    trend: 'new',
    trend_delta: null,
    current_price: 1234.5,
    change_pct: 1.8,
    pe_ratio: 22.5,
    market_cap_cr: 50000,
    summary: 'Fixture pick summary for E2E testing.',
    bull_factors: ['Strong earnings growth'],
    bear_factors: ['Valuation is stretched'],
    entry_price: 1200,
    target_price: 1400,
    stop_loss: 1100,
    action_score: 0.7,
    upside_pct: 13.4,
    ranking_reasons: ['High signal score'],
    is_recent_ipo: false,
    sector: 'Technology',
    ...overrides,
  };
}

export function marketPicksSseBody(picks: ReturnType<typeof marketPick>[]): string {
  const symbols = picks.map(p => p.symbol);
  const events = [
    { event: 'picks_start', sources: [{ name: 'Moneycontrol', type: 'news' }] },
    { event: 'source_done', source: 'Moneycontrol', articles: 5, status: 'ok' },
    { event: 'extracting', total_articles: 5, total_batches: 1 },
    { event: 'extract_progress', batch: 1, total_batches: 1, found_so_far: symbols.length },
    { event: 'consolidating', total_raw: symbols.length, unique: symbols.length },
    ...symbols.map(symbol => ({ event: 'validate_progress', symbol, ok: true })),
    { event: 'researching', stocks: symbols, total: symbols.length },
    ...symbols.map(symbol => ({ event: 'stock_researched', symbol, ok: true })),
    { event: 'scoring' },
    { event: 'done', picks, generated_at: new Date().toISOString(), total_picks: picks.length, from_cache: false },
  ];
  return events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('');
}
