'use client';

import { useEffect, useState, useCallback, useRef, type ReactNode } from 'react';
import Link from 'next/link';
import PageShell from '@/components/page-shell';
import { Skeleton } from '@/components/data-table-ui';
import { ErrorBanner, SpinIcon } from '@/components/error-banner';
import { usePositions } from '@/lib/positions';
import { getClientId } from '@/lib/watchlist';
import { useToast } from '@/components/toast';
import { fmtInr, fmtTimestampIST } from '@/lib/format';
import { useBrokerSyncStatus, syncBroker } from '@/lib/use-broker-sync-status';
import type {
  PortfolioProfile, PortfolioAccount, PortfolioAccountType,
  PortfolioAsset, PortfolioAssetType, PortfolioNetWorth,
  CasImportResult, CsvPreviewResult, CsvImportResult,
  BrokerConnection,
} from '@/types';

const CSV_MAPPING_KEY_PREFIX = 'portfolio_csv_mapping:';
const CSV_REQUIRED_FIELDS = ['date', 'symbol', 'side', 'quantity', 'price'] as const;
const CSV_ALL_FIELDS = [...CSV_REQUIRED_FIELDS, 'amount', 'isin'] as const;

const PROFILE_KEY = 'portfolio_aggregator_profile_id';

// Brokers supported today (routes/portfolio_aggregator.py's
// _SUPPORTED_BROKERS) — each redirect-based broker's login has no way to
// echo custom state back, so the account + broker being connected are
// stashed here right before the browser leaves for the broker's login
// page, and read back by the shared broker-callback page. HDFC Securities
// never redirects at all (see HdfcBrokerRow below) so it never touches
// this key.
//
// Paytm Money is marked experimental: its exact REST shape (endpoints,
// field names, checksum scheme) was inferred from public docs/SDK
// snippets, not verified against a live response (see backend/portfolio/
// paytm_sync.py's own module docstring) — unlike Zerodha (confirmed
// against the installed kiteconnect package's real API surface) and HDFC
// Securities (its real login flow and holdings endpoint were confirmed
// against a live account; only the tradebook/trade-sync endpoint is still
// an inferred guess — see backend/portfolio/hdfc_sync.py's own module
// docstring). Remove Paytm's flag once it's completed one successful live
// sync against a real account (docs/backlog.md).
const SUPPORTED_BROKERS: { id: string; label: string; experimental?: boolean }[] = [
  { id: 'zerodha', label: 'Zerodha' },
  { id: 'hdfc_securities', label: 'HDFC Securities' },
  { id: 'paytm_money', label: 'Paytm Money', experimental: true },
];
const PENDING_BROKER_CONNECT_KEY = 'portfolio_pending_broker_connect';

const ACCOUNT_TYPES: PortfolioAccountType[] = ['bank', 'broker', 'amc', 'epfo', 'other'];
const ASSET_TYPES: PortfolioAssetType[] = ['mf', 'stock', 'fd', 'epf', 'ppf', 'cash', 'manual', 'loan'];
const SECURITY_TYPES = new Set(['mf', 'stock']);

// A success/failure status message must not render identically regardless
// of outcome (design.md's five-states rule, state 4 — "never a silent failure", extended
// here to "never an indistinguishable one"). The success/neutral cases are
// a small, fixed, enumerable set of literal strings this file itself
// produces; every caught exception message or backend-supplied error string
// (last_sync_error, e.message) is an error by default — which is correct at
// every call site: those strings only ever come from a caught exception or
// a backend-reported sync failure, never a success path.
function msgTone(m: string): 'success' | 'neutral' | 'error' {
  if (m === 'Connected.' || m === 'Syncing…' || m.startsWith('Synced ') || m.startsWith('Valued ')) return 'success';
  if (m.startsWith('Enter ')) return 'neutral';
  return 'error';
}
const MSG_TONE_CLASS: Record<ReturnType<typeof msgTone>, string> = {
  success: 'text-buy', neutral: 'text-muted', error: 'text-sell',
};

// Every Portfolio Aggregator profile/account/asset row is now owned by this
// browser's client_id (or, once signed in, the account — the proxy below
// forwards the session cookie the same way the watchlist/positions proxies
// do). Every call in this file goes through this one helper, so injecting
// client_id here — as a query param (what the GET/DELETE endpoints read)
// and merged into a JSON body (what the POST/PATCH endpoints read) — covers
// every endpoint without threading it through each call site by hand.
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const clientId = getClientId();
  const sep = path.includes('?') ? '&' : '?';
  let body = init?.body;
  if (typeof body === 'string') {
    try {
      const parsed = JSON.parse(body) as Record<string, unknown>;
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && parsed.client_id === undefined) {
        body = JSON.stringify({ ...parsed, client_id: clientId });
      }
    } catch {
      // Not a JSON body — leave untouched.
    }
  }
  const res = await fetch(`/api/portfolio/${path}${sep}client_id=${encodeURIComponent(clientId)}`, {
    ...init,
    body,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  const responseBody = await res.json();
  if (!res.ok) throw new Error(responseBody?.detail ?? `Request failed (${res.status})`);
  return responseBody as T;
}

function ProfilePicker({ onSelect }: { onSelect: (p: PortfolioProfile) => void }) {
  const [profiles, setProfiles] = useState<PortfolioProfile[] | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  // A failed fetch must not render identically to "you genuinely have zero
  // profiles" (design.md's five-states rule, state 4) — `profiles` stays null on failure so
  // the misleading empty state below never renders; this is what does.
  const [loadError, setLoadError] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    setLoadError(null);
    api<{ profiles: PortfolioProfile[] }>('profiles')
      .then(d => setProfiles(d.profiles))
      .catch(e => setLoadError(e instanceof Error ? e.message : 'Could not load your profiles.'));
  }, []);
  useEffect(load, [load]);

  async function create() {
    if (!name.trim() || creating) return;
    setError(null);
    setCreating(true);
    try {
      const p = await api<PortfolioProfile>('profiles', { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
      setName('');
      load();
      onSelect(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create profile');
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="max-w-md mx-auto mt-12 bg-card border border-border rounded-xl p-6">
      <h1 className="text-lg font-bold text-tx mb-1">Net Worth</h1>
      <p className="text-sm text-muted mb-5">Pick a profile to continue, or create a new one.</p>
      {loadError ? (
        <ErrorBanner message={loadError} className="mb-5" onRetry={load} />
      ) : profiles === null ? (
        <div className="flex flex-col gap-2 mb-5" aria-busy="true">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full rounded-lg" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-2 mb-5">
          {profiles.map(p => (
            <button
              key={p.id}
              onClick={() => onSelect(p)}
              className="text-left px-4 py-2 rounded-lg border border-border bg-surface hover:border-accent text-sm text-tx transition-colors"
            >
              {p.name}
            </button>
          ))}
          {profiles.length === 0 && <p className="text-sm text-muted">No profiles yet.</p>}
        </div>
      )}
      <div className="flex gap-2">
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && create()}
          placeholder="New profile name"
          className="flex-1 px-3 py-2 rounded-lg border border-border bg-surface text-sm text-tx"
        />
        <button onClick={create} disabled={creating} className="px-4 py-2 rounded-lg bg-accent text-bg text-sm font-semibold disabled:opacity-50">
          {creating ? 'Creating…' : 'Create'}
        </button>
      </div>
      {error && <p className="text-sm text-sell mt-2">{error}</p>}
    </div>
  );
}

// Visible micro-label above a compact form input, so these dense inline
// add-account/add-asset forms don't rely on placeholder text as the only
// label (placeholder-as-label disappears once typed into and isn't
// announced consistently by screen readers).
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-[10px] font-semibold text-muted uppercase tracking-wide">
      {label}
      {children}
    </label>
  );
}

function AddAccountForm({ profileId, onAdded }: { profileId: number; onAdded: () => void }) {
  const [name, setName] = useState('');
  const [type, setType] = useState<PortfolioAccountType>('bank');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (!name.trim() || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await api('accounts', { method: 'POST', body: JSON.stringify({ profile_id: profileId, name: name.trim(), type }) });
      setName('');
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add account');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-wrap gap-2 items-end">
      <Field label="Account name">
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="e.g. HDFC Savings"
          className="px-3 py-2 rounded-lg border border-border bg-surface text-sm text-tx"
        />
      </Field>
      <Field label="Type">
        <select value={type} onChange={e => setType(e.target.value as PortfolioAccountType)}
          className="px-3 py-2 rounded-lg border border-border bg-surface text-sm text-tx">
          {ACCOUNT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </Field>
      <button onClick={submit} disabled={submitting} className="px-4 py-2 rounded-lg bg-accent text-bg text-sm font-semibold disabled:opacity-50">
        {submitting ? 'Adding…' : 'Add account'}
      </button>
      {error && <span className="text-sm text-sell">{error}</span>}
    </div>
  );
}

function AddAssetForm({ accountId, onAdded }: { accountId: number; onAdded: () => void }) {
  const [name, setName] = useState('');
  const [type, setType] = useState<PortfolioAssetType>('cash');
  const [value, setValue] = useState('');
  const [units, setUnits] = useState('');
  const [symbol, setSymbol] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const isSecurity = SECURITY_TYPES.has(type);

  async function submit() {
    const v = parseFloat(value);
    if (!name.trim() || Number.isNaN(v) || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await api('assets', {
        method: 'POST',
        body: JSON.stringify({
          account_id: accountId, type, name: name.trim(), value: v,
          symbol: isSecurity && symbol.trim() ? symbol.trim().toUpperCase() : null,
          units: isSecurity && units ? parseFloat(units) : null,
        }),
      });
      setName(''); setValue(''); setUnits(''); setSymbol('');
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add asset');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-wrap gap-2 items-end pl-4">
      <Field label="Type">
        <select value={type} onChange={e => setType(e.target.value as PortfolioAssetType)}
          className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx">
          {ASSET_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </Field>
      <Field label="Name">
        <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. TCS shares"
          className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx w-32" />
      </Field>
      {isSecurity && (
        <>
          <Field label="Symbol">
            <input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="TCS"
              className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx w-20" />
          </Field>
          <Field label="Units">
            <input value={units} onChange={e => setUnits(e.target.value)} placeholder="10" type="number"
              className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx w-20" />
          </Field>
        </>
      )}
      <Field label="Value ₹">
        <input value={value} onChange={e => setValue(e.target.value)} placeholder="0" type="number"
          className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx w-28" />
      </Field>
      <button onClick={submit} disabled={submitting} className="px-3 py-1.5 rounded-lg border border-accent text-accent text-xs font-semibold disabled:opacity-50">
        {submitting ? 'Adding…' : 'Add asset'}
      </button>
      {error && <span className="text-xs text-sell">{error}</span>}
    </div>
  );
}

function AssetRow({ asset, onChanged }: { asset: PortfolioAsset; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(asset.value ?? ''));
  const [saving, setSaving] = useState(false);
  const { isPositioned } = usePositions();
  const alsoTracked = asset.symbol ? isPositioned(asset.symbol) : false;
  const { showError } = useToast();

  // Reseeds from the live prop, not just at mount — this row stays mounted
  // across every refresh() (keyed by asset.id), so a background valuation
  // refresh updating the displayed value one row up must not leave a stale
  // number sitting in an edit box the user hasn't opened yet.
  function startEditing() {
    setValue(String(asset.value ?? ''));
    setEditing(true);
  }

  async function saveValue() {
    const v = parseFloat(value);
    if (Number.isNaN(v)) return;
    setSaving(true);
    try {
      await api(`assets/${asset.id}/valuations`, { method: 'POST', body: JSON.stringify({ value: v }) });
      setEditing(false);
      onChanged();
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not save this value.');
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!confirm(`Delete "${asset.name}"? This can't be undone.`)) return;
    try {
      await api(`assets/${asset.id}`, { method: 'DELETE' });
      onChanged();
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not delete this asset.');
    }
  }

  const signed = asset.type === 'loan' ? -(asset.value ?? 0) : (asset.value ?? 0);

  return (
    <div className="flex items-center justify-between py-1.5 pl-4 border-b border-border last:border-0 text-sm">
      <span className="text-tx">
        {asset.name}
        {asset.symbol && <span className="text-muted"> ({asset.symbol})</span>}
        <span className="text-muted"> · {asset.type}</span>
        {alsoTracked && (
          <Link href="/portfolio" className="ml-2 text-[10px] font-semibold text-accent hover:underline">
            also in Positions →
          </Link>
        )}
      </span>
      {editing ? (
        <span className="flex items-center gap-1">
          <input value={value} onChange={e => setValue(e.target.value)} type="number" disabled={saving}
            className="w-24 px-2 py-1 rounded border border-border bg-bg text-xs text-tx disabled:opacity-50" autoFocus />
          <button onClick={saveValue} disabled={saving} className="text-xs text-accent font-semibold disabled:opacity-50">
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button onClick={() => setEditing(false)} disabled={saving} className="text-xs text-muted disabled:opacity-50">Cancel</button>
        </span>
      ) : (
        <span className="flex items-center gap-2">
          <span className={`font-mono font-semibold ${asset.type === 'loan' ? 'text-sell' : 'text-tx'}`}>
            {asset.type === 'loan' ? '−' : ''}{fmtInr(Math.abs(signed))}
          </span>
          <button onClick={startEditing} className="text-xs text-muted hover:text-tx">edit</button>
          <button onClick={remove} className="text-xs text-muted hover:text-sell">delete</button>
        </span>
      )}
    </div>
  );
}

// HDFC Securities' real login has no browser redirect at all — the app
// itself collects the HDFC username/password and relays an OTP, in two
// steps against POST .../login-start then POST .../verify-otp (see
// backend/portfolio/hdfc_sync.py's module docstring for the full 5-step
// flow this drives). Deliberately a separate component from BrokerRow
// rather than a shared one with a redirect/no-redirect branch — the two
// flows share almost no state shape (a URL to navigate to vs. two
// sequential forms) and forcing one component to cover both would obscure
// more than it'd reuse.
function HdfcBrokerRow({ account, connection, onSynced, onPoll }: {
  account: PortfolioAccount; connection: BrokerConnection | undefined; onSynced: () => void; onPoll: () => void;
}) {
  const label = 'HDFC Securities';
  const { busy, setBusy, msg, setMsg, syncing } = useBrokerSyncStatus(connection, onSynced, onPoll);
  const [showCreds, setShowCreds] = useState(!connection);
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  // Set once login-start succeeds — switches the form to the OTP step.
  // Reset (back to the credentials form) whenever the account/connection
  // identity changes, so a stale in-progress login from a different
  // account/connection can never bleed into this one.
  const [otpRequired, setOtpRequired] = useState(false);
  const [otp, setOtp] = useState('');

  useEffect(() => {
    setOtpRequired(false);
    setOtp('');
  }, [account.id, connection?.id]);

  async function loginStart() {
    setBusy(true);
    setMsg(null);
    try {
      if (showCreds && (!apiKey.trim() || !apiSecret.trim())) {
        setMsg('Enter both API key and API secret.');
        setBusy(false);
        return;
      }
      if (!username.trim() || !password.trim()) {
        setMsg('Enter your HDFC Securities username and password.');
        setBusy(false);
        return;
      }
      const body: { account_id: number; api_key?: string; api_secret?: string; username: string; password: string } = {
        account_id: account.id, username: username.trim(), password,
      };
      if (showCreds) {
        body.api_key = apiKey.trim();
        body.api_secret = apiSecret.trim();
      }
      await api<{ otp_required: true }>('broker/hdfc_securities/login-start', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      setPassword('');
      setOtpRequired(true);
      setMsg('Enter the OTP HDFC just sent you.');
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Could not start HDFC Securities login');
    } finally {
      setBusy(false);
    }
  }

  async function verifyOtp() {
    setBusy(true);
    setMsg(null);
    try {
      if (!otp.trim()) {
        setMsg('Enter the OTP.');
        setBusy(false);
        return;
      }
      await api<{ connected: boolean }>('broker/hdfc_securities/verify-otp', {
        method: 'POST',
        body: JSON.stringify({ account_id: account.id, otp: otp.trim() }),
      });
      setOtp('');
      setOtpRequired(false);
      setShowCreds(false);
      setMsg('Connected.');
      onSynced();
    } catch (e) {
      // A failed OTP clears the backend's pending_token_id (single-use) —
      // the user has to restart from login-start, not just retry the OTP.
      setOtpRequired(false);
      setMsg(e instanceof Error ? e.message : 'OTP verification failed — try connecting again');
    } finally {
      setBusy(false);
    }
  }

  async function sync() {
    await syncBroker('hdfc_securities', account.id, api, setBusy, setMsg, onSynced);
  }

  return (
    <span className="flex flex-col gap-1">
      <span className="flex items-center gap-2 flex-wrap">
        {connection?.connected && (
          <span className="text-xs text-muted">
            {label} connected{connection.last_synced_at ? ` · last synced ${fmtTimestampIST(connection.last_synced_at)}` : ' · never synced'}
          </span>
        )}
        {connection && !connection.connected && (
          <span className="text-xs text-muted">{label}: API key saved, not yet connected</span>
        )}
        {connection?.connected && (
          <button onClick={sync} disabled={syncing} className="text-xs text-accent font-semibold disabled:opacity-50 flex items-center gap-1.5">
            {syncing && <SpinIcon />}
            {syncing ? 'Syncing…' : 'Sync now'}
          </button>
        )}
        {/* `connection.connected` only means a token was obtained once —
            it stays true after the token expires (see sync_status/
            last_sync_error instead), so "Reconnect" must not be gated on
            it alone or it hides exactly when it's needed most: right
            after a sync fails with an expired/invalid token. */}
        {!otpRequired && (!connection || !connection.connected || connection.sync_status === 'error' || showCreds) && (
          <button onClick={loginStart} disabled={busy} className="text-xs text-accent font-semibold disabled:opacity-50">
            {busy ? 'Connecting…' : connection?.connected ? `Reconnect ${label}` : `Connect ${label}`}
          </button>
        )}
        {connection && !showCreds && !otpRequired && (
          <button onClick={() => setShowCreds(true)} className="text-xs text-muted hover:text-tx">
            change API key
          </button>
        )}
        {msg && <span role="status" aria-live="polite" className={`text-xs ${MSG_TONE_CLASS[msgTone(msg)]}`}>{msg}</span>}
      </span>
      {!otpRequired && (showCreds || !connection || !connection.connected || connection.sync_status === 'error') && (
        <span className="flex items-center gap-2 flex-wrap">
          {showCreds && (
            <>
              <input
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="HDFC Securities API key"
                className="px-2 py-1 rounded border border-border bg-bg text-xs text-tx w-40"
              />
              <input
                value={apiSecret}
                onChange={e => setApiSecret(e.target.value)}
                placeholder="API secret"
                type="password"
                className="px-2 py-1 rounded border border-border bg-bg text-xs text-tx w-40"
              />
            </>
          )}
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="HDFC username"
            className="px-2 py-1 rounded border border-border bg-bg text-xs text-tx w-32"
          />
          <input
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="HDFC password"
            type="password"
            className="px-2 py-1 rounded border border-border bg-bg text-xs text-tx w-32"
          />
          {connection && showCreds && (
            <button onClick={() => setShowCreds(false)} className="text-xs text-muted hover:text-tx">
              cancel
            </button>
          )}
        </span>
      )}
      {otpRequired && (
        <span className="flex items-center gap-2">
          <input
            value={otp}
            onChange={e => setOtp(e.target.value)}
            placeholder="OTP"
            inputMode="numeric"
            className="px-2 py-1 rounded border border-border bg-bg text-xs text-tx w-24"
          />
          <button onClick={verifyOtp} disabled={busy} className="text-xs text-accent font-semibold disabled:opacity-50">
            {busy ? 'Verifying…' : 'Verify OTP'}
          </button>
        </span>
      )}
    </span>
  );
}

function BrokerRow({ account, broker, connection, onSynced, onPoll }: {
  account: PortfolioAccount; broker: { id: string; label: string; experimental?: boolean };
  connection: BrokerConnection | undefined; onSynced: () => void; onPoll: () => void;
}) {
  const { busy, setBusy, msg, setMsg, syncing } = useBrokerSyncStatus(connection, onSynced, onPoll);
  // Credential form starts open until an app has been registered for this
  // (account, broker) — after that, "Connect"/"Reconnect" reuses the saved
  // key/secret (see BrokerLoginUrlRequest) and this stays collapsed behind
  // an explicit "change API key" link, so a normal resume/retry never
  // forces re-typing a secret.
  const [showCreds, setShowCreds] = useState(!connection);
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');

  async function connect() {
    setBusy(true);
    setMsg(null);
    try {
      const body: { account_id: number; api_key?: string; api_secret?: string } = { account_id: account.id };
      if (showCreds) {
        if (!apiKey.trim() || !apiSecret.trim()) {
          setMsg('Enter both API key and API secret.');
          setBusy(false);
          return;
        }
        body.api_key = apiKey.trim();
        body.api_secret = apiSecret.trim();
      }
      // A DIFFERENT, still-unresolved connect attempt already parked here
      // (started in another tab, not yet completed) must not be silently
      // clobbered — the shared callback route has no way to tell two
      // attempts apart once overwritten (neither broker's OAuth redirect
      // echoes back custom state, see broker-callback/page.tsx's own
      // comment), so whichever tab's redirect lands second would resume
      // against the WRONG (account_id, broker) pairing. This can't fully
      // prevent the race (nothing stops the user proceeding anyway once
      // warned), but it turns a silent wrong-account resume into a clear,
      // actionable message instead.
      const pendingRaw = localStorage.getItem(PENDING_BROKER_CONNECT_KEY);
      if (pendingRaw) {
        try {
          const pending = JSON.parse(pendingRaw) as { account_id: number; broker: string; started_at?: number };
          // A pending attempt older than a normal OAuth-login-page detour
          // is treated as abandoned (tab closed, user gave up mid-flow) and
          // safe to overwrite — without this, one abandoned attempt would
          // permanently block connecting ANY broker until localStorage is
          // cleared by hand, which is worse than the race this check exists
          // to catch. 30 min comfortably covers a slow real login+OTP flow.
          const isStale = !pending.started_at || Date.now() - pending.started_at > 30 * 60 * 1000;
          if (!isStale && (pending.account_id !== account.id || pending.broker !== broker.id)) {
            setMsg('Another broker connection is already in progress in a different tab — finish or cancel it first.');
            setBusy(false);
            return;
          }
        } catch { /* malformed leftover value — safe to overwrite below */ }
      }
      const { login_url } = await api<{ login_url: string }>(`broker/${broker.id}/login-url`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      localStorage.setItem(PENDING_BROKER_CONNECT_KEY, JSON.stringify({ account_id: account.id, broker: broker.id, started_at: Date.now() }));
      window.location.href = login_url;
    } catch (e) {
      setMsg(e instanceof Error ? e.message : `Could not start ${broker.label} login`);
      setBusy(false);
    }
  }

  async function sync() {
    await syncBroker(broker.id, account.id, api, setBusy, setMsg, onSynced);
  }

  return (
    <span className="flex flex-col gap-1">
      <span className="flex items-center gap-2 flex-wrap">
        {broker.experimental && (
          <span className="text-xs text-hold font-semibold" title="Not yet verified against a live account — see docs/backlog.md">
            beta
          </span>
        )}
        {connection?.connected && (
          <span className="text-xs text-muted">
            {broker.label} connected{connection.last_synced_at ? ` · last synced ${fmtTimestampIST(connection.last_synced_at)}` : ' · never synced'}
          </span>
        )}
        {connection && !connection.connected && (
          <span className="text-xs text-muted">{broker.label}: API key saved, not yet connected</span>
        )}
        {connection?.connected && (
          <button onClick={sync} disabled={syncing} className="text-xs text-accent font-semibold disabled:opacity-50 flex items-center gap-1.5">
            {syncing && <SpinIcon />}
            {syncing ? 'Syncing…' : 'Sync now'}
          </button>
        )}
        {/* See HdfcBrokerRow's identical comment — `connected` alone stays
            true after token expiry, so gate Reconnect on sync_status
            too or it hides right when it's needed. */}
        {(!connection || !connection.connected || connection.sync_status === 'error' || showCreds) && (
          <button onClick={connect} disabled={busy} className="text-xs text-accent font-semibold disabled:opacity-50">
            {busy ? 'Redirecting…' : connection?.connected ? `Reconnect ${broker.label}` : `Connect ${broker.label}`}
          </button>
        )}
        {connection && !showCreds && (
          <button onClick={() => setShowCreds(true)} className="text-xs text-muted hover:text-tx">
            change API key
          </button>
        )}
        {msg && <span role="status" aria-live="polite" className={`text-xs ${MSG_TONE_CLASS[msgTone(msg)]}`}>{msg}</span>}
      </span>
      {showCreds && (
        <span className="flex items-center gap-2">
          <input
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            placeholder={`${broker.label} API key`}
            className="px-2 py-1 rounded border border-border bg-bg text-xs text-tx w-40"
          />
          <input
            value={apiSecret}
            onChange={e => setApiSecret(e.target.value)}
            placeholder="API secret"
            type="password"
            className="px-2 py-1 rounded border border-border bg-bg text-xs text-tx w-40"
          />
          {connection && (
            <button onClick={() => setShowCreds(false)} className="text-xs text-muted hover:text-tx">
              cancel
            </button>
          )}
        </span>
      )}
    </span>
  );
}

// One (account, broker) pair is a real credential registration — showing
// all 3 supported brokers' full connect controls on every broker account
// regardless of which one the user actually meant was the reported "sharp
// UX" complaint: an account for one broker doesn't need the other two
// brokers' connect buttons/forms cluttering the row. Now only brokers
// already registered on this account (a `connections` row exists, whether
// or not the handshake finished — see BrokerConnection.connected's own
// comment) get their full row; anything else is behind a single "+ Connect
// broker" picker so at most one extra row is ever visible at a time.
function BrokerConnectControls({ account, connections, onSynced, onPoll }: {
  account: PortfolioAccount; connections: BrokerConnection[]; onSynced: () => void; onPoll: () => void;
}) {
  const [addingBrokerId, setAddingBrokerId] = useState<string | null>(null);
  const registeredIds = new Set(connections.map(c => c.broker));
  const registered = SUPPORTED_BROKERS.filter(b => registeredIds.has(b.id));
  const unregistered = SUPPORTED_BROKERS.filter(b => !registeredIds.has(b.id));
  const addingBroker = unregistered.find(b => b.id === addingBrokerId);

  // Deliberately does NOT clear addingBrokerId on a successful connect —
  // doing that synchronously (before the async onSynced()/refresh() has
  // actually landed the new connection in `connections`) briefly removed
  // this broker from BOTH the "adding" slot and `registered` for one
  // render, which React sees as the component disappearing entirely for
  // that render. `addingBroker` above already derives to undefined the
  // moment this broker's connection actually shows up in `registered` —
  // `unregistered` stops containing it — so no explicit clearing is
  // needed to avoid that specific one-render gap.
  //
  // Note this does NOT make the transition remount-free: the "adding" row
  // renders one tree level deeper (nested inside the bordered wrapper span
  // below) than the "registered" row (a direct child of `registered.map`
  // in the outer span) — React only matches keys within the same parent's
  // children, so the key match doesn't span that structural difference,
  // and BrokerRow/HdfcBrokerRow still remounts (losing its local "Connected."
  // message) the instant `addingBroker` flips to undefined. Harmless in
  // practice — the row immediately re-renders with the correct, permanent
  // "connected · last synced …" line either way — just not the full state
  // continuity this comment used to claim.
  function renderBroker(broker: { id: string; label: string; experimental?: boolean }) {
    const connection = connections.find(c => c.broker === broker.id);
    return broker.id === 'hdfc_securities' ? (
      <HdfcBrokerRow key={broker.id} account={account} connection={connection} onSynced={onSynced} onPoll={onPoll} />
    ) : (
      <BrokerRow key={broker.id} account={account} broker={broker} connection={connection} onSynced={onSynced} onPoll={onPoll} />
    );
  }

  return (
    <span className="flex items-center gap-3 flex-wrap">
      {registered.map(renderBroker)}
      {addingBroker ? (
        <span className="flex items-center gap-2 flex-wrap bg-surface border border-border rounded-lg px-2 py-1">
          {renderBroker(addingBroker)}
          <button onClick={() => setAddingBrokerId(null)} className="text-xs text-muted hover:text-tx">cancel</button>
        </span>
      ) : (
        unregistered.length > 0 && (
          <select
            defaultValue=""
            onChange={e => { if (e.target.value) setAddingBrokerId(e.target.value); }}
            className="text-xs text-accent font-semibold bg-transparent border border-accent/40 rounded-lg px-2 py-1"
          >
            <option value="" disabled>+ Connect broker…</option>
            {unregistered.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
        )
      )}
    </span>
  );
}

function AccountBlock({ account, assets, connections, onChanged, onPoll }: {
  account: PortfolioAccount; assets: PortfolioAsset[]; connections: BrokerConnection[]; onChanged: () => void; onPoll: () => void;
}) {
  const [showAdd, setShowAdd] = useState(false);
  const { showError } = useToast();
  // A successful add unmounts the form (and whatever input inside it had
  // focus) with nothing to take its place — without this, focus silently
  // drops to <body> and a keyboard user has to restart Tab navigation from
  // the top of the page. Returning it to the toggle button that reopens the
  // form is the closest sensible landing spot.
  const addToggleRef = useRef<HTMLButtonElement>(null);

  async function removeAccount() {
    if (!confirm(`Delete account "${account.name}"? This can't be undone.`)) return;
    try {
      await api(`accounts/${account.id}`, { method: 'DELETE' });
      onChanged();
    } catch {
      // 422 when the account still has assets — a background mutation
      // failure, so a toast (design.md's five-states rule, state 4), not a browser alert().
      showError('Delete every asset in this account first.');
    }
  }

  const subtotal = assets.reduce((s, a) => s + (a.type === 'loan' ? -(a.value ?? 0) : (a.value ?? 0)), 0);

  return (
    <div className="bg-card border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-2 pb-2 border-b border-border">
        <p className="text-sm font-semibold text-tx">
          {account.name}
          {' '}
          <span className="text-muted font-normal">· {account.type}{account.institution ? ` · ${account.institution}` : ''}</span>
          {assets.length > 0 && (
            <span className={`ml-2 font-mono ${subtotal < 0 ? 'text-sell' : 'text-muted'}`}>
              {subtotal < 0 ? '−' : ''}{fmtInr(Math.abs(subtotal))}
            </span>
          )}
        </p>
        <div className="flex items-center gap-3">
          {account.type === 'broker' && (
            <BrokerConnectControls account={account} connections={connections} onSynced={onChanged} onPoll={onPoll} />
          )}
          <button ref={addToggleRef} onClick={() => setShowAdd(s => !s)} className="text-xs text-accent font-semibold">
            {showAdd ? 'Cancel' : '+ Asset'}
          </button>
          <button onClick={removeAccount} className="text-xs text-muted hover:text-sell">delete account</button>
        </div>
      </div>
      {assets.map(a => <AssetRow key={a.id} asset={a} onChanged={onChanged} />)}
      {assets.length === 0 && <p className="text-xs text-muted pl-4 py-1">No assets yet.</p>}
      {showAdd && (
        <div className="mt-2">
          <AddAssetForm accountId={account.id} onAdded={() => { onChanged(); setShowAdd(false); addToggleRef.current?.focus(); }} />
        </div>
      )}
    </div>
  );
}

function ImportCasForm({ accounts, onImported }: { accounts: PortfolioAccount[]; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState('');
  const [accountId, setAccountId] = useState<number | ''>(accounts[0]?.id ?? '');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CasImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!file || !accountId) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('password', password);
      form.append('account_id', String(accountId));
      form.append('client_id', getClientId());
      const res = await fetch('/api/portfolio/import-cas', { method: 'POST', body: form });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? `Import failed (${res.status})`);
      setResult(body as CasImportResult);
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'CAS import failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bg-card border border-border rounded-xl p-4 mb-4">
      <p className="text-sm font-semibold text-tx mb-2">Import CAS statement</p>
      <p className="text-xs text-muted mb-3">
        CAMS/KFintech detailed (mailback) CAS PDF only — summary statements aren&apos;t supported.
      </p>
      <div className="flex flex-wrap gap-2 items-center">
        <input type="file" accept="application/pdf" onChange={e => setFile(e.target.files?.[0] ?? null)}
          className="text-xs text-tx" />
        <input type="password" value={password} onChange={e => setPassword(e.target.value)}
          placeholder="PDF password"
          className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx w-32" />
        <select value={accountId} onChange={e => setAccountId(Number(e.target.value))}
          className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx">
          {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <button onClick={submit} disabled={busy || !file || !accountId}
          className="px-3 py-1.5 rounded-lg border border-accent text-accent text-xs font-semibold disabled:opacity-50">
          {busy ? 'Importing…' : 'Import'}
        </button>
      </div>
      {error && <p className="text-xs text-sell mt-2">{error}</p>}
      {result && (
        <div className="mt-2 text-xs text-muted">
          <p>{result.assets_created} assets created, {result.assets_matched} matched, {result.transactions} transactions.</p>
          {result.warnings.map((w, i) => <p key={i} className="text-hold">⚠ {w}</p>)}
        </div>
      )}
    </div>
  );
}

function ImportCsvForm({ accounts, onImported }: { accounts: PortfolioAccount[]; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvPreviewResult | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [accountId, setAccountId] = useState<number | ''>(accounts[0]?.id ?? '');
  const [broker, setBroker] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CsvImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  function mappingKey(headers: string[]): string {
    return CSV_MAPPING_KEY_PREFIX + headers.map(h => h.toLowerCase()).join('|');
  }

  async function pickFile(f: File | null) {
    setFile(f);
    setPreview(null);
    setResult(null);
    setError(null);
    if (!f) return;
    try {
      const form = new FormData();
      form.append('file', f);
      const res = await fetch('/api/portfolio/import-csv/preview', { method: 'POST', body: form });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? `Preview failed (${res.status})`);
      const prev = body as CsvPreviewResult;
      setPreview(prev);
      if (prev.detected === 'zerodha') setBroker('zerodha');
      const cached = localStorage.getItem(mappingKey(prev.headers));
      setMapping(cached ? JSON.parse(cached) : prev.suggested_mapping);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    }
  }

  async function submit() {
    if (!file || !preview || !accountId || !broker.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('mapping', JSON.stringify(mapping));
      form.append('account_id', String(accountId));
      form.append('broker', broker.trim());
      form.append('client_id', getClientId());
      const res = await fetch('/api/portfolio/import-csv', { method: 'POST', body: form });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? `Import failed (${res.status})`);
      localStorage.setItem(mappingKey(preview.headers), JSON.stringify(mapping));
      setResult(body as CsvImportResult);
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'CSV import failed');
    } finally {
      setBusy(false);
    }
  }

  const canImport = preview && accountId && broker.trim()
    && CSV_REQUIRED_FIELDS.every(f => mapping[f]);

  return (
    <div className="bg-card border border-border rounded-xl p-4 mb-4">
      <p className="text-sm font-semibold text-tx mb-2">Import broker CSV/XLSX</p>
      <p className="text-xs text-muted mb-3">
        Stock buy/sell tradebook — Zerodha is auto-detected, other brokers map columns below.
      </p>
      <input type="file" accept=".csv,.xlsx" onChange={e => pickFile(e.target.files?.[0] ?? null)}
        className="text-xs text-tx mb-2" />
      {preview && (
        <>
          <div className="grid grid-cols-2 gap-2 mb-2">
            {CSV_ALL_FIELDS.map(field => (
              <label key={field} className="flex items-center gap-2 text-xs text-muted">
                <span className="w-16 capitalize">{field}{(CSV_REQUIRED_FIELDS as readonly string[]).includes(field) ? ' *' : ''}</span>
                <select value={mapping[field] ?? ''} onChange={e => setMapping(m => ({ ...m, [field]: e.target.value || null }))}
                  className="flex-1 px-2 py-1 rounded border border-border bg-bg text-xs text-tx">
                  <option value="">—</option>
                  {preview.headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </label>
            ))}
          </div>
          <div className="flex flex-wrap gap-2 items-center">
            <select value={accountId} onChange={e => setAccountId(Number(e.target.value))}
              className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx">
              {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
            <input value={broker} onChange={e => setBroker(e.target.value)} placeholder="Broker name"
              className="px-2 py-1.5 rounded-lg border border-border bg-bg text-xs text-tx w-32" />
            <button onClick={submit} disabled={busy || !canImport}
              className="px-3 py-1.5 rounded-lg border border-accent text-accent text-xs font-semibold disabled:opacity-50">
              {busy ? 'Importing…' : 'Import'}
            </button>
          </div>
        </>
      )}
      {error && <p className="text-xs text-sell mt-2">{error}</p>}
      {result && (
        <div className="mt-2 text-xs text-muted">
          <p>Imported {result.imported}, duplicates {result.duplicates}, skipped {result.skipped} — {result.assets_created} assets created, {result.assets_matched} matched.</p>
          {result.warnings.map((w, i) => <p key={i} className="text-hold">⚠ {w}</p>)}
        </div>
      )}
    </div>
  );
}

function ProfileView({ profile, onSwitch }: { profile: PortfolioProfile; onSwitch: () => void }) {
  const [accounts, setAccounts] = useState<PortfolioAccount[]>([]);
  const [assetsByAccount, setAssetsByAccount] = useState<Record<number, PortfolioAsset[]>>({});
  const [connections, setConnections] = useState<BrokerConnection[]>([]);
  const [networth, setNetworth] = useState<PortfolioNetWorth | null>(null);
  const [showAddAccount, setShowAddAccount] = useState(false);
  // Same "return focus to the toggle, don't drop it" reasoning as
  // AccountBlock's own addToggleRef.
  const addAccountToggleRef = useRef<HTMLButtonElement>(null);
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [showImportCas, setShowImportCas] = useState(false);
  const [showImportCsv, setShowImportCsv] = useState(false);
  // Gates the skeleton vs. "No accounts yet" empty state below — accounts
  // starts at [] same as the true empty case, so without this flag the
  // first paint reads as an already-empty profile instead of still loading.
  // Only ever flips true, never back — later refresh() calls (after adding
  // an asset, syncing, etc.) shouldn't re-flash the skeleton.
  const [loaded, setLoaded] = useState(false);
  // Separate from `loaded` above — networth is fetched independently
  // (a completely different, unawaited api() call in refresh() below) and
  // can resolve before or after the accounts+assets fetch. Reusing
  // `loaded` for the Net Worth skeleton left a window where accounts
  // finished first: loaded is true (skeleton condition false) but
  // networth is still null (data condition also false), so the whole
  // card rendered nothing for that stretch instead of skeleton or data.
  const [netWorthLoaded, setNetWorthLoaded] = useState(false);
  // A fetch failure must not render identically to a genuine "zero
  // accounts"/"zero net worth" (design.md's five-states rule, state 4) — both branches
  // below used to have no .catch() at all, so a real failure silently
  // rendered as "No accounts yet" / an empty net-worth card.
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [networthError, setNetworthError] = useState<string | null>(null);
  const [connectionsError, setConnectionsError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setAccountsError(null);
    setNetworthError(null);
    setConnectionsError(null);
    api<{ accounts: PortfolioAccount[] }>(`accounts?profile_id=${profile.id}`).then(async d => {
      setAccounts(d.accounts);
      // allSettled, not all — a single account's assets fetch failing must
      // not discard every other account's already-successful fetch (Promise.all
      // rejects the whole batch on the first failure, which would leave
      // assetsByAccount never updated at all, so every account — including
      // the ones whose fetch actually succeeded — would fall back to `?? []`
      // below and render a false "No assets yet" instead of an error).
      const results = await Promise.allSettled(
        d.accounts.map(async acc => [acc.id, (await api<{ assets: PortfolioAsset[] }>(`assets?account_id=${acc.id}`)).assets] as const),
      );
      const entries = results.filter(r => r.status === 'fulfilled').map(r => r.value);
      setAssetsByAccount(prev => ({ ...prev, ...Object.fromEntries(entries) }));
      const failed = results.filter(r => r.status === 'rejected').length;
      if (failed > 0) {
        setAccountsError(`Could not load assets for ${failed} of ${d.accounts.length} account${d.accounts.length === 1 ? '' : 's'}.`);
      }
    }).catch(e => setAccountsError(e instanceof Error ? e.message : 'Could not load your accounts.'))
      .finally(() => setLoaded(true));
    api<PortfolioNetWorth>(`networth?profile_id=${profile.id}`).then(setNetworth)
      .catch(e => setNetworthError(e instanceof Error ? e.message : 'Could not load your net worth.'))
      .finally(() => setNetWorthLoaded(true));
    // A failure here must not render identically to "no broker connected"
    // (design.md's five-states rule, state 4) — same class of bug as accounts/networth
    // above, just one that a prior audit pass over this file missed.
    api<{ connections: BrokerConnection[] }>(`broker/connections?profile_id=${profile.id}`)
      .then(d => setConnections(d.connections))
      .catch(e => setConnectionsError(e instanceof Error ? e.message : 'Could not load your broker connections.'));
  }, [profile.id]);

  // Lighter than refresh() — used by the 2s sync-status poll below, which
  // only needs the connections list (the one place sync_status lives), not
  // a full accounts+per-account-assets+networth refetch every tick. Sharing
  // refresh() for that poll blew through the "portfolio_agg_read" rate
  // limit (120/60s, shared across all these read endpoints) in well under a
  // minute with just two accounts.
  const pollConnections = useCallback(() => {
    api<{ connections: BrokerConnection[] }>(`broker/connections?profile_id=${profile.id}`)
      .then(d => setConnections(d.connections))
      .catch(() => {
        // Keeps the last good connections list rather than wiping it to []
        // (STATE-01) — this fires every 2s while a sync is active, so a
        // transient blip shouldn't make "Connected"/sync status disappear;
        // it'll self-heal on the next successful tick.
      });
  }, [profile.id]);

  useEffect(refresh, [refresh]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-bold text-tx">{profile.name}</h1>
          <p className="text-xs text-muted">Personal net worth — banks, brokers, FDs, EPF/PPF, loans.</p>
        </div>
        <div className="flex items-center gap-3">
          {refreshMsg && <span role="status" aria-live="polite" className={`text-xs ${MSG_TONE_CLASS[msgTone(refreshMsg)]}`}>{refreshMsg}</span>}
          <button
            onClick={async () => {
              setRefreshing(true);
              setRefreshMsg(null);
              try {
                const res = await api<{ valued: number; skipped: number }>('refresh-valuations', { method: 'POST' });
                setRefreshMsg(`Valued ${res.valued}, skipped ${res.skipped}.`);
                refresh();
              } catch (e) {
                setRefreshMsg(e instanceof Error ? e.message : 'Refresh failed.');
              } finally {
                setRefreshing(false);
              }
            }}
            disabled={refreshing}
            className="text-sm text-accent font-semibold disabled:opacity-50"
          >
            {refreshing ? 'Refreshing…' : 'Refresh valuations'}
          </button>
          {accounts.length > 0 && (
            <>
              <button onClick={() => { setShowImportCas(s => !s); setShowImportCsv(false); }}
                className="text-sm text-accent font-semibold">
                {showImportCas ? 'Cancel' : 'Import CAS'}
              </button>
              <button onClick={() => { setShowImportCsv(s => !s); setShowImportCas(false); }}
                className="text-sm text-accent font-semibold">
                {showImportCsv ? 'Cancel' : 'Import CSV'}
              </button>
            </>
          )}
          <button onClick={onSwitch} className="text-sm text-muted hover:text-tx">Switch profile</button>
        </div>
      </div>

      {showImportCas && <ImportCasForm accounts={accounts} onImported={() => { refresh(); setShowImportCas(false); }} />}
      {showImportCsv && <ImportCsvForm accounts={accounts} onImported={() => { refresh(); setShowImportCsv(false); }} />}

      {networthError && <ErrorBanner message={networthError} className="mb-6" onRetry={refresh} />}
      {!networth && !netWorthLoaded && (
        <div className="bg-card border border-border rounded-xl p-5 mb-6" aria-busy="true">
          <Skeleton className="h-3 w-24 mb-3" />
          <Skeleton className="h-8 w-40 mb-3" />
          <Skeleton className="h-3 w-full" />
        </div>
      )}
      {networth && (() => {
        const entries = Object.entries(networth.by_type).sort(([, a], [, b]) => Math.abs(b) - Math.abs(a));
        const scale = entries.reduce((s, [, v]) => s + Math.abs(v), 0) || 1;
        return (
          <div className="bg-card border border-border rounded-xl p-5 mb-6">
            <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-2">Net Worth</p>
            <p className="text-3xl font-bold font-mono text-tx mb-3">{fmtInr(networth.total)}</p>
            {entries.length > 0 && (
              <div className="flex h-2 w-full rounded-full overflow-hidden mb-3 bg-border/60">
                {entries.map(([type, val], i) => (
                  <div
                    key={type}
                    title={`${type}: ${fmtInr(val)}`}
                    className={val < 0 ? 'bg-sell' : 'bg-accent'}
                    style={{ width: `${(Math.abs(val) / scale) * 100}%`, opacity: val < 0 ? 0.7 : Math.max(0.3, 1 - i * 0.15) }}
                  />
                ))}
              </div>
            )}
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              {entries.map(([type, val]) => (
                <span key={type} className="text-sm text-muted">
                  {type} <span className="text-tx/60">({Math.round((Math.abs(val) / scale) * 100)}%)</span>:{' '}
                  <span className={`font-mono font-semibold ${val < 0 ? 'text-sell' : 'text-tx'}`}>{fmtInr(val)}</span>
                </span>
              ))}
              {entries.length === 0 && <span className="text-sm text-muted">No assets yet.</span>}
            </div>
          </div>
        );
      })()}

      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-semibold text-tx">Accounts</p>
        <button ref={addAccountToggleRef} onClick={() => setShowAddAccount(s => !s)} className="text-sm text-accent font-semibold">
          {showAddAccount ? 'Cancel' : '+ Add account'}
        </button>
      </div>
      {showAddAccount && (
        <div className="mb-4">
          <AddAccountForm profileId={profile.id} onAdded={() => { refresh(); setShowAddAccount(false); addAccountToggleRef.current?.focus(); }} />
        </div>
      )}

      {(accountsError || connectionsError) && (
        <ErrorBanner message={accountsError || connectionsError!} className="mb-3" onRetry={refresh} />
      )}
      {!loaded ? (
        <div className="flex flex-col gap-3" aria-busy="true">
          {[0, 1].map(i => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {accounts.map(acc => (
            <AccountBlock
              key={acc.id}
              account={acc}
              assets={assetsByAccount[acc.id] ?? []}
              connections={connections.filter(c => c.account_id === acc.id)}
              onChanged={refresh}
              onPoll={pollConnections}
            />
          ))}
          {accounts.length === 0 && !accountsError && <p className="text-sm text-muted">No accounts yet — add one above.</p>}
        </div>
      )}
    </div>
  );
}

export default function PortfolioAggregatorPage() {
  const [profile, setProfile] = useState<PortfolioProfile | null | undefined>(undefined);
  // A fetch failure while resolving a STORED profile id must not be treated
  // as "no profile" (design.md's five-states rule, state 4) — that would silently drop the
  // user back to profile selection even though their profile still exists,
  // just because of a transient network blip. `profile` stays undefined
  // (loading) on failure so the picker/dashboard never falsely render;
  // this error banner does instead, with a real retry.
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadProfile = useCallback(() => {
    setLoadError(null);
    const stored = localStorage.getItem(PROFILE_KEY);
    if (!stored) { setProfile(null); return; }
    const id = Number(stored);
    api<{ profiles: PortfolioProfile[] }>('profiles')
      .then(d => setProfile(d.profiles.find(p => p.id === id) ?? null))
      .catch(e => setLoadError(e instanceof Error ? e.message : 'Could not load your profile.'));
  }, []);
  useEffect(loadProfile, [loadProfile]);

  function select(p: PortfolioProfile) {
    localStorage.setItem(PROFILE_KEY, String(p.id));
    setProfile(p);
  }

  function switchProfile() {
    localStorage.removeItem(PROFILE_KEY);
    setProfile(null);
  }

  return (
    <PageShell active="portfolio-aggregator" maxWidth="max-w-5xl">
      {loadError ? (
        <ErrorBanner message={loadError} className="max-w-md mx-auto mt-12" onRetry={loadProfile} />
      ) : profile === undefined ? (
        <p className="text-sm text-muted text-center mt-12" aria-busy="true">Loading…</p>
      ) : profile === null ? (
        <ProfilePicker onSelect={select} />
      ) : (
        <ProfileView profile={profile} onSwitch={switchProfile} />
      )}
    </PageShell>
  );
}
