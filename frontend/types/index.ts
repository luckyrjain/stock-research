export interface ValidationResult {
  found: boolean;
  valid: boolean;
  symbol: string;
  company: string;
  suspended?: boolean;
  suggestions: { symbol: string; company: string }[];
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

export interface Report {
  symbol: string;
  generated_at: string;
  analysis: Partial<Analysis>;
  stock_info: Partial<StockInfo>;
  research: { ratios?: Record<string, string>; about?: string };
  news: { title: string; source: string; published_at: string; url: string }[];
  holdings: {
    shareholding_pattern?: Record<string, number>;
    mutual_funds?: { fund: string; holding_pct: number }[];
  };
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
