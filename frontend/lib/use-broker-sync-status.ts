'use client';

import { useEffect, useRef, useState } from 'react';
import type { BrokerConnection, BrokerSyncAck } from '@/types';

// BrokerRow and HdfcBrokerRow each independently carried this same ~35-line
// pair of effects (the file's own comments said as much: "See HdfcBrokerRow's
// identical effect"/"identical ref"/"identical comment"). The two components
// genuinely differ in their connect/auth flow (redirect vs. two-step OTP) —
// that stays local to each — but "poll while syncing, then resolve
// success/error into a message and fire onSynced() once" is pure
// sync-status-tracking logic with nothing broker-specific about it.
//
// Callers still own `busy`/`msg` for their OWN actions (`connect()`/`sync()`/
// `loginStart()`/`verifyOtp()` all set them directly before this hook's
// resolution effect gets a chance to), so both are returned as state +
// setter pairs rather than hidden behind a narrower interface.
export function useBrokerSyncStatus(
  connection: BrokerConnection | undefined,
  onSynced: () => void,
  onPoll: () => void,
) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // Guards the completion-refresh below against firing more than once per
  // sync: `onPoll` (the 2s-interval poller while syncing) only refetches
  // `connections` to stay under the read-rate-limit, so a real refresh()
  // must run once when the job finishes — but connections' own object
  // identity changes on every unrelated refresh too, not just this one.
  const handledSyncRef = useRef<string | null>(null);

  useEffect(() => {
    if (connection?.sync_status !== 'syncing') return;
    const id = setInterval(onPoll, 2000);
    return () => clearInterval(id);
  }, [connection?.sync_status, onPoll]);

  useEffect(() => {
    if (connection?.sync_status === 'success' && connection.last_sync_summary) {
      const r = connection.last_sync_summary;
      const archived = r.holdings_archived ? `, ${r.holdings_archived} archived` : '';
      setMsg(`Synced ${r.holdings_synced} holdings, ${r.trades_synced} trades${archived}.`);
      setBusy(false);
      // The lightweight onPoll used while syncing only refetches
      // `connections` (rate-limit reasons — see onPoll's own comment) —
      // accounts/assets/net-worth are still whatever they were before this
      // sync started until a real refresh runs. `last_synced_at` is a
      // stable per-sync key, so this only fires once per completed sync,
      // not on every later unrelated refresh that also touches `connection`.
      const key = `${connection.id}:${connection.last_synced_at}`;
      if (handledSyncRef.current !== key) {
        handledSyncRef.current = key;
        onSynced();
      }
    } else if (connection?.sync_status === 'error' && connection.last_sync_error) {
      // A partial-fetch failure (e.g. holdings synced, tradebook fetch
      // failed) still carries real synced counts in last_sync_summary
      // alongside the error — shown together so the user isn't left
      // thinking nothing happened when some data actually landed.
      const r = connection.last_sync_summary;
      const synced = r && ((r.holdings_synced ?? 0) > 0 || (r.trades_synced ?? 0) > 0);
      setMsg(synced ? `${connection.last_sync_error} (${r.holdings_synced} holdings, ${r.trades_synced} trades synced.)` : connection.last_sync_error);
      setBusy(false);
      if (synced) {
        const key = `${connection.id}:${connection.last_synced_at}`;
        if (handledSyncRef.current !== key) {
          handledSyncRef.current = key;
          onSynced();
        }
      }
    }
  }, [connection?.sync_status, connection?.last_sync_summary, connection?.last_sync_error, connection?.id, connection?.last_synced_at, onSynced]);

  const syncing = busy || connection?.sync_status === 'syncing';

  return { busy, setBusy, msg, setMsg, syncing };
}

// BrokerRow's and HdfcBrokerRow's sync() bodies were identical apart from
// the endpoint string — HdfcBrokerRow hardcodes 'hdfc_securities', which is
// exactly BrokerRow's broker.id for the HDFC entry, so the endpoint shape
// is actually the same. `api` is passed in rather than imported here
// because it's a small per-page wrapper (defined in
// app/portfolio-aggregator/page.tsx) that injects that page's client_id
// into every request.
export async function syncBroker(
  brokerId: string,
  accountId: number,
  api: <T>(path: string, init?: RequestInit) => Promise<T>,
  setBusy: (busy: boolean) => void,
  setMsg: (msg: string | null) => void,
  onSynced: () => void,
) {
  setBusy(true);
  setMsg(null);
  try {
    await api<BrokerSyncAck>(`broker/${brokerId}/sync`, {
      method: 'POST',
      body: JSON.stringify({ account_id: accountId }),
    });
    // The sync itself runs in the background (202 Accepted). `busy`
    // deliberately stays true here rather than clearing in a `finally`
    // below — the connections list hasn't been refetched yet at this
    // point, so `connection.sync_status` is still whatever it was
    // *before* this click, not yet "syncing". Clearing `busy` here would
    // briefly re-enable "Sync now" until the next poll catches up. The
    // status-resolution effect in useBrokerSyncStatus clears it once the
    // outcome is known.
    setMsg('Syncing…');
    onSynced();
  } catch (e) {
    setMsg(e instanceof Error ? e.message : 'Sync failed');
    setBusy(false);
  }
}
