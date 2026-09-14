import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;

  return proxyJson(
    `${API}/api/verdict-history/${encodeURIComponent(symbol)}`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { symbol, history: [], win_rate: null, scored_count: 0 },
  );
}
