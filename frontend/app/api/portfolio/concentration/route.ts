import { authHeaders } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

// Same auth/client_id passthrough as app/api/positions/route.ts, since this
// reads the same positions table under the same dual identity.

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  return proxyJson(
    `${API}/api/portfolio/concentration${qs ? `?${qs}` : ''}`,
    { headers: { ...clientIpHeaders(req), ...authHeaders(req) }, cache: 'no-store' },
    { by_sector: {}, concentrated_sectors: [] },
  );
}
