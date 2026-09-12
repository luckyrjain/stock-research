import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;

  return proxyJson(
    `${API}/api/insider-activity/${encodeURIComponent(symbol)}`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { symbol, insider_trades: [], bulk_block_deals: [] },
  );
}
