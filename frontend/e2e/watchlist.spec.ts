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

  test('nav shows a passive alert dot when a watched stock has a same-day change', async ({ page }) => {
    // Regression test for the deep gap analysis finding: this signal
    // (verdict_history.detect_recent_changes, the same check
    // watchlist_alerts.py's daily digest email already computes) was
    // previously only visible to a user who happened to be on /watchlist
    // itself — nothing made a user elsewhere in the app aware anything
    // had changed.
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
    await page.route('**/api/watchlist/calendar?*', route => route.fulfill({
      json: {
        entries: [{
          symbol: 'TCS', corporate_actions: [], rating_action: null, next_results_date: null,
          recommendation_change: { old_recommendation: 'HOLD', new_recommendation: 'BUY', confidence: 'HIGH' },
          price_move: null,
        }],
      },
    }));
    await page.route('**/api/market-picks/status', route => route.fulfill({
      json: { last_run_at: null, cache_fresh: false, next_scheduled_at: new Date().toISOString() },
    }));

    // On the market-picks page (not /watchlist itself) so this proves the
    // signal reaches a user who isn't already looking at their watchlist.
    await page.goto('/market-picks');

    const watchlistLink = page.getByRole('link', { name: 'Watchlist' });
    await expect(watchlistLink).toBeVisible();
    await expect(watchlistLink.locator('[aria-label*="same-day"]')).toBeVisible();
  });
});
