import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

// Catch-all proxy for the Portfolio Aggregator's own sub-paths
// (profiles/accounts/assets/valuations/networth) — no auth/client_id
// passthrough, since this feature has none (see routes/portfolio_aggregator.py's
// docstring: a personal, localhost/Tailscale-only tool, not a multi-tenant
// one). A specific route (app/api/portfolio/concentration/route.ts, a
// different, unrelated feature sharing the same /api/portfolio prefix) takes
// precedence over this catch-all for its own exact path.
function unavailable() {
  return Response.json(
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { status: 503 },
  );
}

async function proxy(req: Request, path: string[], method: string) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  const url = `${API}/api/portfolio/${path.join('/')}${qs ? `?${qs}` : ''}`;

  // The three import-cas/import-csv[/preview] paths are multipart file
  // uploads — forward the raw body and original Content-Type (it carries
  // the multipart boundary FastAPI needs) instead of the JSON passthrough
  // every other portfolio-aggregator endpoint uses.
  const contentType = req.headers.get('content-type') || '';
  const isMultipart = contentType.startsWith('multipart/form-data');
  const headers: Record<string, string> = { ...clientIpHeaders(req) };
  let body: BodyInit | undefined;
  if (method === 'GET' || method === 'DELETE') {
    body = undefined;
  } else if (isMultipart) {
    headers['Content-Type'] = contentType;
    body = await req.arrayBuffer();
  } else {
    headers['Content-Type'] = 'application/json';
    body = await req.text();
  }

  let upstream: Response;
  try {
    upstream = await fetch(url, { method, headers, body, cache: 'no-store' });
  } catch {
    return unavailable();
  }

  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}

export async function GET(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path, 'GET');
}

export async function POST(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path, 'POST');
}

export async function PATCH(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path, 'PATCH');
}

export async function DELETE(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path, 'DELETE');
}
