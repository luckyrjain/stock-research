import { setSessionCookieHeader } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';

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
    upstream = await fetch(`${API}/api/auth/verify${qs ? `?${qs}` : ''}`, { headers: clientIpHeaders(req), cache: 'no-store' });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  if (upstream.ok && data.session_token) {
    // Never echo the raw token back to the browser — it goes into the
    // httpOnly cookie only. Returning it in the JSON body too would let any
    // page-level JS (or XSS) read the live session token straight out of the
    // fetch response, defeating the point of the httpOnly cookie.
    const { session_token, ...body } = data;
    const res = Response.json(body, { status: upstream.status });
    res.headers.append('Set-Cookie', setSessionCookieHeader(session_token));
    return res;
  }
  return Response.json(data, { status: upstream.status });
}
