import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: Request) {
  return proxyJson(
    `${API}/api/market-picks/status`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { error: 'Malformed upstream response.' },
  );
}
