import { authHeaders } from '@/lib/auth-cookie';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

// Catch-all proxy for the Portfolio Aggregator's own sub-paths
// (profiles/accounts/assets/valuations/networth) — every row is now owned
// by client_id or, once signed in, the account (see
// routes/portfolio_aggregator.py's module docstring), so the session cookie
// is forwarded as a Bearer header exactly like the watchlist/positions
// proxies, on top of the existing client_id passthrough. A specific route
// (app/api/portfolio/concentration/route.ts, a different, unrelated feature
// sharing the same /api/portfolio prefix) takes precedence over this
// catch-all for its own exact path.
//
// PORTFOLIO_AGGREGATOR_ENABLED=false is an operator-facing kill switch,
// independent of the ownership model above — a cheap extra guard against
// accidentally exposing this feature on a deployment that isn't actually
// meant to serve it (this repo's own architectural stance is a
// localhost/Tailscale-only personal tool, not a public multi-tenant one).
// Defaults to enabled so existing deployments don't need to opt in.
function disabled() {
  return Response.json(
    { error: 'Portfolio Aggregator is disabled on this deployment.' },
    { status: 404 },
  );
}

async function proxy(req: Request, path: string[], method: string) {
  if (process.env.PORTFOLIO_AGGREGATOR_ENABLED === 'false') return disabled();

  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  const url = `${API}/api/portfolio/${path.join('/')}${qs ? `?${qs}` : ''}`;

  // The three import-cas/import-csv[/preview] paths are multipart file
  // uploads — forward the raw body and original Content-Type (it carries
  // the multipart boundary FastAPI needs) instead of the JSON passthrough
  // every other portfolio-aggregator endpoint uses.
  const contentType = req.headers.get('content-type') || '';
  const isMultipart = contentType.startsWith('multipart/form-data');
  const headers: Record<string, string> = { ...clientIpHeaders(req), ...authHeaders(req) };
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

  return proxyJson(url, { method, headers, body, cache: 'no-store' });
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
