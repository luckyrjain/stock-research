import { test, expect } from '@playwright/test';
import { SSE_HEADERS, marketPick, marketPicksSseBody } from './fixtures';

test.describe('Market Picks page', () => {
  test('shows the idle hero with a CTA to run a scan', async ({ page }) => {
    await page.route('**/api/market-picks/status', route => route.fulfill({
      json: { last_run_at: null, cache_fresh: false, next_scheduled_at: new Date().toISOString() },
    }));

    await page.goto('/market-picks');

    await expect(page.getByRole('button', { name: /See This Week.s Picks/ })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Track Record' })).toBeVisible();
  });

  test('runs a full mocked scan and renders the ranked picks', async ({ page }) => {
    const picks = [marketPick('TCS'), marketPick('INFY', { rank: 2, recommendation: 'WATCHLIST' })];

    await page.route('**/api/market-picks/status', route => route.fulfill({
      json: { last_run_at: null, cache_fresh: false, next_scheduled_at: new Date().toISOString() },
    }));
    await page.route('**/api/market-picks*', route => route.fulfill({
      status: 200, headers: SSE_HEADERS, body: marketPicksSseBody(picks),
    }));
    await page.route('**/api/prices*', route => route.fulfill({
      json: { prices: { TCS: { price: 1234.5, change_pct: 1.8 }, INFY: { price: 1234.5, change_pct: 1.8 } } },
    }));

    await page.goto('/market-picks');
    await page.getByRole('button', { name: /See This Week.s Picks/ }).click();

    await expect(page.getByText('TCS Limited')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('INFY Limited')).toBeVisible();
    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('WATCH', { exact: true }).first()).toBeVisible();
  });
});
