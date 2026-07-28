import { test, expect } from '@playwright/test';
import { sseAnalysisBody, SSE_HEADERS, validationResult } from './fixtures';

test.describe('Home page', () => {
  test('shows the search UI and entry points into the other modes', async ({ page }) => {
    // The idle home page (before any analysis has run) shows a compact hero
    // with two pill links AND the full nav bar — a first-time visitor must
    // be able to sign in and discover Screener/Watchlist/Portfolio/Compare
    // without first committing a real ticker and waiting through a full
    // analysis (see the second test below for the post-analysis state).
    await page.goto('/');
    await expect(page.getByLabel('NSE or BSE stock ticker')).toBeVisible();
    await expect(page.getByRole('link', { name: /top picks/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /SME golden cross screener/ })).toBeVisible();
    // exact: true — the idle hero's own "⚡ SME golden cross screener →" pill
    // (checked on the line above) also contains the substring "screener",
    // which would otherwise make this locator match two elements once the
    // nav is always mounted alongside that pill.
    await expect(page.getByRole('link', { name: 'Screener', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Watchlist' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'API Keys' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible();
  });

  test('nav collapses into a hamburger menu on a phone-width viewport', async ({ page }) => {
    // Regression test for the deep gap analysis finding: SiteNav's 8
    // pipe-separated links previously just wrapped onto 2-3 lines of small
    // text above every page's content on a narrow viewport — real friction
    // for this India-focused, mobile-heavy product. Below the md
    // breakpoint they now collapse behind a toggle instead.
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto('/');

    const toggle = page.getByRole('button', { name: 'Toggle navigation menu' });
    await expect(toggle).toBeVisible();
    // The desktop link list is present in the DOM (display:contents at
    // md+) but not visible at this width.
    await expect(page.getByRole('menu')).toHaveCount(0);

    await toggle.click();
    const menu = page.getByRole('menu');
    await expect(menu).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Screener' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Watchlist' })).toBeVisible();

    await menu.getByRole('menuitem', { name: 'Watchlist' }).click();
    await expect(page).toHaveURL('/watchlist');
  });

  test('idle hero offers a one-click sample report for skeptical first-time visitors', async ({ page }) => {
    const symbol = 'TCS';
    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({ status: 200, headers: SSE_HEADERS, body: sseAnalysisBody(symbol) }));
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: { symbol, insider_trades: [], bulk_block_deals: [] },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    await page.getByRole('button', { name: /See a real report for TCS/ }).click();

    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible({ timeout: 15000 });
  });

  test('runs a full mocked stock analysis and renders the verdict', async ({ page }) => {
    const symbol = 'TCS';

    await page.route(`**/api/validate/${symbol}`, route =>
      route.fulfill({ json: validationResult(symbol) }));

    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({ status: 200, headers: SSE_HEADERS, body: sseAnalysisBody(symbol) }));

    // Standalone add-on cards fetched independently after the report loads —
    // let them resolve to an explicit empty/graceful shape rather than
    // falling through to a real (absent) backend, so the test isn't
    // dependent on the Next.js proxy's unavailable-backend fallback timing.
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: { symbol, insider_trades: [], bulk_block_deals: [] },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    const input = page.getByLabel('NSE or BSE stock ticker');
    await input.fill(symbol);
    // "Symbol found" also exists as an sr-only live-region announcement, but
    // the actual visible confirmation is the company-name/exchange row that
    // ticker-search.tsx renders once validation resolves — wait for that.
    await expect(page.getByText(validationResult(symbol).company)).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: 'Analyse Stock' }).click();

    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(`${symbol} Limited`).first()).toBeVisible();

    // The nav bar is visible on the idle hero too now (see the test above) —
    // still present once a report has loaded.
    await expect(page.getByRole('link', { name: 'Market Picks' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Screener', exact: true })).toBeVisible();
  });

  test('formats a value just under a Cr/L boundary as the larger unit, not a false "100" of the smaller one', async ({ page }) => {
    // Regression test for an adversarial-review finding: fmtCr()/fmtInr()
    // compared the raw unrounded value against a unit threshold, but
    // formatted via toFixed(), which rounds separately. A market cap of
    // 99,998 Cr fails the ">= 1,00,000" (1L Cr) check and falls to the
    // K-Cr branch, but (99998/1000).toFixed(2) itself rounds up to
    // "100.00" -- displaying the nonsensical "₹100.00K Cr" instead of
    // "₹1.00L Cr". Same bug shape for fmtInr() at the Cr/L boundary, used
    // for insider-trade values.
    const symbol = 'TCS';

    await page.route(`**/api/validate/${symbol}`, route =>
      route.fulfill({ json: validationResult(symbol) }));
    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({
        status: 200, headers: SSE_HEADERS,
        body: sseAnalysisBody(symbol, { stockInfoOverrides: { market_cap_cr: 99998 } }),
      }));
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: {
        symbol,
        insider_trades: [{
          person: 'Fixture Promoter', category: 'Promoter', action: 'BUY',
          quantity: 1000, value: 9999960, date: '20-Jul-2026', date_iso: '2026-07-20',
        }],
        bulk_block_deals: [],
        insider_trades_unavailable: false,
        bulk_block_deals_unavailable: false,
      },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    const input = page.getByLabel('NSE or BSE stock ticker');
    await input.fill(symbol);
    await expect(page.getByText(validationResult(symbol).company)).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: 'Analyse Stock' }).click();

    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible({ timeout: 15000 });

    // fmtCr(99998) via the Market Cap key metric.
    await expect(page.getByText('₹1.00L Cr')).toBeVisible();
    await expect(page.getByText('₹100.00K Cr')).toHaveCount(0);

    // fmtInr(9999960) via the insider-trade value.
    await expect(page.getByText('₹1.0 Cr')).toBeVisible();
    await expect(page.getByText('₹100.0L')).toHaveCount(0);
  });

  test('shows the specific backend-unavailable message, not a generic "connection lost" fallback', async ({ page }) => {
    // Regression test for an adversarial-review finding: app/api/analyse/
    // [symbol]/route.ts's crafted SSE error message ("Backend unavailable.
    // Please make sure the analysis service is running.") used to be
    // returned with a non-200 HTTP status. EventSource only ever reads a
    // response body when the status is exactly 200 with a text/event-stream
    // Content-Type -- any other status makes the browser "fail the
    // connection" and fire a generic error event WITHOUT parsing the body,
    // so this specific message was unreachable; useStockAnalysis.ts's
    // onerror handler always fell back to a hardcoded generic string
    // instead. Deliberately does NOT mock /api/analyse/** -- the E2E
    // harness runs no real backend process, so the real Next.js proxy
    // route's own fetch() call genuinely fails, exercising its actual
    // error-response code rather than a browser-level mock standing in
    // for it.
    const symbol = 'TCS';
    await page.route(`**/api/validate/${symbol}`, route =>
      route.fulfill({ json: validationResult(symbol) }));

    await page.goto('/');
    const input = page.getByLabel('NSE or BSE stock ticker');
    await input.fill(symbol);
    await expect(page.getByText(validationResult(symbol).company)).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: 'Analyse Stock' }).click();

    await expect(page.getByText('Backend unavailable. Please make sure the analysis service is running.'))
      .toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Connection to server lost. Please try again.')).toHaveCount(0);
  });

  test('shows a degraded-analysis banner when every LLM provider failed', async ({ page }) => {
    // A full provider outage previously converged to a generic HOLD with no
    // visible signal that this wasn't a real analyst call — see crew.py's
    // run_analysis_with_fallback and the `degraded` field's own comment in
    // types/index.ts.
    const symbol = 'TCS';
    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({ status: 200, headers: SSE_HEADERS, body: sseAnalysisBody(symbol, { degraded: true }) }));
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: { symbol, insider_trades: [], bulk_block_deals: [] },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    await page.getByRole('button', { name: /See a real report for TCS/ }).click();

    await expect(page.getByText('Analysis degraded —', { exact: false })).toBeVisible({ timeout: 15000 });
  });
});
