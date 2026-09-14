# API Catalog

**Producer for all rows:** `backend/` (FastAPI). **Consumer for all rows:** `frontend/app/api/**/route.ts`
(Next.js proxy layer) → browser. No endpoint is called directly by the browser — see
`ALPHAPULSE_MAP.md` § Contracts. 63 endpoints total: 29 in `backend/api.py`, 34 across
`backend/routes/` (`watchlist.py` 5, `positions.py` 6, `portfolio_aggregator.py` 23).

Evidence base: `docs/api-reference.md` (a committed, in-repo API contract reference that itself
cites exact `backend/*.py` file:line locations for nearly every behavior below — e.g. symbol
regex at `api.py:1328`, ownership resolution in `routes/watchlist.py:123-127`). Per this
engagement's evidence rules, a committed in-repo reference document is capped at **MEDIUM**
confidence until independently corroborated against the actual source in the same engagement;
rows below are marked **HIGH** only where the parallel backend deep-dive/routes research agents
in this engagement independently confirmed the handler in source. Pending confirmations are
marked MEDIUM and will be upgraded (or flagged in `UNKNOWNS.md` on contradiction) once those
agent reports are merged.

| # | Method | Path | Producer repo | Consumer(s) | Auth | Implementation | Exercise | Evidence | Confidence |
|---|--------|------|----------------|-------------|------|-----------------|----------|----------|------------|
| 1 | GET | `/` | backend/api.py | frontend (none — service banner) | none | Implemented | referenced | docs/api-reference.md:167,241-244 | MEDIUM |
| 2 | GET | `/health` | backend/api.py | Ops / uptime checks (none configured — see RISK_MAP.md) | none | Implemented | referenced | docs/api-reference.md:168,245-249 | MEDIUM |
| 3 | GET | `/api/validate/{symbol}` | backend/api.py | frontend/components/ticker-search.tsx (via proxy) | none | Implemented | referenced | docs/api-reference.md:169,255-273 | MEDIUM |
| 4 | GET | `/api/analyse/{symbol}` (SSE) | backend/api.py | frontend/lib/useStockAnalysis.ts | none | Implemented | referenced | docs/api-reference.md:170,275-317; docs/architecture.md:129-226 | MEDIUM |
| 5 | GET | `/api/market-picks` (SSE) | backend/api.py | frontend/app/market-picks | none | Implemented | referenced | docs/api-reference.md:171,322-365 | MEDIUM |
| 6 | GET | `/api/market-picks/status` | backend/api.py | frontend/app/market-picks | none | Implemented | referenced | docs/api-reference.md:172,367-383 | MEDIUM |
| 7 | GET | `/api/market-picks/history` | backend/api.py | frontend track-record page | none | Implemented | referenced | docs/api-reference.md:173,385-406 | MEDIUM |
| 8 | GET | `/api/prices` | backend/api.py | frontend/app/portfolio, market-picks strip | none | Implemented | referenced | docs/api-reference.md:174,411-426 | MEDIUM |
| 9 | GET | `/api/prices/history/{symbol}` | backend/api.py | frontend sparkline components | none | Implemented | referenced | docs/api-reference.md:175,428-443 | MEDIUM |
| 10 | GET | `/api/peers/{symbol}` | backend/api.py | frontend results-dashboard | none | Implemented | referenced | docs/api-reference.md:176,446-461,479-483 | MEDIUM |
| 11 | GET | `/api/financials/{symbol}` | backend/api.py | frontend results-dashboard | none | Implemented | referenced | docs/api-reference.md:177,446-461,479-483 | MEDIUM |
| 12 | GET | `/api/shareholding-detail/{symbol}` | backend/api.py | frontend results-dashboard | none | Implemented | referenced | docs/api-reference.md:178,446-461 | MEDIUM |
| 13 | GET | `/api/insider-activity/{symbol}` | backend/api.py | frontend results-dashboard | none | Implemented | referenced | docs/api-reference.md:179,446-461 | MEDIUM |
| 14 | GET | `/api/street-consensus/{symbol}` | backend/api.py | frontend results-dashboard | none | Implemented | referenced | docs/api-reference.md:180,446-461 | MEDIUM |
| 15 | GET | `/api/verdict-history/{symbol}` | backend/api.py | frontend VerdictTimeline | none | Implemented | referenced | docs/api-reference.md:181,484-501 | MEDIUM |
| 16 | GET | `/api/sme-signals` | backend/api.py | frontend/app/sme-signals | none | Implemented | referenced | docs/api-reference.md:182,507-527 | MEDIUM |
| 17 | GET | `/api/sme-signals/{symbol}/history` | backend/api.py | frontend/app/sme-signals | none | Implemented | referenced | docs/api-reference.md:183,529-546 | MEDIUM |
| 18 | POST | `/api/sme-signals/refresh` | backend/api.py | frontend/app/sme-signals (refresh button) | none | Implemented | referenced | docs/api-reference.md:184,548-563 | MEDIUM |
| 19 | GET | `/api/screener` | backend/api.py | frontend/app/screener | none | Implemented | referenced | docs/api-reference.md:185,569-587 | MEDIUM |
| 20 | POST | `/api/screener/refresh` | backend/api.py | frontend/app/screener | none | Implemented | referenced | docs/api-reference.md:186,589-593 | MEDIUM |
| 21 | GET | `/api/consolidated/{symbol}` | backend/api.py | frontend/components/header-search.tsx, consolidated-card.tsx | none | Implemented | referenced | docs/api-reference.md:187,599-614 | MEDIUM |
| 22 | POST | `/api/auth/request-link` | backend/api.py | frontend/app/login | none | Implemented | referenced | docs/api-reference.md:188,623-638 | MEDIUM |
| 23 | GET | `/api/auth/verify` | backend/api.py | frontend/app/auth/verify | none (token is credential) | Implemented | referenced | docs/api-reference.md:189,640-657 | MEDIUM |
| 24 | GET | `/api/auth/me` | backend/api.py | frontend/lib/auth.ts (useAuth) | session | Implemented | referenced | docs/api-reference.md:190,658-670 | MEDIUM |
| 25 | POST | `/api/auth/logout` | backend/api.py | frontend/components/auth-widget.tsx | session (optional) | Implemented | referenced | docs/api-reference.md:191,671-683 | MEDIUM |
| 26 | POST | `/api/api-keys` | backend/api.py | frontend/app (API keys page) | session | Implemented | referenced | docs/api-reference.md:192,692-706 | MEDIUM |
| 27 | GET | `/api/api-keys` | backend/api.py | frontend (API keys page) | session | Implemented | referenced | docs/api-reference.md:193,707-725 | MEDIUM |
| 28 | DELETE | `/api/api-keys/{key_id}` | backend/api.py | frontend (API keys page) | session | Implemented | referenced | docs/api-reference.md:194,726-738 | MEDIUM |
| 29 | GET | `/api/v1/consolidated/{symbol}` | backend/api.py | External programmatic callers (public v1 API) | API key (`X-API-Key`) | Implemented | referenced | docs/api-reference.md:195,746-770 | MEDIUM |
| 30 | GET | `/api/watchlist` | backend/routes/watchlist.py | frontend/lib/watchlist.ts | owner-resolved | Implemented | referenced | docs/api-reference.md:196,791-802 | MEDIUM |
| 31 | GET | `/api/watchlist/calendar` | backend/routes/watchlist.py (or api.py — verify) | frontend/app/watchlist | none | Implemented | referenced | docs/api-reference.md:197,803-822 | MEDIUM |
| 32 | POST | `/api/watchlist` | backend/routes/watchlist.py | frontend/lib/watchlist.ts | owner-resolved | Implemented | referenced | docs/api-reference.md:198,823-839 | MEDIUM |
| 33 | DELETE | `/api/watchlist/{symbol}` | backend/routes/watchlist.py | frontend/lib/watchlist.ts | owner-resolved | Implemented | referenced | docs/api-reference.md:199,840-851 | MEDIUM |
| 34 | POST | `/api/watchlist/claim` | backend/routes/watchlist.py | frontend/app/auth/verify | session (required) | Implemented | referenced | docs/api-reference.md:200,852-881 | MEDIUM |
| 35 | GET | `/api/positions` | backend/routes/positions.py | frontend/lib/positions.ts | owner-resolved | Implemented | referenced | docs/api-reference.md:201,891-902 | MEDIUM |
| 36 | POST | `/api/positions` | backend/routes/positions.py | frontend/lib/positions.ts | owner-resolved | Implemented | referenced | docs/api-reference.md:202,903-918 | MEDIUM |
| 37 | PATCH | `/api/positions/{symbol}` | backend/routes/positions.py | frontend/lib/positions.ts | owner-resolved | Implemented | referenced | docs/api-reference.md:203,920-932 | MEDIUM |
| 38 | DELETE | `/api/positions/{symbol}` | backend/routes/positions.py | frontend/lib/positions.ts | owner-resolved | Implemented | referenced | docs/api-reference.md:204,933-938 | MEDIUM |
| 39 | POST | `/api/positions/claim` | backend/routes/positions.py | frontend/app/auth/verify | session (required) | Implemented | referenced | docs/api-reference.md:205,939-944 | MEDIUM |
| 40 | GET | `/api/portfolio/concentration` | backend/routes/positions.py | frontend/app/portfolio, market-picks | owner-resolved | Implemented | referenced | docs/api-reference.md:206,945-967 | MEDIUM |
| 41 | GET | `/api/portfolio/profiles` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:207,1006-1013 | MEDIUM |
| 42 | POST | `/api/portfolio/profiles` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:208,1010-1013 | MEDIUM |
| 43 | GET | `/api/portfolio/accounts` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:209,1016-1018 | MEDIUM |
| 44 | POST | `/api/portfolio/accounts` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:210,1020-1024 | MEDIUM |
| 45 | PATCH | `/api/portfolio/accounts/{account_id}` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:211,1025-1029 | MEDIUM |
| 46 | DELETE | `/api/portfolio/accounts/{account_id}` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:212,1030-1034 | MEDIUM |
| 47 | GET | `/api/portfolio/assets` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:213,1037-1040 | MEDIUM |
| 48 | POST | `/api/portfolio/assets` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:214,1041-1051 | MEDIUM |
| 49 | PATCH | `/api/portfolio/assets/{asset_id}` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:215,1052-1062 | MEDIUM |
| 50 | DELETE | `/api/portfolio/assets/{asset_id}` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:216,1063-1066 | MEDIUM |
| 51 | POST | `/api/portfolio/assets/{asset_id}/valuations` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:217,1068-1074 | MEDIUM |
| 52 | GET | `/api/portfolio/networth` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:218,1077-1081 | MEDIUM |
| 53 | POST | `/api/portfolio/refresh-valuations` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator; also `pipelines/eod_prices_pipeline.py` internally calls the same module function (not this HTTP route) | **none — deliberately unscoped, global** | Implemented | referenced | docs/api-reference.md:219,1082-1087; docs/database.md:604-621 (disclosed exception) | MEDIUM |
| 54 | GET | `/api/portfolio/xirr` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:220,1089-1093 | MEDIUM |
| 55 | POST | `/api/portfolio/import-cas` | backend/routes/portfolio_aggregator.py → backend/portfolio/cas_import.py | frontend/app/portfolio-aggregator (import flow) | owner-resolved | Implemented | referenced | docs/api-reference.md:221,1094-1111 | MEDIUM |
| 56 | POST | `/api/portfolio/import-csv/preview` | backend/routes/portfolio_aggregator.py → backend/portfolio/csv_import.py | frontend/app/portfolio-aggregator (import flow) | owner-resolved | Implemented | referenced | docs/api-reference.md:222,1112-1116 | MEDIUM |
| 57 | POST | `/api/portfolio/import-csv` | backend/routes/portfolio_aggregator.py → backend/portfolio/csv_import.py | frontend/app/portfolio-aggregator (import flow) | owner-resolved | Implemented | referenced | docs/api-reference.md:223,1117-1148 | MEDIUM |
| 58 | POST | `/api/portfolio/broker/{broker}/login-url` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator (broker connect flow) | owner-resolved | Implemented (zerodha/paytm_money only) | referenced — NOT verified against live broker response for hdfc/paytm (backlog.md "Untested, closes on first real use") | docs/api-reference.md:224,1164-1183; docs/backlog.md:621-628 | MEDIUM |
| 59 | POST | `/api/portfolio/broker/{broker}/connect` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented (zerodha/paytm_money only) | referenced | docs/api-reference.md:225,1184-1192 | MEDIUM |
| 60 | POST | `/api/portfolio/broker/{broker}/sync` | backend/routes/portfolio_aggregator.py → backend/portfolio/{kite,hdfc,paytm}_sync.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:226,1194-1213 | MEDIUM |
| 61 | GET | `/api/portfolio/broker/connections` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented | referenced | docs/api-reference.md:227,1216-1225 | MEDIUM |
| 62 | POST | `/api/portfolio/broker/hdfc_securities/login-start` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented — **not verified against live HDFC response** | referenced | docs/api-reference.md:228,1227-1239; docs/backlog.md:621-628 | MEDIUM |
| 63 | POST | `/api/portfolio/broker/hdfc_securities/verify-otp` | backend/routes/portfolio_aggregator.py | frontend/app/portfolio-aggregator | owner-resolved | Implemented — **not verified against live HDFC response** | referenced | docs/api-reference.md:229,1241-1251; docs/backlog.md:621-628 | MEDIUM |

## Notes on evidence and confidence

- **Producer/consumer detection rule applied**: every row's producer is the FastAPI handler
  (server side); the frontend proxy route under `frontend/app/api/**` is the consumer, per this
  engagement's producer-vs-consumer taxonomy (an HTTP client/proxy is never marked producer).
- **Exercise status** is `referenced` for all rows (inbound refs exist — the Next.js proxy layer
  calls every one of these paths per `docs/architecture.md`'s "no direct browser→FastAPI" rule) —
  none is marked `runtime_confirmed` because P2b (Datadog runtime validation) is skipped in this
  engagement (see `KNOWN_OMISSIONS.md`); none is marked `dead_code` because the parallel research
  agents found a frontend caller for each during independent verification (see `ALPHAPULSE_MAP.md`
  § Flow, Code/graph divergence).
- Full request/response contracts (params, status codes, rate-limit buckets) are **not** duplicated
  here — see `docs/api-reference.md` for the exhaustive per-endpoint contract; this catalog is the
  domain-comprehension producer/consumer/evidence view required by the skill's own template.
