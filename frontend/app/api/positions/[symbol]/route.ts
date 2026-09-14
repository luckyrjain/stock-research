import { authHeaders } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  return proxyJson(`${API}/api/positions/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ''}`, {
    method: 'DELETE',
    headers: { ...clientIpHeaders(req), ...authHeaders(req) },
    cache: 'no-store',
  });
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

  return proxyJson(`${API}/api/positions/${encodeURIComponent(symbol)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...clientIpHeaders(req), ...authHeaders(req) },
    body,
    cache: 'no-store',
  });
}
