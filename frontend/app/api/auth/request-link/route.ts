import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function POST(req: Request) {
  const body = await req.text();

  return proxyJson(`${API}/api/auth/request-link`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...clientIpHeaders(req) },
    body,
    cache: 'no-store',
  });
}
