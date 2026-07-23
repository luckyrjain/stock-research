const API = process.env.API_URL ?? 'http://localhost:8000';

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/watchlist/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ''}`, {
      method: 'DELETE',
      cache: 'no-store',
    });
  } catch {
    return Response.json(
      { error: 'Backend unavailable. Make sure the analysis service is running.' },
      { status: 503 },
    );
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
