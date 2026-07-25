// Shared by every Next.js proxy route under app/api/* — server-only.
//
// Every one of these routes forwards a browser request to the FastAPI
// backend server-to-server. From the backend's point of view, every such
// request arrives from this Next.js server's own IP, never the real
// visitor's — which collapses api.py's per-IP rate limiters (see its own
// _client_ip comment) into one shared bucket for the whole site, the
// opposite of what they're for.
//
// clientIpHeaders() forwards the real client IP — read off whatever sits in
// front of this Next.js server in production (a reverse proxy/CDN/load
// balancer; see docs/deployment.md), via the standard X-Forwarded-For
// header — alongside TRUSTED_PROXY_SECRET, a value only this server and the
// backend know. The backend only trusts the forwarded IP when that secret
// matches (see api.py::_client_ip); without it, api.py falls back to its
// own view of the connecting peer, exactly as before this existed. Both
// pieces are optional: a deployment that hasn't set TRUSTED_PROXY_SECRET
// (or has no reverse proxy in front of Next.js at all) gets the same
// behavior as before — nothing here is a hard requirement.
export function clientIpHeaders(req: Request): Record<string, string> {
  const headers: Record<string, string> = {};

  const forwarded = req.headers.get('x-forwarded-for');
  const clientIp = forwarded?.split(',')[0]?.trim() || req.headers.get('x-real-ip')?.trim();
  if (clientIp) headers['X-Forwarded-For'] = clientIp;

  const secret = process.env.TRUSTED_PROXY_SECRET;
  if (secret) headers['X-Internal-Proxy-Secret'] = secret;

  return headers;
}
