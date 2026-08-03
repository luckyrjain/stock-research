#!/usr/bin/env node
// Captures the README's docs/screenshots/*.png from a live local instance.
//
// Prereqs: `make dev` running in another terminal (backend on :8000,
// frontend on :3000). For a genuine completed analysis (not the safe HOLD
// fallback), the backend also needs a configured LLM provider key and real
// network access to NSE/Screener.in/yfinance/Google News.
//
// Usage:  node frontend/scripts/capture-screenshots.js
// (or via the wrapper: scripts/update-screenshots.sh)

const path = require('path');
const { chromium } = require('@playwright/test');

const OUT_DIR = path.join(__dirname, '..', '..', 'docs', 'screenshots');
const BASE_URL = process.env.SCREENSHOT_BASE_URL || 'http://localhost:3000';
const VIEWPORT = { width: 1440, height: 2200 };
const ANALYSIS_SYMBOL = process.env.SCREENSHOT_SYMBOL || 'TCS';
const ANALYSIS_TIMEOUT_MS = Number(process.env.SCREENSHOT_ANALYSIS_TIMEOUT_MS || 150_000);

// Static pages: { name, path, waitFor } — waitFor is a Playwright selector
// string that must appear before the shot is taken.
const STATIC_PAGES = [
  { name: 'home', path: '/', waitFor: 'text=Analyse Stock' },
  { name: 'market-picks', path: '/market-picks', waitFor: 'text=Top Indian Stocks' },
  { name: 'screener', path: '/screener', waitFor: 'text=Stock Screener' },
];

// Clips a screenshot to the real rendered content inside <main>, not the
// full min-h-screen wrapper — every page's <main> carries `min-h-screen`
// (see frontend/CLAUDE.md's design-system note), so a naive full-viewport
// or fullPage shot on a short page (e.g. an empty Screener/Watchlist state)
// captures a lot of blank background below the real content. This instead
// measures the bottom edge of every element actually inside <main> and
// clips there, so short and tall pages both get a tight screenshot with no
// hand-tuned per-page height.
async function screenshotMainContent(page, outPath) {
  const contentBottom = await page.evaluate(() => {
    const main = document.querySelector('main') || document.body;
    let maxBottom = 0;
    for (const el of main.querySelectorAll('*')) {
      const rect = el.getBoundingClientRect();
      if (rect.bottom > maxBottom) maxBottom = rect.bottom;
    }
    return Math.ceil(maxBottom);
  });
  const height = Math.max(200, Math.min(contentBottom + 24, VIEWPORT.height));
  await page.screenshot({ path: outPath, clip: { x: 0, y: 0, width: VIEWPORT.width, height } });
}

async function captureStaticPages(page) {
  for (const { name, path: urlPath, waitFor } of STATIC_PAGES) {
    await page.goto(`${BASE_URL}${urlPath}`, { waitUntil: 'networkidle' });
    await page.waitForSelector(waitFor, { timeout: 30_000 });
    await page.waitForTimeout(500); // let any load-in animation settle
    const outPath = path.join(OUT_DIR, `${name}.png`);
    await screenshotMainContent(page, outPath);
    console.log(`captured ${name}.png`);
  }
}

// Captures the stock-analysis flow. If a completed BUY/HOLD/SELL verdict
// renders within the timeout, saves analysis-report.png (the real deal —
// needs a configured LLM key + live network access to the six data
// sources). Otherwise falls back to analysis-progress.png, the same
// in-flight "fetching data" state this repo shipped before live capture
// was possible in every environment.
async function captureAnalysis(page) {
  await page.goto(`${BASE_URL}/?symbol=${encodeURIComponent(ANALYSIS_SYMBOL)}`, {
    waitUntil: 'domcontentloaded',
  });
  await page.waitForTimeout(2_000); // let the SSE stream open and the progress tracker mount

  // Exact-text match (not substring) — several progress-tracker labels
  // ("MF Holdings", "Shareholding") contain "hold" as a substring, which a
  // loose /BUY|HOLD|SELL/ regex would match before the real report loads.
  const verdictSelector = 'text=/^(BUY|SELL|HOLD)$/';
  const completed = await page
    .waitForSelector(verdictSelector, { timeout: ANALYSIS_TIMEOUT_MS })
    .then(() => true)
    .catch(() => false);

  await page.waitForTimeout(500);

  if (completed) {
    const outPath = path.join(OUT_DIR, 'analysis-report.png');
    await screenshotMainContent(page, outPath);
    console.log(`captured analysis-report.png (${ANALYSIS_SYMBOL})`);
    console.log(
      '  NOTE: check whether the "Analysis degraded" banner is showing — that means no ' +
      'LLM provider was reachable and this is the safe HOLD fallback, not a real analyst call. ' +
      'Re-run with a configured LLM key for a genuine result.',
    );
  } else {
    const outPath = path.join(OUT_DIR, 'analysis-progress.png');
    await screenshotMainContent(page, outPath);
    console.log(
      `captured analysis-progress.png (analysis for ${ANALYSIS_SYMBOL} did not complete within ` +
      `${Math.round(ANALYSIS_TIMEOUT_MS / 1000)}s — captured the in-progress state instead)`,
    );
  }
}

(async () => {
  // Normally left unset — chromium.launch() auto-locates the browser
  // `npx playwright install chromium` installed. Override only for an
  // environment with a non-standard browser layout (see this repo's own
  // sandbox note in the top-level CLAUDE.md-adjacent docs, if any).
  const launchOpts = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
    : {};
  const browser = await chromium.launch(launchOpts);
  const page = await browser.newPage({ viewport: VIEWPORT });
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log('  console error:', msg.text());
  });

  try {
    await captureStaticPages(page);
    await captureAnalysis(page);
  } finally {
    await browser.close();
  }

  console.log(`\nDone. Review the images in ${OUT_DIR} before committing.`);
})().catch((err) => {
  console.error(err);
  console.error(
    '\nIf this is "executable doesn\'t exist", run `npx playwright install chromium` in ' +
    'frontend/ first. Otherwise make sure `make dev` is running (backend on :8000, frontend on :3000).',
  );
  process.exit(1);
});
