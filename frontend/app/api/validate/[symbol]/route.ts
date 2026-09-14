import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const exchange = new URL(req.url).searchParams.get('exchange');
  const upstreamUrl = exchange
    ? `${API}/api/validate/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}`
    : `${API}/api/validate/${encodeURIComponent(symbol)}`;

  return proxyJson(
    upstreamUrl,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { found: false, valid: false, symbol, company: '', suggestions: [], error: 'Backend unavailable' },
  );
}
