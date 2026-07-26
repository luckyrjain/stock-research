import { getSessionTokenFromRequest } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

function unavailable() {
  return Response.json(
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { status: 503 },
  );
}

function authHeaders(req: Request): Record<string, string> {
  const token = getSessionTokenFromRequest(req);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/positions/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ''}`, {
      method: 'DELETE',
      headers: { ...clientIpHeaders(req), ...authHeaders(req) },
      cache: 'no-store',
    });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}

// Updates just the share count (see lib/positions.ts::updateShares) — the
// one field filled in after the fact from the Portfolio page, never at
// "I bought this" click-time.
export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/positions/${encodeURIComponent(symbol)}`, {
      method: 'PATCH',
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
