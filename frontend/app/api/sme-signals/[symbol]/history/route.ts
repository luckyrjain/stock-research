import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;

  return proxyJson(
    `${API}/api/sme-signals/${encodeURIComponent(symbol)}/history`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { error: 'Malformed upstream response.' },
  );
}
