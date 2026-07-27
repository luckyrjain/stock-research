import { test, expect } from '@playwright/test';

const FIXTURE_SIGNALS = [
  {
    symbol: 'SMEONE', name: 'SME One Ltd', exchange: 'NSE', isin: null,
    avg_volume_20d: 15000, avg_turnover_20d: 750000, market_cap_cr: 120,
    trade_date: '2026-07-24', close_price: 55.5, ema20: 54.0, ema50: 51.0,
    rsi14: 62.0, volume_spike: true, cross: 'golden', in_golden_cross: true,
  },
  {
    symbol: 'SMETWO', name: 'SME Two Ltd', exchange: 'BSE', isin: 'INE000X01234',
    avg_volume_20d: 8000, avg_turnover_20d: 320000, market_cap_cr: 60,
    trade_date: '2026-07-24', close_price: 22.1, ema20: 21.0, ema50: 23.5,
    rsi14: 28.0, volume_spike: false, cross: 'death', in_golden_cross: false,
  },
];

const FIXTURE_RESPONSE = {
  signals: FIXTURE_SIGNALS,
  total_monitored: 300,
  golden_now: 42,
  last_run: new Date().toISOString(),
  refreshing: false,
  golden_hit_rate_90d: { sample_size: 20, win_rate: 65, lookback_days: 90, forward_days: 20 },
};

test.describe('SME Signals page', () => {
  test('renders golden/death cross rows with jargon explained via tooltips', async ({ page }) => {
    await page.route('**/api/sme-signals?*', route => route.fulfill({ json: FIXTURE_RESPONSE }));

    await page.goto('/sme-signals');

    await expect(page.getByRole('link', { name: 'SMEONE' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'SMETWO' })).toBeVisible();
    await expect(page.getByText('65%')).toBeVisible(); // Golden Follow-Through stat

    // Regression coverage for the deep gap analysis finding: this page was
    // previously the one complex page with zero InfoTooltip usage, despite
    // being arguably the most jargon-dense page in the product.
    await page.getByRole('button', { name: 'About Golden cross / death cross' }).click();
    await expect(page.getByRole('dialog', { name: 'Golden cross / death cross' })).toBeVisible();
    await expect(page.getByText(/classic bullish momentum signal/)).toBeVisible();
  });

  test('shows the empty state when no crosses match the selected filters', async ({ page }) => {
    await page.route('**/api/sme-signals?*', route => route.fulfill({
      json: { signals: [], total_monitored: 300, golden_now: 0, last_run: null, refreshing: false,
              golden_hit_rate_90d: { sample_size: 0, win_rate: null, lookback_days: 90, forward_days: 20 } },
    }));

    await page.goto('/sme-signals');

    await expect(page.getByText('No crossovers found for the selected filters.')).toBeVisible();
  });

  test('RSI tooltip explains oversold/overbought thresholds', async ({ page }) => {
    await page.route('**/api/sme-signals?*', route => route.fulfill({ json: FIXTURE_RESPONSE }));

    await page.goto('/sme-signals');
    await expect(page.getByRole('link', { name: 'SMEONE' })).toBeVisible();

    await page.getByRole('button', { name: 'About RSI(14)' }).click();
    await expect(page.getByRole('dialog', { name: 'RSI(14)' })).toBeVisible();
    await expect(page.getByText(/Relative Strength Index/)).toBeVisible();
  });
});
