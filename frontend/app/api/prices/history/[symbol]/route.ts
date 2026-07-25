import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const days = new URL(req.url).searchParams.get('days');
  const qs = days ? `?days=${encodeURIComponent(days)}` : '';

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/prices/history/${symbol}${qs}`, { headers: clientIpHeaders(req), cache: 'no-store' });
  } catch {
    return Response.json({ symbol, exchange: null, dates: [], closes: [] }, { status: 503 });
  }

  try {
    const data = await upstream.json();
    return Response.json(data, { status: upstream.status });
  } catch {
    return Response.json({ symbol, exchange: null, dates: [], closes: [] }, { status: 502 });
  }
}
