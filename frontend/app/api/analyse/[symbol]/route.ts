import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

function sseErrorResponse(message: string) {
  // Always HTTP 200 -- EventSource only ever reads a response body when the
  // status is exactly 200 with a text/event-stream Content-Type; any other
  // status makes the browser "fail the connection" per the WHATWG spec and
  // fire a generic error event WITHOUT ever parsing this payload, so the
  // specific `message` here would silently never reach the client. An
  // `event: error` frame inside a 200-status stream is how a real,
  // mid-stream backend error is already delivered and handled correctly by
  // useStockAnalysis.ts's onmessage -- this reuses that same, working path
  // instead of relying on the HTTP status layer at all.
  const payload = `data: ${JSON.stringify({ event: 'error', message })}\n\n`;

  return new Response(payload, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const { searchParams } = new URL(req.url);
  const force = searchParams.get('force') ?? 'false';

  let upstream: Response;
  try {
    upstream = await fetch(
      `${API}/api/analyse/${encodeURIComponent(symbol)}?force=${encodeURIComponent(force)}`,
      { headers: clientIpHeaders(req), cache: 'no-store' },
    );
  } catch {
    return sseErrorResponse('Backend unavailable. Please make sure the analysis service is running.');
  }

  if (!upstream.ok || !upstream.body) {
    const message = upstream.status >= 500
      ? 'Analysis backend is unavailable right now. Please try again shortly.'
      : `Analysis request failed with status ${upstream.status}.`;
    return sseErrorResponse(message);
  }

  // Pipe the SSE stream straight through — no buffering
  return new Response(upstream.body, {
    headers: {
      'Content-Type':  'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection':    'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
