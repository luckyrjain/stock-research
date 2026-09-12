import { authHeaders } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

// Same shape as app/api/watchlist/claim/route.ts — a missing session is a
// real 401 here, not a fall-back-to-client_id case, since this endpoint's
// only caller already knows a session exists.
export async function POST(req: Request) {
  const body = await req.text();

  return proxyJson(`${API}/api/positions/claim`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...clientIpHeaders(req),
      ...authHeaders(req),
    },
    body,
    cache: 'no-store',
  });
}
