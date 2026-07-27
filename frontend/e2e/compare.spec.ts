import { test, expect } from '@playwright/test';
import { sseAnalysisBody, SSE_HEADERS } from './fixtures';

function mockAddOnCards(page: import('@playwright/test').Page, symbol: string) {
  return Promise.all([
    page.route(`**/api/peers/${symbol}`, route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    })),
    page.route(`**/api/insider-activity/${symbol}`, route => route.fulfill({
      json: { symbol, insider_trades: [], bulk_block_deals: [] },
    })),
    page.route(`**/api/street-consensus/${symbol}`, route => route.fulfill({
      json: { symbol, articles: [] },
    })),
    page.route(`**/api/prices/history/${symbol}**`, route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    })),
    page.route(`**/api/verdict-history/${symbol}`, route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    })),
  ]);
}

test.describe('Compare page', () => {
  test('shows the empty state with no symbols in the URL', async ({ page }) => {
    await page.goto('/compare');
    await expect(page.getByText('Enter two comma-separated tickers above to compare them side by side.')).toBeVisible();
  });

  test('runs two independent mocked analyses side by side', async ({ page }) => {
    const symbols = ['TCS', 'INFY'];

    for (const symbol of symbols) {
      await page.route(`**/api/analyse/${symbol}**`, route =>
        route.fulfill({ status: 200, headers: SSE_HEADERS, body: sseAnalysisBody(symbol) }));
      await mockAddOnCards(page, symbol);
    }

    await page.goto(`/compare?symbols=${symbols.join(',')}`);

    for (const symbol of symbols) {
      await expect(page.getByText(`${symbol} Limited`).first()).toBeVisible({ timeout: 15000 });
    }
    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'INFY' })).toBeVisible();
  });

  test('caps input at two symbols and normalizes casing', async ({ page }) => {
    await page.goto('/compare');
    await page.getByLabel('Tickers to compare, comma-separated').fill('tcs, infy, reliance');
    await page.getByRole('button', { name: 'Compare' }).click();

    // The URL carries the full typed list (RELIANCE included) so the
    // truncation message below can detect the drop — only the rendered
    // columns/fetches are capped at two, via parseSymbols().
    await expect(page).toHaveURL('/compare?symbols=TCS%2CINFY%2CRELIANCE');
    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'INFY' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'RELIANCE' })).toHaveCount(0);
    // Regression test for the deep gap analysis finding: pasting 3 tickers
    // previously silently dropped the third with zero indication anything
    // was cut off.
    await expect(page.getByText('Only the first 2 tickers are compared — 1 more was dropped.')).toBeVisible();
  });

  test('shows no truncation message when exactly two symbols are given', async ({ page }) => {
    await page.goto('/compare');
    await page.getByLabel('Tickers to compare, comma-separated').fill('tcs, infy');
    await page.getByRole('button', { name: 'Compare' }).click();

    await expect(page).toHaveURL('/compare?symbols=TCS%2CINFY');
    await expect(page.getByText(/tickers are compared/)).toHaveCount(0);
  });
});
