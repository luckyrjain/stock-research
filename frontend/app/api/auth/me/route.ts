import { authHeaders, getSessionTokenFromRequest } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: Request) {
  const token = getSessionTokenFromRequest(req);
  if (!token) {
    return Response.json({ error: 'Not signed in.' }, { status: 401 });
  }

  return proxyJson(`${API}/api/auth/me`, {
    headers: { ...clientIpHeaders(req), ...authHeaders(req) },
    cache: 'no-store',
  });
}
