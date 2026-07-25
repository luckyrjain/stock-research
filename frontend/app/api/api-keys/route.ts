import { getSessionTokenFromRequest } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

function unavailable() {
  return Response.json(
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { status: 503 },
  );
}

// Same pattern as app/api/watchlist/route.ts: forward the session cookie as a
// Bearer header. Unlike watchlist, there is no anonymous fallback here — key
// management always requires being signed in, so a missing/invalid session
// just means the backend returns 401.
function authHeaders(req: Request): Record<string, string> {
  const token = getSessionTokenFromRequest(req);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function GET(req: Request) {
  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/api-keys`, {
      headers: { ...clientIpHeaders(req), ...authHeaders(req) },
      cache: 'no-store',
    });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}

export async function POST(req: Request) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/api-keys`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...clientIpHeaders(req), ...authHeaders(req) },
      body,
      cache: 'no-store',
    });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
