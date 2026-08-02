import { getSessionTokenFromRequest } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

// Same auth/client_id passthrough as app/api/positions/route.ts, since this
// reads the same positions table under the same dual identity.
function authHeaders(req: Request): Record<string, string> {
  const token = getSessionTokenFromRequest(req);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/portfolio/concentration${qs ? `?${qs}` : ''}`, {
      headers: { ...clientIpHeaders(req), ...authHeaders(req) },
      cache: 'no-store',
    });
  } catch {
    return Response.json({ by_sector: {}, concentrated_sectors: [] }, { status: 503 });
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
