import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const symbols = searchParams.get('symbols') ?? '';

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/watchlist/calendar?symbols=${encodeURIComponent(symbols)}`, {
      headers: clientIpHeaders(req),
      cache: 'no-store',
    });
  } catch {
    return Response.json({ entries: [] }, { status: 503 });
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
