import { authHeaders } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

function unavailable() {
  return Response.json(
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { status: 503 },
  );
}

// Unlike every other watchlist proxy route, a missing session here isn't a
// "fall back to client_id" case — the backend itself requires one (this
// endpoint's only caller already knows a session exists, since it only
// fires right after sign-in completes) and returns 401 without it.
export async function POST(req: Request) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/watchlist/claim`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...clientIpHeaders(req),
        ...authHeaders(req),
      },
      body,
      cache: 'no-store',
    });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
