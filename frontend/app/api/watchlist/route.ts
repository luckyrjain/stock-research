const API = process.env.API_URL ?? 'http://localhost:8000';

function unavailable() {
  return Response.json(
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { status: 503 },
  );
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/watchlist${qs ? `?${qs}` : ''}`, { cache: 'no-store' });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}

export async function POST(req: Request) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/watchlist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      cache: 'no-store',
    });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
