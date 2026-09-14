import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  const url = `${API}/api/sme-signals${qs ? `?${qs}` : ''}`;

  return proxyJson(url, { headers: clientIpHeaders(req), cache: 'no-store' });
}
