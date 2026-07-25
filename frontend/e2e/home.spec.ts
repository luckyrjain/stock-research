import { test, expect } from '@playwright/test';
import { sseAnalysisBody, SSE_HEADERS, validationResult } from './fixtures';

test.describe('Home page', () => {
  test('shows the search UI and entry points into the other modes', async ({ page }) => {
    // The idle home page (before any analysis has run) shows a compact hero
    // with just two pill links, not the full nav bar — the full nav only
    // appears once `phase` leaves 'idle' (see the second test below, which
    // exercises that state after a completed analysis).
    await page.goto('/');
    await expect(page.getByLabel('NSE or BSE stock ticker')).toBeVisible();
    await expect(page.getByRole('link', { name: /top picks/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /SME golden cross screener/ })).toBeVisible();
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
    await expect(page.getByText('Symbol found')).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: 'Analyse Stock' }).click();

    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(`${symbol} Limited`).first()).toBeVisible();

    // The full nav bar (hidden on the idle hero — see the test above) appears
    // once phase leaves 'idle'. Home's own nav doesn't carry a Portfolio
    // link (see CLAUDE.md's "Portfolio summary" section — positions are
    // only ever created from Market Picks, so that page's nav is enough).
    await expect(page.getByRole('link', { name: 'Market Picks' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Screener' })).toBeVisible();
  });
});
