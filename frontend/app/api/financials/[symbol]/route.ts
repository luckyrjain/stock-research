import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

function fallback(symbol: string) {
  return { symbol, profit_loss: null, balance_sheet: null, cash_flow: null, dcf: null };
}

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/financials/${encodeURIComponent(symbol)}`, { headers: clientIpHeaders(req), cache: 'no-store' });
  } catch {
    return Response.json(fallback(symbol), { status: 503 });
  }

  try {
    const data = await upstream.json();
    return Response.json(data, { status: upstream.status });
  } catch {
    return Response.json(fallback(symbol), { status: 502 });
  }
}
