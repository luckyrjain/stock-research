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

  return proxyJson(`${API}/api/watchlist/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ''}`, {
    method: 'DELETE',
    headers: { ...clientIpHeaders(req), ...authHeaders(req) },
    cache: 'no-store',
  });
}
