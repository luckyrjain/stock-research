import { clearSessionCookieHeader, getSessionTokenFromRequest } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function POST(req: Request) {
  const token = getSessionTokenFromRequest(req);
  if (token) {
    try {
      await fetch(`${API}/api/auth/logout`, {
        method: 'POST',
        headers: { ...clientIpHeaders(req), Authorization: `Bearer ${token}` },
        cache: 'no-store',
      });
    } catch {
      // Best-effort — the cookie gets cleared below regardless of whether the
      // backend session delete succeeded, so the browser is signed out either way.
    }
  }

  const res = Response.json({ ok: true });
  res.headers.append('Set-Cookie', clearSessionCookieHeader());
  return res;
}
