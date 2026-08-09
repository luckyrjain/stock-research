import { test, expect } from '@playwright/test';
import { expectNoA11yViolations } from './fixtures';

const PROFILE = { id: 1, name: 'Household' };
const ACCOUNT = { id: 1, profile_id: 1, name: 'HDFC Savings', institution: null, type: 'bank' };

test.describe('Net Worth (portfolio aggregator)', () => {
  test('has no axe violations once a profile is selected', async ({ page }) => {
    await page.route('**/api/portfolio/profiles', route => {
      if (route.request().method() === 'POST') {
        return route.fulfill({ json: PROFILE });
      }
      return route.fulfill({ json: { profiles: [PROFILE] } });
    });
    await page.route('**/api/portfolio/accounts?profile_id=1', route => route.fulfill({ json: { accounts: [] } }));
    await page.route('**/api/portfolio/networth?profile_id=1', route => route.fulfill({
      json: { total: 0, by_type: {}, by_account: [] },
    }));
    await page.route('**/api/portfolio/broker/connections?profile_id=1', route => route.fulfill({ json: { connections: [] } }));

    await page.goto('/portfolio-aggregator');
    await page.getByRole('button', { name: PROFILE.name }).click();

    await expect(page.getByText('No accounts yet — add one above.')).toBeVisible();
    await expectNoA11yViolations(page);
  });

  test('editing an asset value shows the latest refreshed number, not the value at mount', async ({ page }) => {
    // Regression test for a fix where AssetRow's edit input seeded its
    // value once at mount and never resynced — a background "Refresh
    // valuations" updating the displayed number left the edit box showing
    // a stale figure, silently reverting a just-refreshed value on save.
    let assetValue = 100000;
    await page.route('**/api/portfolio/profiles', route => {
      if (route.request().method() === 'POST') return route.fulfill({ json: PROFILE });
      return route.fulfill({ json: { profiles: [PROFILE] } });
    });
    await page.route('**/api/portfolio/accounts?profile_id=1', route => route.fulfill({ json: { accounts: [ACCOUNT] } }));
    await page.route('**/api/portfolio/assets?account_id=1', route => route.fulfill({
      json: {
        assets: [{
          id: 1, account_id: 1, type: 'cash', name: 'Emergency Fund', symbol: null, meta: {},
          archived: false, units: null, avg_cost: null, value: assetValue, valued_on: '2026-08-01',
        }],
      },
    }));
    await page.route('**/api/portfolio/networth?profile_id=1', route => route.fulfill({
      json: { total: assetValue, by_type: { cash: assetValue }, by_account: [{ account_id: 1, account_name: ACCOUNT.name, value: assetValue }] },
    }));
    await page.route('**/api/portfolio/broker/connections?profile_id=1', route => route.fulfill({ json: { connections: [] } }));
    await page.route('**/api/portfolio/refresh-valuations', route => {
      assetValue = 150000;
      return route.fulfill({ json: { valued: 1, skipped: 0 } });
    });

    await page.goto('/portfolio-aggregator');
    await page.getByRole('button', { name: PROFILE.name }).click();

    await expect(page.getByText('Emergency Fund')).toBeVisible();
    // Net worth total and the asset row both render the same formatted
    // figure when there's only one asset — .first() sidesteps the
    // strict-mode ambiguity, this assertion is just confirming the initial
    // value loaded, the edit-input check below is the actual regression test.
    await expect(page.getByText('₹1.0L', { exact: false }).first()).toBeVisible();

    await page.getByRole('button', { name: 'Refresh valuations' }).click();
    await expect(page.getByText('Valued 1, skipped 0.')).toBeVisible();
    await expect(page.getByText('₹1.5L', { exact: false }).first()).toBeVisible();

    await page.getByRole('button', { name: 'edit' }).click();
    await expect(page.locator('input[type="number"]')).toHaveValue('150000');
  });
});
