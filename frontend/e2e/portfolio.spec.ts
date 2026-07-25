import { test, expect } from '@playwright/test';

const POSITION = {
  symbol: 'TCS',
  company: 'Tata Consultancy Services',
  exchange: 'NSE',
  entry_price: 3000,
  target_price: 3600,
  stop_loss: 2800,
  bought_at: new Date().toISOString(),
};

test.describe('Portfolio page', () => {
  test('shows the empty state with no tracked positions', async ({ page }) => {
    await page.goto('/portfolio');
    await expect(page.getByText("You haven't marked any picks as bought yet.")).toBeVisible();
  });

  test('aggregates a seeded position against a live price', async ({ page }) => {
    // Positions are purely localStorage (frontend/lib/positions.ts) — seed it
    // before the page's own scripts run via addInitScript, same as how a
    // real "I bought this" click on Market Picks would have populated it.
    await page.addInitScript((pos) => {
      window.localStorage.setItem('alphapulse_positions', JSON.stringify([pos]));
    }, POSITION);

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
