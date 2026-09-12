import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

function fallback(symbol: string) {
  return { symbol, profit_loss: null, balance_sheet: null, cash_flow: null, dcf: null };
}

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;

  return proxyJson(
    `${API}/api/financials/${encodeURIComponent(symbol)}`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    fallback(symbol),
  );
}
