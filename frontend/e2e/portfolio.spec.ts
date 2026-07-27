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

  test('counts a share-tracked position toward Invested even when its live price is unavailable', async ({ page }) => {
    // Regression test for an adversarial-review finding: the "Invested"
    // total and the "N of M positions have a share count" label were both
    // silently gated on live-price availability (GET /api/prices missing
    // a symbol is a documented real failure mode), not just on whether the
    // user entered a share count. A position with a valid share count and
    // entry price -- enough to know how much capital went in -- was
    // wrongly excluded from both when its live quote happened to be
    // unavailable that moment.
    const withShares = { ...POSITION, symbol: 'TCS', shares: 10, entry_price: 3000 };
    const noQuote = { ...POSITION, symbol: 'INFY', company: 'Infosys', shares: 5, entry_price: 1500 };

    await page.route('**/api/positions*', route => route.fulfill({ json: { items: [withShares, noQuote] } }));
    // GET /api/prices only returns a quote for TCS -- INFY's is missing,
    // simulating GET /api/prices' documented real failure mode for one
    // symbol out of several requested.
    await page.route('**/api/prices?*', route => route.fulfill({
      json: { prices: { TCS: { price: 3300, change_pct: 2.1 } } },
    }));

    await page.goto('/portfolio');

    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'INFY' })).toBeVisible();

    // Both positions have a share count, regardless of INFY's missing quote.
    await expect(page.getByText('2 of 2 positions have a share count')).toBeVisible();
    // Invested = 10*3000 (TCS) + 5*1500 (INFY, unpriced) = ₹37,500 -- INFY's
    // known invested amount must still contribute even without a live quote.
    await expect(page.getByText('₹37,500')).toBeVisible();
  });

  test('shows "—" for Current Value/P&L, not a misleading ₹0, when no share-tracked position is priced yet', async ({ page }) => {
    // Regression test for a gap an independent adversarial review caught in
    // the fix above: broadening "Invested" to not require a live price
    // meant that when NO share-tracked position has a live price at all
    // (GET /api/prices still pending, or failed outright -- both real,
    // documented failure modes), Current Value/P&L were still computed as
    // 0/0 over an empty `pricedWithShares` set and rendered as a real
    // "₹0" / "+₹0" figure (styled green, since 0 >= 0) instead of the
    // "absent, not a guessed zero" treatment this same block's own comment
    // already promises for the Invested figure.
    const withShares = { ...POSITION, symbol: 'TCS', shares: 10, entry_price: 3000 };

    await page.route('**/api/positions*', route => route.fulfill({ json: { items: [withShares] } }));
    // No TCS entry in the returned prices map -- simulates GET /api/prices
    // never resolving a quote for this symbol.
    await page.route('**/api/prices?*', route => route.fulfill({ json: { prices: {} } }));

    await page.goto('/portfolio');

    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    await expect(page.getByText('1 of 1 position have a share count')).toBeVisible();
    await expect(page.getByText('₹30,000')).toBeVisible(); // Invested = 10*3000, still known

    // "Current Value"/"P&L" as exact-text labels are unique to the stat
    // tiles -- the table's own headers render "Current"/"Value" as two
    // separate <th>s, and its sort button's text is "P&L ↓", not "P&L".
    const currentValueCard = page.getByText('Current Value', { exact: true }).locator('xpath=..');
    await expect(currentValueCard.getByText('—', { exact: true })).toBeVisible();

    const pnlCard = page.getByText('P&L', { exact: true }).locator('xpath=..');
    await expect(pnlCard.getByText('—', { exact: true })).toBeVisible();
    // The misleading zero this test guards against would render as this
    // exact text -- assert it's genuinely absent, not just that "—" exists
    // somewhere else on the page.
    await expect(page.getByText('+₹0')).toHaveCount(0);
  });
});
