import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const reqUrl = new URL(req.url);
  const days = reqUrl.searchParams.get('days');
  const benchmark = reqUrl.searchParams.get('benchmark');
  const forwarded = new URLSearchParams();
  if (days) forwarded.set('days', days);
  if (benchmark) forwarded.set('benchmark', benchmark);
  const qs = forwarded.toString() ? `?${forwarded.toString()}` : '';

  return proxyJson(
    `${API}/api/prices/history/${encodeURIComponent(symbol)}${qs}`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { symbol, exchange: null, dates: [], closes: [] },
  );
}
