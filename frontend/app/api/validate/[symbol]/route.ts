import { clientIpHeaders } from '@/lib/proxy-headers';

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
  try {
    const res = await fetch(upstreamUrl, { headers: clientIpHeaders(req), cache: 'no-store' });
    const data = await res.json();
    return Response.json(data);
  } catch {
    return Response.json(
      { found: false, valid: false, symbol, company: '', suggestions: [], error: 'Backend unavailable' },
      { status: 503 },
    );
  }
}
