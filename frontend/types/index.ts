export interface ValidationResult {
  found: boolean;
  valid: boolean;
  symbol: string;
  company: string;
  exchange?: string;
  suspended?: boolean;
  suggestions: { symbol: string; company: string; exchange?: string }[];
}

export interface StockInfo {
  symbol: string;
  exchange: string;
  primary_exchange?: string;
  company_name: string;
  current_price: number | null;
  previous_close?: number | null;
  change_pct: number;
  volume?: number | null;
  avg_volume_10d?: number | null;
  market_cap_cr: number | null;
  pe_ratio: number | null;
  eps: number | null;
  book_value: number | null;
  price_to_book?: number | null;
  '52w_high': number | null;
  '52w_low': number | null;
  dividend_yield_pct?: number | null;
  beta?: number | null;
  sector: string | null;
  industry: string | null;
  about?: string | null;
  prices_by_exchange?: Record<string, StockInfo>;
}

export interface Analysis {
  // Analyst output can be either plain strings or structured factor objects.
  // The UI normalizes both shapes for rendering.
  bull_factors: Array<string | Record<string, string | number | null>>;
  bear_factors: Array<string | Record<string, string | number | null>>;
  key_risks: string[];
  symbol: string;
  recommendation: 'BUY' | 'SELL' | 'HOLD';
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  summary: string;
  valuation: { verdict: string; comment: string };
  business_quality: string;
  news_sentiment: 'Positive' | 'Neutral' | 'Negative';
  news_highlights: string | string[];
  institutional_trend: string;
}

export interface SignalItem {
  name: string;
  value: string;
  score: number;
  meta: Record<string, number | string | null>;
}

export interface SignalSummary {
  final_score: number;
  verdict: 'BUY' | 'SELL' | 'HOLD' | string;
  signals: Record<string, SignalItem>;
}

export interface Report {
  symbol: string;
  generated_at: string;
  analysis: Partial<Analysis>;
  signals?: Partial<SignalSummary>;
  stock_info: Partial<StockInfo>;
  research: { ratios?: Record<string, string>; about?: string };
  news: { title: string; source: string; published_at: string; url: string }[];
  holdings: {
    shareholding_pattern?: Record<string, number>;
    mutual_funds?: { fund: string; holding_pct: number }[];
  };
}

// Standalone daily-close series for sparklines — fetched separately from the
// six-task pipeline above, so it's not part of TaskName/SSEMessage.
export interface PriceHistory {
  symbol:   string;
  exchange: string | null;
  dates:    string[];   // 'YYYY-MM-DD', oldest first
  closes:   number[];   // aligned with dates
}

export type TaskName = 'stock_info' | 'research' | 'news' | 'shareholding' | 'mf_holdings';
export type TaskStatus = 'idle' | 'running' | 'ok' | 'fail' | 'cached';
export type Phase = 'idle' | 'fetching' | 'analysing' | 'done' | 'error';

export type SSEMessage =
  | { event: 'start';     stale: string[]; cached: string[] }
  | { event: 'task_done'; task: string; ok: boolean; error?: string }
  | { event: 'analysing' }
  | { event: 'done';      report: Report }
  | { event: 'error';     message: string };

// ── Market Picks ─────────────────────────────────────────────────────────────

export interface PickSource {
  name: string;
  type: 'news' | 'brokerage' | 'platform';
  url?: string;
  headline?: string;
  direction?: 'BUY' | 'SELL' | 'NEUTRAL';
}

export interface MarketPick {
  rank: number;
  symbol: string;
  company: string;
  exchange: string;
  mention_count: number;
  sources: PickSource[];
  confidence_score: number;   // 0–100
  signal_score: number;       // –1 to 1
  signal_verdict: string;
  recommendation: 'BUY' | 'WATCHLIST' | 'HOLD' | 'SELL';
  trend: 'rising' | 'falling' | 'stable' | 'new';
  trend_delta: number | null;
  current_price: number | null;
  change_pct: number;
  pe_ratio: number | null;
  market_cap_cr: number | null;
  summary: string;
  bull_factors: string[];
  bear_factors: string[];
  entry_price: number | null;
  target_price: number | null;
  stop_loss: number | null;
  action_score: number;        // 0–1 directional conviction
  upside_pct: number | null;   // (target - price) / price * 100
  ranking_reasons: string[];   // why this stock ranked here
  is_recent_ipo: boolean;      // listed < 8 months ago (IPO momentum flag)
  horizon?: 'short' | 'medium' | 'long';  // investment horizon from LLM analysis
}

export type MarketPicksPhase =
  | 'idle' | 'scanning' | 'extracting' | 'consolidating' | 'researching' | 'scoring' | 'done' | 'error';

// ── Market Picks track record (output/_history/ aggregated) ──────────────────

export interface MarketPickTrackRecord {
  symbol:              string;
  first_seen:          string;    // 'YYYY-MM-DD'
  last_seen:           string;    // 'YYYY-MM-DD'
  times_picked:        number;
  recommendation_then: string | null;
  recommendation_now:  string | null;
  price_then:          number | null;
  price_now:           number | null;
  change_pct:          number | null;   // null if either price is missing (legacy snapshot)
  confidence_then:     number | null;
  confidence_now:      number | null;
}

export interface MarketPicksHistoryResponse {
  symbols:        MarketPickTrackRecord[];
  snapshot_count: number;
}

// ── SME EMA Signals ──────────────────────────────────────────────────────────

export interface SmeSignal {
  symbol:          string;
  name:            string | null;
  exchange:        string;
  trade_date:      string;           // 'YYYY-MM-DD'
  close_price:     number | null;
  ema20:           number | null;
  ema50:           number | null;
  cross:           'golden' | 'death';
  in_golden_cross: boolean;
}

export interface SmeSignalsResponse {
  signals:          SmeSignal[];
  total_monitored:  number;
  golden_now:       number;          // stocks currently in golden-cross regime
  last_run:         string | null;   // ISO timestamp or null
  refreshing:       boolean;         // a pipeline refresh is running server-side
}

export interface SmeSignalHistoryRow {
  trade_date:  string;               // 'YYYY-MM-DD'
  close_price: number | null;
  ema20:       number | null;
  ema50:       number | null;
  cross:       'golden' | 'death' | null;
}

export interface SmeSignalHistoryResponse {
  symbol:   string;
  name:     string | null;
  exchange: string | null;
  series:   SmeSignalHistoryRow[];   // up to ~63 trading days, oldest first
}

export type MarketPicksSSEMessage =
  | { event: 'picks_start';       sources: { name: string; type: string }[] }
  | { event: 'source_done';       source: string; articles: number; status: 'ok' | 'empty' }
  | { event: 'extracting';        total_articles: number; total_batches: number }
  | { event: 'extract_progress';  batch: number; total_batches: number; found_so_far: number }
  | { event: 'consolidating';     total_raw: number; unique: number }
  | { event: 'validate_progress'; symbol: string; ok: boolean }
  | { event: 'researching';       stocks: string[]; total: number }
  | { event: 'stock_researched';  symbol: string; ok: boolean }
  | { event: 'scoring' }
  | { event: 'analysis_error'; symbols: string[]; reason: string }
  | { event: 'done'; picks: MarketPick[]; generated_at: string; total_picks: number; from_cache?: boolean }
  | { event: 'error'; message: string };

// Pure aggregation of what stock analysis, market picks, and SME signals have
// each already cached for a symbol — no new fetching. Any section is null
// when that pipeline hasn't run for this symbol yet (or its cache has gone
// stale), which is the common case, not an error.
export interface ConsolidatedAnalysis {
  recommendation: string | null;
  confidence:     string | null;
  summary:        string | null;
  as_of:          string | null;   // ISO timestamp the analysis was cached at
}

export interface ConsolidatedMarketPick {
  rank:             number | null;
  recommendation:   'BUY' | 'WATCHLIST' | 'HOLD' | 'SELL' | null;
  confidence_score: number | null;
  summary:          string | null;
  generated_at:     string;        // when the market-picks run that included this stock ran
}

export interface ConsolidatedSme {
  trade_date:      string;
  cross:           'golden' | 'death' | null;
  in_golden_cross: boolean;
  name:            string | null;
  exchange:        string | null;
}

export interface ConsolidatedView {
  symbol:      string;
  analysis:    ConsolidatedAnalysis | null;
  market_pick: ConsolidatedMarketPick | null;
  sme:         ConsolidatedSme | null;
}
