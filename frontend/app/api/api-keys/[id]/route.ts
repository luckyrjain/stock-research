import { getSessionTokenFromRequest } from '@/lib/auth-cookie';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const token = getSessionTokenFromRequest(req);

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/api-keys/${encodeURIComponent(id)}`, {
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
