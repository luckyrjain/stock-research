import { setSessionCookieHeader } from '@/lib/auth-cookie';

const API = process.env.API_URL ?? 'http://localhost:8000';

function unavailable() {
  return Response.json(
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { status: 503 },
  );
}

// Unlike the other proxy routes, this one also sets the session cookie on the
// frontend's own origin when the backend confirms the token — the cookie has
// to be set here (not by the backend) since the browser only ever talks to
// this Next.js origin, not FastAPI's directly.
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/auth/verify${qs ? `?${qs}` : ''}`, { cache: 'no-store' });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  const res = Response.json(data, { status: upstream.status });
  if (upstream.ok && data.session_token) {
    res.headers.append('Set-Cookie', setSessionCookieHeader(data.session_token));
  }
  return res;
}
