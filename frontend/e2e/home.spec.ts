import { test, expect } from '@playwright/test';
import { sseAnalysisBody, SSE_HEADERS, validationResult, expectNoA11yViolations } from './fixtures';

test.describe('Home page', () => {
  test('shows the search UI and entry points into the other modes', async ({ page }) => {
    // The idle home page (before any analysis has run) shows a compact hero
    // with two pill links AND the full nav bar — a first-time visitor must
    // be able to sign in and discover Screener/Watchlist/Portfolio/Compare
    // without first committing a real ticker and waiting through a full
    // analysis (see the second test below for the post-analysis state).
    await page.goto('/');
    await expect(page.getByLabel('NSE or BSE stock ticker')).toBeVisible();
    await expect(page.getByRole('link', { name: /top picks/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /SME golden cross screener/ })).toBeVisible();
    // exact: true — the idle hero's own "⚡ SME golden cross screener →" pill
    // (checked on the line above) also contains the substring "screener",
    // which would otherwise make this locator match two elements once the
    // nav is always mounted alongside that pill.
    await expect(page.getByRole('link', { name: 'Screener', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Watchlist' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'API Keys' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible();
    // ENF-03 (design.md §19): landmark/label/contrast/heading-order gate.
    await expectNoA11yViolations(page);
  });

  test('nav collapses into a hamburger menu on a phone-width viewport', async ({ page }) => {
    // Regression test for the deep gap analysis finding: SiteNav's 8
    // pipe-separated links previously just wrapped onto 2-3 lines of small
    // text above every page's content on a narrow viewport — real friction
    // for this India-focused, mobile-heavy product. Below the md
    // breakpoint they now collapse behind a toggle instead.
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto('/');

    const toggle = page.getByRole('button', { name: 'Toggle navigation menu' });
    await expect(toggle).toBeVisible();
    // The desktop link list is present in the DOM (display:contents at
    // md+) but not visible at this width.
    await expect(page.getByRole('menu')).toHaveCount(0);

    await toggle.click();
    const menu = page.getByRole('menu');
    await expect(menu).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Screener' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Watchlist' })).toBeVisible();

    await menu.getByRole('menuitem', { name: 'Watchlist' }).click();
    await expect(page).toHaveURL('/watchlist');
  });

  test('idle hero offers a one-click sample report for skeptical first-time visitors', async ({ page }) => {
    const symbol = 'TCS';
    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({ status: 200, headers: SSE_HEADERS, body: sseAnalysisBody(symbol) }));
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: { symbol, insider_trades: [], bulk_block_deals: [] },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    await page.getByRole('button', { name: /See a real report for TCS/ }).click();

    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible({ timeout: 15000 });
  });

  test('runs a full mocked stock analysis and renders the verdict', async ({ page }) => {
    const symbol = 'TCS';

    await page.route(`**/api/validate/${symbol}`, route =>
      route.fulfill({ json: validationResult(symbol) }));

    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({ status: 200, headers: SSE_HEADERS, body: sseAnalysisBody(symbol) }));

    // Standalone add-on cards fetched independently after the report loads —
    // let them resolve to an explicit empty/graceful shape rather than
    // falling through to a real (absent) backend, so the test isn't
    // dependent on the Next.js proxy's unavailable-backend fallback timing.
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: { symbol, insider_trades: [], bulk_block_deals: [] },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    const input = page.getByLabel('NSE or BSE stock ticker');
    await input.fill(symbol);
    // "Symbol found" also exists as an sr-only live-region announcement, but
    // the actual visible confirmation is the company-name/exchange row that
    // ticker-search.tsx renders once validation resolves — wait for that.
    await expect(page.getByText(validationResult(symbol).company)).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: 'Analyse Stock' }).click();

    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(`${symbol} Limited`).first()).toBeVisible();

    // The nav bar is visible on the idle hero too now (see the test above) —
    // still present once a report has loaded.
    await expect(page.getByRole('link', { name: 'Market Picks' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Screener', exact: true })).toBeVisible();
  });

  test('formats a value just under a Cr/L boundary as the larger unit, not a false "100" of the smaller one', async ({ page }) => {
    // Regression test for an adversarial-review finding: fmtCr()/fmtInr()
    // compared the raw unrounded value against a unit threshold, but
    // formatted via toFixed(), which rounds separately. A market cap of
    // 99,998 Cr fails the ">= 1,00,000" (1L Cr) check and falls to the
    // K-Cr branch, but (99998/1000).toFixed(2) itself rounds up to
    // "100.00" -- displaying the nonsensical "₹100.00K Cr" instead of
    // "₹1.00L Cr". Same bug shape for fmtInr() at the Cr/L boundary, used
    // for insider-trade values.
    const symbol = 'TCS';

    await page.route(`**/api/validate/${symbol}`, route =>
      route.fulfill({ json: validationResult(symbol) }));
    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({
        status: 200, headers: SSE_HEADERS,
        body: sseAnalysisBody(symbol, { stockInfoOverrides: { market_cap_cr: 99998 } }),
      }));
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: {
        symbol,
        insider_trades: [{
          person: 'Fixture Promoter', category: 'Promoter', action: 'BUY',
          quantity: 1000, value: 9999960, date: '20-Jul-2026', date_iso: '2026-07-20',
        }],
        bulk_block_deals: [],
        insider_trades_unavailable: false,
        bulk_block_deals_unavailable: false,
      },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    const input = page.getByLabel('NSE or BSE stock ticker');
    await input.fill(symbol);
    await expect(page.getByText(validationResult(symbol).company)).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: 'Analyse Stock' }).click();

    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible({ timeout: 15000 });

    // fmtCr(99998) via the Market Cap key metric.
    await expect(page.getByText('₹1.00L Cr')).toBeVisible();
    await expect(page.getByText('₹100.00K Cr')).toHaveCount(0);

    // fmtInr(9999960) via the insider-trade value.
    await expect(page.getByText('₹1.0 Cr')).toBeVisible();
    await expect(page.getByText('₹100.0L')).toHaveCount(0);
  });

  test('shows the specific backend-unavailable message, not a generic "connection lost" fallback', async ({ page }) => {
    // Regression test for an adversarial-review finding: app/api/analyse/
    // [symbol]/route.ts's crafted SSE error message ("Backend unavailable.
    // Please make sure the analysis service is running.") used to be
    // returned with a non-200 HTTP status. EventSource only ever reads a
    // response body when the status is exactly 200 with a text/event-stream
    // Content-Type -- any other status makes the browser "fail the
    // connection" and fire a generic error event WITHOUT parsing the body,
    // so this specific message was unreachable; useStockAnalysis.ts's
    // onerror handler always fell back to a hardcoded generic string
    // instead. Deliberately does NOT mock /api/analyse/** -- the E2E
    // harness runs no real backend process, so the real Next.js proxy
    // route's own fetch() call genuinely fails, exercising its actual
    // error-response code rather than a browser-level mock standing in
    // for it.
    const symbol = 'TCS';
    await page.route(`**/api/validate/${symbol}`, route =>
      route.fulfill({ json: validationResult(symbol) }));

    await page.goto('/');
    const input = page.getByLabel('NSE or BSE stock ticker');
    await input.fill(symbol);
    await expect(page.getByText(validationResult(symbol).company)).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: 'Analyse Stock' }).click();

    await expect(page.getByText('Backend unavailable. Please make sure the analysis service is running.'))
      .toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Connection to server lost. Please try again.')).toHaveCount(0);
  });

  test('a stale valid symbol cannot be analysed after picking a suggestion that then errors', async ({ page }) => {
    // Regression test (adversarial-review finding): selectSuggestion() calls
    // validate() directly without first resetting validSymbol.current (unlike
    // handleChange(), which always does). If that validate() call then hit
    // the 'error' branch (a non-2xx /api/validate response), the PRIOR
    // symbol's still-truthy validSymbol.current survived into the new error
    // state -- so pressing Enter silently analysed the old symbol instead of
    // doing nothing, even though the UI showed a connection-error message
    // for a different symbol. validate() now clears validSymbol.current
    // unconditionally up front, and the Enter-key handler also requires
    // status === 'valid' as defense in depth.
    const symbol = 'TCS';
    const suggestion = 'TCSALT';

    await page.route(`**/api/validate/${symbol}`, route =>
      route.fulfill({
        json: { ...validationResult(symbol), suggestions: [{ symbol: suggestion, company: 'TCS Alt Ltd', exchange: 'NSE' }] },
      }));
    await page.route(`**/api/validate/${suggestion}`, route => route.fulfill({ status: 503, json: {} }));

    let analyseRequested = false;
    await page.route(`**/api/analyse/${symbol}**`, route => {
      analyseRequested = true;
      return route.fulfill({ status: 200, headers: SSE_HEADERS, body: sseAnalysisBody(symbol) });
    });

    await page.goto('/');
    const input = page.getByLabel('NSE or BSE stock ticker');
    await input.fill(symbol);
    await expect(page.getByText(validationResult(symbol).company)).toBeVisible({ timeout: 5000 });

    // Picking the "also try" suggestion triggers a second validate() call
    // for a DIFFERENT symbol that resolves as a connection error.
    await page.getByRole('button', { name: new RegExp(suggestion) }).click();
    await expect(page.getByText("Couldn't check this symbol — connection error.")).toBeVisible({ timeout: 5000 });

    // The old symbol's stale validSymbol.current must not fire on Enter.
    await input.press('Enter');
    await page.waitForTimeout(300);
    expect(analyseRequested).toBe(false);
    await expect(page.getByText('BUY', { exact: true })).toHaveCount(0);
  });

  test('an UNKNOWN signal renders as a dash, not a fabricated "0.00" score', async ({ page }) => {
    // Regression test: the Quant Signals card used to render every signal's
    // score via fmt(signal.score, 2) regardless of signal.value -- an
    // UNKNOWN signal (backend convention: score is always 0 when a signal
    // couldn't be computed, e.g. too little price history) rendered as
    // "0.00", visually identical to a genuinely-computed neutral score.
    // This is exactly the "missing data must never look like a real value"
    // principle this product claims to follow (see docs/backlog.md's
    // Product & UX section).
    const symbol = 'TCS';
    await page.route(`**/api/validate/${symbol}`, route =>
      route.fulfill({ json: validationResult(symbol) }));
    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({
        status: 200, headers: SSE_HEADERS,
        body: sseAnalysisBody(symbol, {
          signalsOverrides: {
            technical: { name: 'technical', value: 'UNKNOWN', score: 0, meta: {} },
            volume: { name: 'volume', value: 'STRONG_ACCUMULATION', score: 1, meta: { ratio: 3.2 } },
          },
        }),
      }));
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: { symbol, insider_trades: [], bulk_block_deals: [] },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    const input = page.getByLabel('NSE or BSE stock ticker');
    await input.fill(symbol);
    await expect(page.getByText(validationResult(symbol).company)).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: 'Analyse Stock' }).click();
    await expect(page.getByText('BUY', { exact: true }).first()).toBeVisible({ timeout: 15000 });

    // Scoped to the Quant Signals card itself -- the page legitimately
    // renders "0.00" elsewhere for unrelated real zero-valued fields (e.g.
    // a 0% dividend yield), so a page-wide assertion would be a false
    // positive on those, not on the bug this test targets. Card's title is
    // a direct <p> child of its own root <div> (dashboard-primitives.tsx),
    // so the nearest ancestor <div> of the title text IS that card.
    const quantSignalsCard = page.getByText('Quant Signals').locator('xpath=ancestor::div[1]');
    const technicalRow = quantSignalsCard.getByText('technical (UNKNOWN)');
    await expect(technicalRow).toBeVisible();
    const technicalValue = technicalRow.locator('xpath=ancestor::div[1]').getByText('—', { exact: true });
    await expect(technicalValue).toBeVisible();
    await expect(quantSignalsCard.getByText('0.00', { exact: true })).toHaveCount(0);

    // A real, non-UNKNOWN signal still shows its actual score, not a dash.
    await expect(quantSignalsCard.getByText('volume (STRONG_ACCUMULATION)')).toBeVisible();
    await expect(quantSignalsCard.getByText('1.00', { exact: true })).toBeVisible();
  });

  test('shows a degraded-analysis banner when every LLM provider failed', async ({ page }) => {
    // A full provider outage previously converged to a generic HOLD with no
    // visible signal that this wasn't a real analyst call — see crew.py's
    // run_analysis_with_fallback and the `degraded` field's own comment in
    // types/index.ts.
    const symbol = 'TCS';
    await page.route(`**/api/analyse/${symbol}**`, route =>
      route.fulfill({ status: 200, headers: SSE_HEADERS, body: sseAnalysisBody(symbol, { degraded: true }) }));
    await page.route('**/api/peers/**', route => route.fulfill({
      json: { symbol, self: null, peers: [], sector_median: null, percentiles: {}, absolute_anchor: null },
    }));
    await page.route('**/api/insider-activity/**', route => route.fulfill({
      json: { symbol, insider_trades: [], bulk_block_deals: [] },
    }));
    await page.route('**/api/street-consensus/**', route => route.fulfill({
      json: { symbol, articles: [] },
    }));
    await page.route('**/api/prices/history/**', route => route.fulfill({
      json: { symbol, exchange: 'NSE', dates: [], closes: [] },
    }));
    await page.route('**/api/verdict-history/**', route => route.fulfill({
      json: { symbol, history: [], win_rate: null, scored_count: 0 },
    }));

    await page.goto('/');
    await page.getByRole('button', { name: /See a real report for TCS/ }).click();

    await expect(page.getByText('Analysis degraded —', { exact: false })).toBeVisible({ timeout: 15000 });
  });
});
