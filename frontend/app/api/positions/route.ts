import { authHeaders } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

// Forwards the session cookie (if any) as a Bearer header (see
// lib/auth-cookie.ts's authHeaders()) alongside the existing client_id
// passthrough — same pattern as app/api/watchlist/route.ts. The backend
// prefers the account identity when a valid session is present, so a
// signed-in user's positions follow their account across browsers instead
// of staying tied to one browser's anonymous client_id.

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  return proxyJson(`${API}/api/positions${qs ? `?${qs}` : ''}`, {
    headers: { ...clientIpHeaders(req), ...authHeaders(req) },
    cache: 'no-store',
  });
}

export async function POST(req: Request) {
  const body = await req.text();

  return proxyJson(`${API}/api/positions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...clientIpHeaders(req), ...authHeaders(req) },
    body,
    cache: 'no-store',
  });
}
