const API = process.env.API_URL ?? 'http://localhost:8000';

export async function POST() {
  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/sme-signals/refresh`, { method: 'POST', cache: 'no-store' });
  } catch {
    return Response.json(
      { error: 'Backend unavailable. Make sure the analysis service is running.' },
      { status: 503 },
    );
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
