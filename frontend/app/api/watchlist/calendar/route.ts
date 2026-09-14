import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const symbols = searchParams.get('symbols') ?? '';

  return proxyJson(
    `${API}/api/watchlist/calendar?symbols=${encodeURIComponent(symbols)}`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { entries: [] },
  );
}
