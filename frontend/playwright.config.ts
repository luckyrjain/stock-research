import { defineConfig, devices } from '@playwright/test';

// E2E suite covering core frontend flows. Deliberately mocks every backend
// response at the browser network layer (page.route()) rather than running
// the real FastAPI backend — this repo's existing CI never makes live
// external calls (see tests/*.py's own "no external calls made" convention),
// and a real run would mean live NSE/yfinance/Screener.in scraping on every
// PR, which is exactly the flakiness that convention avoids. Anything NOT
// explicitly mocked in a test falls through to the Next.js proxy routes'
// existing "backend unavailable" 503 handling (no FastAPI process is running
// in this job at all), which the frontend already renders gracefully.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:3000',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:3000',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
