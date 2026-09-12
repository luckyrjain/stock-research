import { authHeaders } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  return proxyJson(`${API}/api/api-keys/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: { ...clientIpHeaders(req), ...authHeaders(req) },
    cache: 'no-store',
  });
}
