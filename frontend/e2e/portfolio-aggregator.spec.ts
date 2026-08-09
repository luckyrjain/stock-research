import { test, expect } from '@playwright/test';
import { expectNoA11yViolations } from './fixtures';

const PROFILE = { id: 1, name: 'Household' };

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
});
