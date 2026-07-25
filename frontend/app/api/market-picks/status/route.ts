import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: Request) {
  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/market-picks/status`, { headers: clientIpHeaders(req), cache: 'no-store' });
  } catch {
    return Response.json(
      { error: 'Backend unavailable. Make sure the analysis service is running.' },
      { status: 503 },
    );
  }

  try {
    const data = await upstream.json();
    return Response.json(data, { status: upstream.status });
  } catch {
    return Response.json({ error: 'Malformed upstream response.' }, { status: 502 });
  }
}
