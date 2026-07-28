import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

function sseError(message: string) {
  // Always HTTP 200 -- see the identical comment in
  // app/api/analyse/[symbol]/route.ts's sseErrorResponse(): EventSource
  // only reads a response body on a 200 text/event-stream response, so a
  // non-200 status here made this crafted message unreachable by the
  // browser, which always fell back to a generic error instead.
  return new Response(`data: ${JSON.stringify({ event: 'error', message })}\n\n`, {
    status: 200,
    headers: {
      'Content-Type':      'text/event-stream',
      'Cache-Control':     'no-cache',
      'Connection':        'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}

export async function GET(req: Request) {
  const force = new URL(req.url).searchParams.get('force');
  const upstreamUrl = force
    ? `${API}/api/market-picks?force=${encodeURIComponent(force)}`
    : `${API}/api/market-picks`;

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, { headers: clientIpHeaders(req), cache: 'no-store' });
  } catch {
    return sseError('Backend unavailable. Make sure the analysis service is running.');
  }

  if (!upstream.ok || !upstream.body) {
    return sseError(`Market picks backend returned status ${upstream.status}.`);
  }

  return new Response(upstream.body, {
    headers: {
      'Content-Type':      'text/event-stream',
      'Cache-Control':     'no-cache',
      'Connection':        'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
