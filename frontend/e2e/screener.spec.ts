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

  test('debounces the Max P/E filter instead of firing one request per keystroke', async ({ page }) => {
    // Regression test: peMax/marketCapMin used to be direct dependencies of
    // fetchStocks, so every onChange fired a new GET /api/screener request —
    // typing a multi-digit value could approach or trip the documented
    // 60/min rate limit. Now debounced (420ms, same as ticker-search.tsx).
    let requestCount = 0;
    await page.route('**/api/screener?*', route => {
      requestCount++;
      return route.fulfill({
        json: {
          stocks: FIXTURE_STOCKS, total: FIXTURE_STOCKS.length, total_monitored: 500,
          industries: [], last_run: new Date().toISOString(), refreshing: false,
        },
      });
    });

    await page.goto('/screener');
    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    const afterInitialLoad = requestCount;

    // Fast typing (well under the 420ms debounce window between keystrokes)
    // must not fire a request per character.
    await page.getByLabel('Max P/E').pressSequentially('12345', { delay: 30 });
    expect(requestCount).toBe(afterInitialLoad);

    // Exactly one request fires once typing settles past the debounce delay.
    await expect.poll(() => requestCount, { timeout: 2000 }).toBe(afterInitialLoad + 1);
  });

  test('"Load more" fetches the next page and appends it, without dropping the already-loaded rows', async ({ page }) => {
    // Regression test: GET /api/screener always supported offset/limit and
    // returned a real `total`, but this page previously hardcoded limit=200
    // and never read `total` or offered a way past it — a broad sort over
    // the full NIFTY 500 universe silently showed only a 200-row slice.
    const requestedOffsets: string[] = [];
    await page.route('**/api/screener?*', route => {
      const url = new URL(route.request().url());
      const offset = url.searchParams.get('offset') ?? '0';
      // The page also fires a separate, filter-independent limit=500 fetch
      // to populate the sector heatmap (see page.tsx's own comment on why)
      // — only the main table's limit=200 requests are relevant here.
      if (url.searchParams.get('limit') === '200') requestedOffsets.push(offset);
      const stocks = offset === '0' ? [FIXTURE_STOCKS[0]] : [FIXTURE_STOCKS[1]];
      return route.fulfill({
        json: {
          stocks, total: 2, total_monitored: 500,
          industries: [], last_run: new Date().toISOString(), refreshing: false,
        },
      });
    });

    await page.goto('/screener');
    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    await expect(page.getByText('Showing 1 of 2 matching stocks')).toBeVisible();

    await page.getByRole('button', { name: /Load 1 more/ }).click();

    await expect(page.getByRole('link', { name: 'RELIANCE' })).toBeVisible();
    // The first page's row is still there — this appends, it doesn't replace.
    await expect(page.getByRole('link', { name: 'TCS' })).toBeVisible();
    await expect(page.getByText('Showing 2 of 2 matching stocks')).toBeVisible();
    // No more rows to load once every matching stock is shown.
    await expect(page.getByRole('button', { name: /Load.*more/ })).toHaveCount(0);
    expect(requestedOffsets).toEqual(['0', '200']);
  });
});
