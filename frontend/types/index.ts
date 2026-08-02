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

// Same two conditions watchlist_alerts.py's daily digest email already
// computes (see verdict_history.detect_recent_changes) — surfaced here so a
// notable move/verdict-flip doesn't only ever reach a user through email.
export interface RecommendationChangeAlert {
  old_recommendation: string | null;
  new_recommendation: string;
  confidence:          string | null;
}
export interface PriceMoveAlert {
  old_price:  number;
  new_price:  number;
  change_pct: number;
}

// GET /api/watchlist/calendar?symbols=... — one entry per watched symbol that
// has *something* to show: a next results date / pending corporate action
// (read straight off each symbol's already-cached filings), and/or a same-day
// recommendation change or price move (from verdict_history). Each of the
// four fields is independently optional/null — a symbol with no cached
// filings and no notable change contributes no entry at all, rather than a
// mostly-empty one.
export interface WatchlistCalendarEntry extends FilingsSummary {
  symbol: string;
  recommendation_change: RecommendationChangeAlert | null;
  price_move:             PriceMoveAlert | null;
}

export interface WatchlistCalendarResponse {
  entries: WatchlistCalendarEntry[];
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

// Per-task fetch timestamp (ISO, or null if that task never ran / errored),
// captured before main._strip_meta() discards each task's own _meta —
// distinct from Report.generated_at, which is stamped fresh on every
// report-assembly call regardless of whether any underlying data was
// actually refetched. A 7-day-stale shareholding table (168h TTL) would
// otherwise still read as "Updated today" with generated_at alone.
export interface DataFreshness {
  stock_info: string | null;
  research: string | null;
  news: string | null;
  shareholding: string | null;
  mf_holdings: string | null;
  filings: string | null;
}

export interface Report {
  symbol: string;
  generated_at: string;
  data_freshness: DataFreshness;
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
  mf_holdings_trend: MfHoldingsStakeDelta[];
  // True when every configured LLM provider failed (or returned
  // unparseable output past its guardrail retry) and `analysis` is
  // crew.py's generic safe-fallback HOLD, not a real analyst call — see
  // crew.py::_safe_analysis_fallback. Previously invisible to the
  // frontend: main._strip_meta() dropped the underscore-prefixed
  // `_degraded` marker before the report ever left the backend, so a
  // provider outage looked identical to a real HOLD verdict.
  degraded: boolean;
}

// One entry per mutual fund currently holding this stock, ranked by stake —
// see mf_holdings_history.py. delta_pct is the change vs. the same fund's
// stake in the previous stored quarterly snapshot; null (never guessed) when
// there's no prior snapshot at all, or this fund is a new entrant not
// present in it. Empty array when DATABASE_URL isn't configured or no
// snapshot has ever been stored for this symbol — not an error.
export interface MfHoldingsStakeDelta {
  fund:              string;
  holding_pct:       number;
  delta_pct:         number | null;
  as_of_date:        string;
  prior_as_of_date:  string | null;
}

// Standalone daily-close series for sparklines — fetched separately from the
// six-task pipeline above, so it's not part of TaskName/SSEMessage.
// Stock's return over the same window a PriceHistory series covers, benchmarked
// against the Nifty50 — only present when that series was fetched with
// ?benchmark=true. null (never guessed) when there's under 2 closes to
// compare, or the Nifty fetch itself failed.
export interface PriceHistoryBenchmark {
  stock_change_pct: number;
  nifty_change_pct: number;
  alpha_pct:        number;
}

export interface PriceHistory {
  symbol:     string;
  exchange:   string | null;
  dates:      string[];   // 'YYYY-MM-DD', oldest first
  closes:     number[];   // aligned with dates
  benchmark?: PriceHistoryBenchmark | null;
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

export type TaskName = 'stock_info' | 'research' | 'news' | 'shareholding' | 'mf_holdings' | 'filings';
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

// ── Market Picks track record (daily snapshots aggregated) ───────────────────

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

// One pick as stored in a single day's snapshot (the `market_picks_history`
// namespace in the backend's app_state table) — a narrower field set than the
// live MarketPick (see market_picks_pipeline.py's _save_history), since only
// what's needed for trend tracking is persisted.
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

// GET /api/financials/{symbol} — multi-year Profit & Loss / Balance Sheet /
// Cash Flow tables scraped from Screener.in (see
// tools/screener_tools.py::get_financial_statements), the biggest data gap
// the frontend design review found versus Screener.in itself. Row set is
// whatever Screener renders for that company (not a fixed schema, a bank's
// balance sheet looks nothing like an FMCG company's) — `rows` is a plain
// list of {label, values}, values aligned 1:1 with `years`. A `null` entry
// in `values` is a genuine gap in that company's history for that year/row
// (e.g. before IPO), not a parse failure.
export interface FinancialStatementRow {
  label:  string;
  values: (number | null)[];
}

export interface FinancialStatement {
  years: string[];
  rows:  FinancialStatementRow[];
}

// A deterministic two-stage DCF off the cash-flow statement's Operating
// Activity row (see dcf_valuation.py) — never LLM-generated. null whenever
// the underlying preconditions aren't met (thin cash-flow history, a
// non-positive latest OCF, or missing price/market-cap to derive a share
// count) — see dcf_valuation.py's own docstring for the full list of
// disclosed simplifications (OCF used as an FCF proxy, a fixed discount
// rate, clamped historical growth).
export interface DcfEstimate {
  fair_value_per_share: number;
  current_price:        number;
  upside_pct:            number;
  verdict:                'Undervalued' | 'Overvalued' | 'Fair';
  growth_rate_used:      number;   // %, clamped historical OCF CAGR used for the projection
  discount_rate:          number;   // %
  terminal_growth:        number;   // %
  latest_ocf_cr:          number;   // ₹ Cr, the OCF the projection started from
}

// Screener's own quarterly-earnings-call links (see
// tools/screener_tools.py::_extract_concalls) — the primary-source
// management commentary this app otherwise never surfaced, only third-party
// news coverage and Screener's own numeric ratios. Every *_url field is
// independently optional — an entry with only a Transcript link (no PPT,
// Notes, or recording) is common, not a parse failure.
export interface Concall {
  date:            string;   // e.g. "Jul 2026"
  transcript_url?: string;
  ppt_url?:        string;
  notes_url?:      string;
  audio_url?:      string;
}

export interface FinancialStatementsResponse {
  symbol:         string;
  profit_loss:    FinancialStatement | null;
  balance_sheet:  FinancialStatement | null;
  cash_flow:      FinancialStatement | null;
  dcf:            DcfEstimate | null;
  concalls:       Concall[];   // [] (never absent) when Screener has none on record
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
  // True when the corresponding source's fetch genuinely failed (an NSE
  // outage, a scraper break) — distinct from an empty array, which just
  // means no activity today (the expected common case). A card renders
  // "temporarily unavailable" for the former and nothing at all for the
  // latter, since a blank card and a broken scraper previously looked
  // identical to the user.
  insider_trades_unavailable:    boolean;
  bulk_block_deals_unavailable:  boolean;
}

// One individually-named shareholder — a promoter, or a member of an
// institutional/other category (see ShareholdingCategory below).
export interface NamedShareholder {
  name:         string;
  holding_pct:  number;
}

// A group of named shareholders under one NSE XBRL filing category (e.g.
// "Mutual Funds", "Foreign Portfolio Investors") — `category` is NSE's own
// raw filing category, word-spaced for display, not a fixed enum this app
// maintains (see tools/nse_tools.py::get_shareholding_detail's own
// disclosed limitation on why the category set isn't hardcoded).
export interface ShareholdingCategory {
  category:  string;
  holders:   NamedShareholder[];
}

// GET /api/shareholding-detail/{symbol} — every individually-named
// shareholder in the company's most recent NSE shareholding XBRL filing:
// named promoters with their own holding %, plus every other named-
// shareholder category the filing contains. More granular than
// `holdings.shareholding_pattern`'s aggregate category percentages or
// `holdings.mutual_funds` (mutual funds only). `unavailable: true`
// distinguishes a genuine scrape failure from a legitimately-thin filing —
// same convention as InsiderActivity's own *_unavailable flags above.
export interface ShareholdingDetail {
  symbol:                   string;
  as_of_date:               string | null;
  promoters:                NamedShareholder[];
  shareholder_categories:   ShareholdingCategory[];
  unavailable:              boolean;
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

// Real numeric analyst consensus scraped directly from Trendlyne's own
// company page (see tools/trendlyne_scraper.py) — additive to
// StreetConsensusArticle above, not a replacement for it. Every field is
// independently null (never guessed) when Trendlyne's page can't be
// resolved or doesn't cleanly present that value.
export interface TrendlyneNumericConsensus {
  symbol:              string;
  analyst_count:       number | null;
  consensus_rating:    string | null;
  mean_target_price:   number | null;
  target_upside_pct:   number | null;
  source_url:          string | null;
}

export interface StreetConsensus {
  symbol:              string;
  articles:            StreetConsensusArticle[];
  numeric_consensus:   TrendlyneNumericConsensus | null;
  // Same "real failure vs. legitimately empty" distinction as
  // InsiderActivity's own *_unavailable flags above.
  articles_unavailable:            boolean;
  numeric_consensus_unavailable:   boolean;
}

// ── Portfolio Aggregator ─────────────────────────────────────────────────────
// A separate personal net-worth tracker (GET/POST /api/portfolio/profiles,
// /accounts, /assets, /networth) — unrelated to Position/the /portfolio
// page above, which aggregates P&L over market-picks positions. No auth;
// see routes/portfolio_aggregator.py's own docstring for why.

export interface PortfolioProfile {
  id:    number;
  name:  string;
}

export type PortfolioAccountType = 'bank' | 'broker' | 'amc' | 'epfo' | 'other';

export interface PortfolioAccount {
  id:            number;
  profile_id:    number;
  name:          string;
  institution:   string | null;
  type:          PortfolioAccountType;
}

export type PortfolioAssetType = 'mf' | 'stock' | 'fd' | 'epf' | 'ppf' | 'cash' | 'manual' | 'loan';

export interface PortfolioAsset {
  id:          number;
  account_id:  number;
  type:        PortfolioAssetType;
  name:        string;
  symbol:      string | null;
  meta:        Record<string, unknown>;
  archived:    boolean;
  // Only present for type mf/stock.
  units:       number | null;
  avg_cost:    number | null;
  // Latest stored valuation, if any (an asset with no valuation yet — not
  // possible via the create-asset flow, but reachable after a
  // not-yet-built import path — shows both as null rather than 0).
  value:       number | null;
  valued_on:   string | null;
}

export interface PortfolioNetWorth {
  total:        number;
  by_type:      Record<string, number>;
  by_account:   Array<{ account_id: number; account_name: string; value: number }>;
}

// CAS PDF import (POST /api/portfolio/import-cas) — CAMS/KFintech detailed
// statement only. Reconciles by AMFI scheme code, then ISIN.
export interface CasImportResult {
  schemes:         number;
  assets_created:  number;
  assets_matched:  number;
  transactions:    number;
  skipped_rows:    number;
  warnings:        string[];
}

// Broker CSV/XLSX import — preview suggests a column mapping (Zerodha's
// tradebook is auto-detected), the user confirms it, then import runs.
export interface CsvPreviewResult {
  headers:            string[];
  sample_rows:         string[][];
  suggested_mapping:  Record<string, string | null>;
  detected:           'zerodha' | null;
}

export interface CsvImportResult {
  rows:            number;
  imported:        number;
  duplicates:      number;
  skipped:         number;
  assets_created:  number;
  assets_matched:  number;
  warnings:        string[];
}
