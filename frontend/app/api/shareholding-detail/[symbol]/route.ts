import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/shareholding-detail/${encodeURIComponent(symbol)}`, { headers: clientIpHeaders(req), cache: 'no-store' });
  } catch {
    return Response.json(
      { symbol, as_of_date: null, promoters: [], shareholder_categories: [], unavailable: true },
      { status: 503 },
    );
  }

  try {
    const data = await upstream.json();
    return Response.json(data, { status: upstream.status });
  } catch {
    return Response.json(
      { symbol, as_of_date: null, promoters: [], shareholder_categories: [], unavailable: true },
      { status: 502 },
    );
  }
}
