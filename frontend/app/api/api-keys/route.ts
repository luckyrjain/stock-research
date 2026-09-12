import { authHeaders } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

// Same pattern as app/api/watchlist/route.ts: forward the session cookie as a
// Bearer header (see lib/auth-cookie.ts's authHeaders()). Unlike watchlist,
// there is no anonymous fallback here — key management always requires being
// signed in, so a missing/invalid session just means the backend returns 401.

export async function GET(req: Request) {
  return proxyJson(`${API}/api/api-keys`, {
    headers: { ...clientIpHeaders(req), ...authHeaders(req) },
    cache: 'no-store',
  });
}

export async function POST(req: Request) {
  const body = await req.text();

  return proxyJson(`${API}/api/api-keys`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...clientIpHeaders(req), ...authHeaders(req) },
    body,
    cache: 'no-store',
  });
}
