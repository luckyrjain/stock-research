import { test, expect } from '@playwright/test';

test.describe('Watchlist page', () => {
  test('renders a starred stock with its live price', async ({ page }) => {
    await page.route('**/api/watchlist?*', route => route.fulfill({
      json: {
        items: [
          { symbol: 'TCS', company: 'Tata Consultancy Services', exchange: 'NSE', addedAt: new Date().toISOString() },
        ],
      },
    }));
    await page.route('**/api/prices?*', route => route.fulfill({
      json: { prices: { TCS: { price: 3456.78, change_pct: 1.5 } } },
    }));

    await page.goto('/watchlist');

    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    await expect(page.getByText('Tata Consultancy Services')).toBeVisible();
    await expect(page.getByText('₹3456.78')).toBeVisible();
  });

  test('shows the empty state with no starred stocks', async ({ page }) => {
    await page.route('**/api/watchlist?*', route => route.fulfill({ json: { items: [] } }));

    await page.goto('/watchlist');

    await expect(page.getByText('Nothing here yet.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Analyse a stock →' })).toBeVisible();
  });
});
