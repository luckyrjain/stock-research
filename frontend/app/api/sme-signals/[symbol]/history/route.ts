const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/sme-signals/${symbol}/history`, { cache: 'no-store' });
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
