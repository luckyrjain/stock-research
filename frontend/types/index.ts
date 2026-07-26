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

export interface Filing {
  title:      string | null;
  desc:       string | null;
  date:       string | null;
  category:   string | null;
  attachment: string | null;   // URL to the NSE PDF, when NSE provides one
}

// Best-effort text classification over the same `filings` list above — see
// signals/filings_classifier.py. Every field is independently optional
// (never guessed); a symbol with no matching filings in its fetch window
// gets an empty/null shape here, not an error.
export interface CorporateAction {
  type:  'dividend' | 'split' | 'bonus' | 'buyback';
  date:  string | null;
  title: string | null;
}
export interface RatingAction {
  agency:      string;
  action:      'upgrade' | 'downgrade' | 'reaffirmed';
  from_rating: string | null;   // only present when a clean "from X to Y" phrase was found
  to_rating:   string | null;
  date:        string | null;
  title:       string | null;
}
export interface FilingsSummary {
  corporate_actions:  CorporateAction[];
  rating_action:      RatingAction | null;
  next_results_date:  string | null;   // 'YYYY-MM-DD'
}

// Quarterly Sales/EPS/operating-margin mini-trend scraped from Screener's
// Quarterly Results table — same page fundamentals already fetches, so it's
// free. Oldest first, same convention as PriceHistory. revenue/eps are
// absent/empty (the whole object is omitted) when Screener doesn't expose a
// clean, fully-numeric window for both rows (e.g. a recent IPO).
// operating_margin is independently optional — several sectors (banks,
// NBFCs) routinely omit that row even when revenue/eps are present, so it's
// absent rather than guessed at, never backfilled from revenue/eps.
export interface QuarterlyTrend {
  quarters:          string[];         // e.g. "Mar 2024", oldest first
  revenue:           number[];         // aligned with quarters
  eps:               number[];         // aligned with quarters
  operating_margin?: number[];         // % — aligned with quarters, when present
}

export interface Report {
  symbol: string;
  generated_at: string;
  analysis: Partial<Analysis>;
  signals?: Partial<SignalSummary>;
  stock_info: Partial<StockInfo>;
  research: {
    ratios?: Record<string, string>;
    about?: string;
    quarterly_trend?: QuarterlyTrend;
    // Only present when Screener's own ratios table came back completely
    // empty and NSE's own XBRL results filings had a usable EPS — see
    // tools/nse_tools.py::get_nse_basic_ratios. Deliberately EPS-only, not
    // a full ratios set — see that function's own docstring for why
    // sales/profit are intentionally excluded (unresolvable XBRL unit
    // scale without a live response to verify against).
    nse_fallback_ratios?: { eps: number; source: string; as_of_date?: string | null };
  };
  news: { title: string; source: string; published_at: string; url: string }[];
  holdings: {
    shareholding_pattern?: Record<string, number>;
    mutual_funds?: { fund: string; holding_pct: number }[];
    pledge_pct?: number | null;   // promoter pledge %, from the same shareholding fetch
  };
  filings: Filing[];   // corporate announcements — also what signals.filings feeds on
  filings_summary: FilingsSummary;
}

// Standalone daily-close series for sparklines — fetched separately from the
// six-task pipeline above, so it's not part of TaskName/SSEMessage.
export interface PriceHistory {
  symbol:   string;
  exchange: string | null;
  dates:    string[];   // 'YYYY-MM-DD', oldest first
  closes:   number[];   // aligned with dates
}

// One row per (symbol, day) the analysis pipeline ran, powering the hero's
// "verdict timeline" strip — fetched separately from the main report, same
// pattern as PriceHistory.
export interface VerdictHistoryEntry {
  date:              string;   // 'YYYY-MM-DD'
  recommendation:    string | null;
  confidence:        string | null;
  current_price:     number | null;
  signal_score:      number | null;
  // Scored against today's live price. return_since_pct is an observed fact
  // (populated whenever both prices are known); outcome is only set for
  // BUY/SELL — a HOLD makes no directional claim, so it's never graded.
  return_since_pct:  number | null;
  outcome:           'win' | 'loss' | null;
}

export interface VerdictHistoryResponse {
  symbol:        string;
  history:       VerdictHistoryEntry[];   // oldest first
  win_rate:      number | null;           // % of scored (BUY/SELL) entries that were a win
  scored_count:  number;
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
  sector: string;               // from stock_info; "Unknown" when NSE/yfinance doesn't report one
  valuation_percentile: number | null;  // 0–100, where current P/E sits vs. this stock's own
                                         // 3–5y Screener-published P/E history; null when Screener
                                         // didn't have a parseable valuation band for this stock
}

export type MarketPicksPhase =
  | 'idle' | 'scanning' | 'extracting' | 'consolidating' | 'researching' | 'scoring' | 'done' | 'error';

// Cache metadata only (no pipeline run) — powers the idle hero's true
// last-run / next-scheduled-scan display on /market-picks.
export interface MarketPicksStatus {
  last_run_at:       string | null;   // ISO timestamp of the last completed run, if any
  cache_fresh:       boolean;         // whether that run is still within the cache TTL
  next_scheduled_at: string;          // ISO timestamp of the next cron-triggered refresh
}

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
  nifty_change_pct:    number | null;   // ^NSEI change over the same first_seen -> last_seen window
  alpha_pct:           number | null;   // change_pct - nifty_change_pct; null wherever either side is
}

// Per-recommendation-tier breakdown (keyed by recommendation_then, e.g. "BUY",
// "WATCHLIST") — only over symbols with a computed change_pct.
export interface MarketPicksTierStat {
  count:          number;
  avg_change_pct: number;
  win_rate:       number;   // 0-100, share of this tier's picks with change_pct > 0
}

export interface MarketPicksHistoryResponse {
  symbols:         MarketPickTrackRecord[];
  snapshot_count:  number;
  win_rate:        number | null;   // 0-100, share of ALL picks with change_pct > 0
  tier_stats:      Record<string, MarketPicksTierStat>;
  avg_alpha_pct:   number | null;
  available_dates: string[];        // 'YYYY-MM-DD', every date with a stored snapshot
}

// One pick as stored in a single day's output/_history/<date>.json snapshot —
// a narrower field set than the live MarketPick (see market_picks_pipeline.py's
// _save_history), since only what's needed for trend tracking is persisted.
export interface MarketPicksSnapshotPick {
  symbol:            string;
  confidence:        number;
  effective_signal:  number;
  mention_count:     number;
  current_price:     number | null;
  recommendation:    string | null;
}

// GET /api/market-picks/history?date=YYYY-MM-DD — that single day's full pick
// list, verbatim, instead of the cross-date aggregation above.
export interface MarketPicksDailySnapshot {
  date:  string;
  picks: MarketPicksSnapshotPick[];
}

// ── SME EMA Signals ──────────────────────────────────────────────────────────

export interface SmeSignal {
  symbol:            string;
  name:              string | null;
  exchange:          string;
  // Populated for BSE SME stocks (BSE's own list API reports it); always
  // null for NSE rows, which already have a directly analyzable ticker in
  // `symbol` and don't need it. Used to deep-link a BSE row to /?symbol=<isin>
  // since the raw BSE scrip code in `symbol` isn't analyzable on its own —
  // see the home page's deep-link handling.
  isin:              string | null;
  avg_volume_20d:    number | null;   // avg daily share volume, last 20 trading days
  avg_turnover_20d:  number | null;   // avg daily turnover in ₹, last 20 trading days
  market_cap_cr:     number | null;   // ₹ Cr, via yfinance fast_info
  trade_date:        string;           // 'YYYY-MM-DD'
  close_price:       number | null;
  ema20:             number | null;
  ema50:             number | null;
  rsi14:             number | null;   // Wilder's RSI(14), 0-100
  volume_spike:      boolean | null;  // day's volume > 2x its trailing 20d average
  // In the default "crosses" view every row is a real cross event (never
  // null); in "regime" view (?view=regime) this is the stock's latest row,
  // which usually isn't a cross day at all, so it's null there.
  cross:             'golden' | 'death' | null;
  in_golden_cross:   boolean;
}

// "golden crosses in the last 90d: X% follow-through" — of golden crosses in
// the lookback window that have had `forward_days` trading days to play out,
// the share that closed higher. win_rate is null when sample_size is 0
// (never guessed at); a cross too recent to have resolved yet is excluded
// from the sample rather than counted as a loss.
export interface SmeGoldenHitRate {
  sample_size:    number;
  win_rate:       number | null;   // 0-100
  lookback_days:  number;
  forward_days:   number;
}

export interface SmeSignalsResponse {
  signals:              SmeSignal[];
  total_monitored:      number;
  golden_now:           number;          // stocks currently in golden-cross regime
  last_run:             string | null;   // ISO timestamp or null
  refreshing:           boolean;         // a pipeline refresh is running server-side
  golden_hit_rate_90d:  SmeGoldenHitRate;
}

export interface SmeSignalHistoryRow {
  trade_date:  string;               // 'YYYY-MM-DD'
  close_price: number | null;
  ema20:       number | null;
  ema50:       number | null;
  cross:       'golden' | 'death' | null;
}

// A past golden/death cross with forward returns — how far price moved N
// trading days later. ret_Nd_pct is null if fewer than N trading days have
// elapsed since the cross within the stored window (sme_ema_pipeline._STORE_DAYS,
// ~3 months), never guessed at.
export interface SmeCrossEvent {
  trade_date:      string;           // 'YYYY-MM-DD'
  cross:           'golden' | 'death';
  close_at_cross:  number | null;
  ret_10d_pct:     number | null;
  ret_20d_pct:     number | null;
}

export interface SmeSignalHistoryResponse {
  symbol:        string;
  name:          string | null;
  exchange:      string | null;
  series:        SmeSignalHistoryRow[];   // up to ~63 trading days, oldest first
  cross_events:  SmeCrossEvent[];         // every cross in that window, most recent first
}

// Custom screener (see GET /api/screener, screener_pipeline.py) — a
// stored-metrics row for one NIFTY 500 stock. Every numeric field is null
// (never guessed) when yfinance/Screener didn't have it for this stock.
export interface ScreenerStock {
  symbol:          string;
  company_name:    string | null;
  exchange:        string | null;
  // From the NIFTY 500 constituent list itself (NSE's own published
  // classification) — the primary filter-chip dimension, in preference to
  // `sector` below (yfinance's own field, whose taxonomy for NSE/BSE
  // symbols is an explicitly disclosed unverified assumption elsewhere in
  // this codebase — see signals/engine.py).
  nse_industry:    string | null;
  sector:          string | null;
  current_price:   number | null;
  pe_ratio:        number | null;
  market_cap_cr:   number | null;
  avg_volume_10d:  number | null;
  rsi14:           number | null;
  ema_trend:       'bullish' | 'bearish' | null;
  fetched_at:      string | null;   // ISO timestamp
}

export interface ScreenerResponse {
  stocks:            ScreenerStock[];
  total:              number;         // count matching the current filters
  total_monitored:    number;         // total rows in screener_stocks, unfiltered
  industries:         string[];       // real, currently-populated nse_industry values
  last_run:           string | null;  // ISO timestamp or null
  refreshing:         boolean;        // a pipeline refresh is running server-side
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

// Peer comparison, scraped from Screener.in's own Peer comparison table. The
// column set (`values`' keys) varies by sector — whatever Screener renders for
// that company's peers is what's returned, not a fixed schema.
export interface PeerRow {
  name:   string;
  slug:   string;
  values: Record<string, string>;
}

// Where the stock's current P/E sits within its OWN last 3-5 years of
// Screener-published yearly P/E — an absolute anchor alongside the
// peer-relative `percentiles` above, never a sector benchmark this codebase
// doesn't have real data for. `null` from the API when Screener doesn't
// publish that history for this company (see api.py::_compute_valuation_anchor).
export interface ValuationAnchor {
  current_pe:  number;
  years:       string[];
  pe_history:  number[];
  low:         number;
  median:      number;
  high:        number;
  percentile:  number;  // 0–100, current_pe's rank within pe_history
}

export interface PeerComparison {
  symbol:           string;
  self:             PeerRow | null;
  peers:            PeerRow[];          // up to 5, ordered as Screener presents them
  sector_median:    PeerRow | null;
  percentiles:      Record<string, number>;  // 0–100, keyed by the same column labels as `values`
  absolute_anchor:  ValuationAnchor | null;
}

// Programmatic API access (GET/POST/DELETE /api/api-keys). `key` is present
// only in the POST response, shown to the caller exactly once — GET never
// returns it, only key_prefix, so the management UI can list keys without
// ever re-displaying the secret.
export interface ApiKey {
  id:            number;
  key_prefix:    string;
  label:         string | null;
  created_at:    string;
  last_used_at:  string | null;
  revoked_at:    string | null;
}

export interface CreatedApiKey extends ApiKey {
  key: string;
}

// GET /api/api-keys also returns tier + usage — this account's current
// standing against the same sliding-window limit GET /api/v1/* enforces
// (api.py::_TIER_LIMITS), a non-mutating peek via rate_limiter.get_usage_count.
export interface ApiUsage {
  calls:           number;
  limit:           number;
  window_seconds:  number;
}

export interface ApiKeysResponse {
  keys:   ApiKey[];
  tier:   'free' | 'pro';
  usage:  ApiUsage;
}

// Structured (not LLM-article) promoter/director insider trades and
// bulk/block deals for one symbol — the same NSE feeds Market Picks already
// scrapes for discovery, surfaced here directly instead of only ever
// showing up when a stock happens to make the weekly picks list. Both lists
// are empty (never null) when NSE has nothing for this symbol — the
// expected common case, not an error.
export interface InsiderTrade {
  person:     string;
  category:   string;
  action:     'BUY' | 'SELL';
  quantity:   number;
  value:      number;
  date:       string;
  date_iso:   string | null;
}

export interface BulkBlockDeal {
  client:     string;
  action:     'BUY' | 'SELL';
  quantity:   number;
  price:      number;
  deal_type:  'Bulk Deal' | 'Block Deal';
  date:       string;
  date_iso:   string | null;
}

export interface InsiderActivity {
  symbol:            string;
  insider_trades:    InsiderTrade[];
  bulk_block_deals:  BulkBlockDeal[];
}

// Trendlyne-cited analyst commentary for one stock — real article
// titles/links/dates, never a fabricated consensus rating or target price
// (see tools/trendlyne_agent.py::fetch_trendlyne_consensus_for_symbol —
// this module searches GNews for articles that cite Trendlyne, it doesn't
// scrape trendlyne.com's own numeric estimates). Empty articles (never
// null) is the expected common case for most stocks on most days.
export interface StreetConsensusArticle {
  title:         string;
  summary:       string;
  url:           string;
  published_at:  string | null;
}

export interface StreetConsensus {
  symbol:    string;
  articles:  StreetConsensusArticle[];
}
