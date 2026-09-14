import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function POST(req: Request) {
  return proxyJson(`${API}/api/sme-signals/refresh`, {
    method: 'POST',
    headers: clientIpHeaders(req),
    cache: 'no-store',
  });
}
