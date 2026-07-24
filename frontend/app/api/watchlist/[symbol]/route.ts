import { getSessionTokenFromRequest } from '@/lib/auth-cookie';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  const token = getSessionTokenFromRequest(req);

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/watchlist/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ''}`, {
      method: 'DELETE',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
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
