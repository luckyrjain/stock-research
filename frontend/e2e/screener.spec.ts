import { test, expect } from '@playwright/test';

const FIXTURE_STOCKS = [
  {
    symbol: 'TCS', company_name: 'Tata Consultancy Services', exchange: 'NSE',
    nse_industry: 'Information Technology', sector: 'Technology',
    current_price: 3456.78, pe_ratio: 28.5, market_cap_cr: 1250000,
    avg_volume_10d: 2500000, rsi14: 62.3, ema_trend: 'bullish', fetched_at: new Date().toISOString(),
  },
  {
    symbol: 'RELIANCE', company_name: 'Reliance Industries', exchange: 'NSE',
    nse_industry: 'Oil Gas & Consumable Fuels', sector: 'Energy',
    current_price: 2890.1, pe_ratio: 24.1, market_cap_cr: 1900000,
    avg_volume_10d: 5000000, rsi14: 45.0, ema_trend: 'bearish', fetched_at: new Date().toISOString(),
  },
];

test.describe('Screener page', () => {
  test('renders monitored stocks with trend badges', async ({ page }) => {
    await page.route('**/api/screener?*', route => route.fulfill({
      json: {
        stocks: FIXTURE_STOCKS,
        total: FIXTURE_STOCKS.length,
        total_monitored: 500,
        industries: ['Information Technology', 'Oil Gas & Consumable Fuels'],
        last_run: new Date().toISOString(),
        refreshing: false,
      },
    }));

    await page.goto('/screener');

    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'RELIANCE' })).toBeVisible();
    await expect(page.getByText('↑ Bullish')).toBeVisible();
    await expect(page.getByText('↓ Bearish')).toBeVisible();
    await expect(page.getByText('500 stocks monitored')).toBeVisible();
  });

  test('shows the empty state when no stocks match', async ({ page }) => {
    await page.route('**/api/screener?*', route => route.fulfill({
      json: { stocks: [], total: 0, total_monitored: 0, industries: [], last_run: null, refreshing: false },
    }));

    await page.goto('/screener');

    await expect(page.getByText(/hasn.t run yet/)).toBeVisible();
  });
});
