import { getSessionTokenFromRequest } from '@/lib/auth-cookie';

const API = process.env.API_URL ?? 'http://localhost:8000';

function unavailable() {
  return Response.json(
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { status: 503 },
  );
}

export async function GET(req: Request) {
  const token = getSessionTokenFromRequest(req);
  if (!token) {
    return Response.json({ error: 'Not signed in.' }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
