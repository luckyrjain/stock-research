import { test, expect } from '@playwright/test';

const POSITION = {
  symbol: 'TCS',
  company: 'Tata Consultancy Services',
  exchange: 'NSE',
  entry_price: 3000,
  target_price: 3600,
  stop_loss: 2800,
  shares: null,
  bought_at: new Date().toISOString(),
};

test.describe('Portfolio page', () => {
  test('shows the empty state with no tracked positions', async ({ page }) => {
    await page.route('**/api/positions*', route => route.fulfill({ json: { items: [] } }));
    await page.goto('/portfolio');
    await expect(page.getByText("You haven't marked any picks as bought yet.")).toBeVisible();
  });

  test('aggregates a fetched position against a live price', async ({ page }) => {
    // Positions are DB-backed via GET /api/positions (frontend/lib/positions.ts) —
    // mock the network response the same way every other backend call in this
    // suite is mocked, rather than seeding localStorage the way this test used
    // to before positions moved off pure localStorage onto the same
    // client_id/account-backed model as the watchlist.
    await page.route('**/api/positions*', route => route.fulfill({ json: { items: [POSITION] } }));
    await page.route('**/api/prices?*', route => route.fulfill({
      json: { prices: { TCS: { price: 3300, change_pct: 2.1 } } },
    }));

    await page.goto('/portfolio');

    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    // (3300 - 3000) / 3000 * 100 = +10.0%
    await expect(page.getByText('+10.0%').first()).toBeVisible();
    await expect(page.getByText('100%')).toBeVisible(); // win rate: 1/1 priced positions in profit
  });
});
