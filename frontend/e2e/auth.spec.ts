import { test, expect } from '@playwright/test';

const USER = { id: 1, email: 'trader@example.com', tier: 'free' };

test.describe('Login page', () => {
  test('sends a magic link and shows the check-your-email confirmation', async ({ page }) => {
    await page.route('**/api/auth/request-link', route => route.fulfill({ json: { sent: true } }));

    await page.goto('/login');
    await page.getByLabel('Email address').fill('trader@example.com');
    await page.getByRole('button', { name: 'Send sign-in link' }).click();

    await expect(page.getByText('Check your email')).toBeVisible();
    await expect(page.getByText('trader@example.com')).toBeVisible();
  });

  test('shows an error message when the request fails', async ({ page }) => {
    await page.route('**/api/auth/request-link', route => route.fulfill({
      status: 429, json: { detail: 'Too many requests. Try again later.' },
    }));

    await page.goto('/login');
    await page.getByLabel('Email address').fill('trader@example.com');
    await page.getByRole('button', { name: 'Send sign-in link' }).click();

    await expect(page.getByText('Too many requests. Try again later.')).toBeVisible();
  });
});

test.describe('Magic-link verification', () => {
  test('completes sign-in on confirm click and redirects home', async ({ page }) => {
    await page.route('**/api/auth/verify?token=**', route => route.fulfill({ json: { user: USER } }));
    await page.route('**/api/auth/me', route => route.fulfill({ json: { user: USER } }));
    // /auth/verify's success path calls refreshWatchlist() and
    // refreshPositions(), which fetch the DB-backed watchlist/positions for
    // the newly-signed-in identity.
    await page.route('**/api/watchlist*', route => route.fulfill({ json: { items: [] } }));
    await page.route('**/api/positions*', route => route.fulfill({ json: { items: [] } }));

    await page.goto('/auth/verify?token=faketoken123');
    await expect(page.getByRole('button', { name: 'Complete sign-in' })).toBeVisible();
    await page.getByRole('button', { name: 'Complete sign-in' }).click();

    await expect(page).toHaveURL('/');

    // The home page's nav bar (with AuthWidget) only renders once a search
    // has started — the idle hero doesn't show it — so confirm the signed-in
    // state actually stuck by visiting a page whose nav always renders it.
    await page.goto('/watchlist');
    await expect(page.getByText(USER.email)).toBeVisible();
  });

  test('shows an error state for an invalid or expired token', async ({ page }) => {
    await page.route('**/api/auth/verify?token=**', route => route.fulfill({
      status: 400, json: { detail: 'This sign-in link is invalid or expired.' },
    }));

    await page.goto('/auth/verify?token=stale-token');
    await page.getByRole('button', { name: 'Complete sign-in' }).click();

    await expect(page.getByText('Sign-in failed')).toBeVisible();
    await expect(page.getByText('This sign-in link is invalid or expired.')).toBeVisible();
    await expect(page.getByRole('link', { name: /Request a new link/ })).toBeVisible();
  });

  test('shows a missing-token error when no token is present in the URL', async ({ page }) => {
    await page.goto('/auth/verify');

    await expect(page.getByText('Sign-in failed')).toBeVisible();
    await expect(page.getByText('Missing sign-in token.')).toBeVisible();
  });

  test('offers to claim anonymous watchlist/position data after sign-in, and claims on confirm', async ({ page }) => {
    await page.route('**/api/auth/verify?token=**', route => route.fulfill({ json: { user: USER } }));
    await page.route('**/api/auth/me', route => route.fulfill({ json: { user: USER } }));
    // The pre-verify anonymous-count check and the post-verify
    // refreshWatchlist()/refreshPositions() both hit these same GETs —
    // fulfilling with non-empty lists is what makes the claim prompt appear.
    await page.route('**/api/watchlist?client_id=**', route => route.fulfill({
      json: { items: [{ symbol: 'TCS', company: 'TCS', exchange: 'NSE', addedAt: '2026-01-01' }] },
    }));
    await page.route('**/api/positions?client_id=**', route => route.fulfill({ json: { items: [] } }));
    let claimed = false;
    await page.route('**/api/watchlist/claim', route => {
      claimed = true;
      return route.fulfill({ json: { claimed: 1, skipped_over_cap: 0, items: [] } });
    });
    await page.route('**/api/positions/claim', route => route.fulfill({ json: { claimed: 0, skipped_over_cap: 0, items: [] } }));

    await page.goto('/auth/verify?token=faketoken123');
    await page.getByRole('button', { name: 'Complete sign-in' }).click();

    await expect(page.getByText(/1 watchlist item.*from browsing anonymously/)).toBeVisible();
    await page.getByRole('button', { name: 'Claim my data' }).click();

    await expect(page).toHaveURL('/');
    expect(claimed).toBe(true);
  });

  test('skipping the claim prompt still redirects home without calling claim', async ({ page }) => {
    await page.route('**/api/auth/verify?token=**', route => route.fulfill({ json: { user: USER } }));
    await page.route('**/api/auth/me', route => route.fulfill({ json: { user: USER } }));
    await page.route('**/api/watchlist?client_id=**', route => route.fulfill({
      json: { items: [{ symbol: 'TCS', company: 'TCS', exchange: 'NSE', addedAt: '2026-01-01' }] },
    }));
    await page.route('**/api/positions?client_id=**', route => route.fulfill({ json: { items: [] } }));
    let claimCalled = false;
    await page.route('**/api/watchlist/claim', () => { claimCalled = true; });

    await page.goto('/auth/verify?token=faketoken123');
    await page.getByRole('button', { name: 'Complete sign-in' }).click();

    await expect(page.getByRole('button', { name: 'Skip' })).toBeVisible();
    await page.getByRole('button', { name: 'Skip' }).click();

    await expect(page).toHaveURL('/');
    expect(claimCalled).toBe(false);
  });
});

test.describe('API keys page', () => {
  test('prompts sign-in when logged out', async ({ page }) => {
    await page.goto('/api-keys');
    await expect(page.getByText('Sign in to create and manage API keys.')).toBeVisible();
  });

  test('creates a key and shows the raw secret exactly once', async ({ page }) => {
    await page.route('**/api/auth/me', route => route.fulfill({ json: { user: USER } }));
    await page.route('**/api/api-keys', route => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 201,
          json: { id: 1, key: 'ap_live_faketestkeyvalue', key_prefix: 'ap_live_fake', label: 'my bot', created_at: new Date().toISOString(), last_used_at: null, revoked_at: null },
        });
      }
      return route.fulfill({
        json: { keys: [], tier: 'free', usage: { calls: 0, limit: 100, window_seconds: 3600 } },
      });
    });

    await page.goto('/api-keys');
    await expect(page.getByText('No API keys yet. Create one above to get started.')).toBeVisible();

    await page.getByLabel('Label (optional)').fill('my bot');
    await page.getByRole('button', { name: 'Create key' }).click();

    await expect(page.getByText("Copy this key now — it won't be shown again.")).toBeVisible();
    await expect(page.getByText('ap_live_faketestkeyvalue')).toBeVisible();
  });

  test('is reachable from the primary nav, even when signed out', async ({ page }) => {
    await page.goto('/watchlist');
    await page.getByRole('link', { name: 'API Keys' }).click();
    await expect(page).toHaveURL('/api-keys');
  });

  test('free-tier usage links to the pricing page', async ({ page }) => {
    await page.route('**/api/auth/me', route => route.fulfill({ json: { user: USER } }));
    await page.route('**/api/api-keys', route => route.fulfill({
      json: { keys: [], tier: 'free', usage: { calls: 10, limit: 100, window_seconds: 3600 } },
    }));

    await page.goto('/api-keys');
    await expect(page.getByRole('link', { name: 'Pricing' })).toBeVisible();
    await page.getByRole('link', { name: 'Pricing' }).click();
    await expect(page).toHaveURL('/pricing');
  });
});

test.describe('Pricing page', () => {
  test('shows both tiers and is honest about there being no self-serve checkout', async ({ page }) => {
    await page.goto('/pricing');

    await expect(page.getByRole('heading', { name: 'Free' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Pro' })).toBeVisible();
    await expect(page.getByText('100', { exact: true })).toBeVisible();
    await expect(page.getByText('1,000', { exact: true })).toBeVisible();
    await expect(page.getByText(/no self-serve checkout/)).toBeVisible();
    await expect(page.getByRole('link', { name: 'API Keys' })).toBeVisible();
  });
});
