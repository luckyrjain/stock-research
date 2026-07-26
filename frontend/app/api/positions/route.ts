import { getSessionTokenFromRequest } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

function unavailable() {
  return Response.json(
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { status: 503 },
  );
}

// Forwards the session cookie (if any) as a Bearer header alongside the
// existing client_id passthrough — same pattern as app/api/watchlist/route.ts.
// The backend prefers the account identity when a valid session is present,
// so a signed-in user's positions follow their account across browsers
// instead of staying tied to one browser's anonymous client_id.
function authHeaders(req: Request): Record<string, string> {
  const token = getSessionTokenFromRequest(req);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/positions${qs ? `?${qs}` : ''}`, {
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
    upstream = await fetch(`${API}/api/positions`, {
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
