// Shared by every Next.js proxy route under app/api/* — server-only.
//
// Every proxy route forwards a request to the FastAPI backend and has to
// tell apart two distinct failure modes: the backend being unreachable at
// all (a network failure — fetch() itself throws) versus the backend
// responding but with a body that isn't valid JSON (a malformed upstream
// response — upstream.json() throws). The former is a 503 (service
// unavailable); the latter a 502 (bad gateway). proxyJson() does the
// fetch-and-classify once; each route still builds its own URL, method,
// headers, and body, and can pass its own fallback response shape (some
// routes return e.g. `{ history: [] }` on failure instead of a generic
// error object) — that same shape is reused for both failure modes unless a
// route passes a distinct `malformedFallback`.
const DEFAULT_UNAVAILABLE_MESSAGE = 'Backend unavailable. Make sure the analysis service is running.';

export function unavailable(message: string = DEFAULT_UNAVAILABLE_MESSAGE) {
  return Response.json({ error: message }, { status: 503 });
}

export async function proxyJson(
  url: string,
  init: RequestInit,
  fallback: unknown = { error: DEFAULT_UNAVAILABLE_MESSAGE },
  malformedFallback: unknown = fallback,
): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch {
    return Response.json(fallback, { status: 503 });
  }

  try {
    const data = await upstream.json();
    return Response.json(data, { status: upstream.status });
  } catch {
    return Response.json(malformedFallback, { status: 502 });
  }
}

// Shared by the two SSE proxy routes (app/api/analyse/[symbol]/route.ts,
// app/api/market-picks/route.ts). Always HTTP 200 -- EventSource only ever
// reads a response body when the status is exactly 200 with a
// text/event-stream Content-Type; any other status makes the browser "fail
// the connection" per the WHATWG spec and fire a generic error event
// WITHOUT ever parsing this payload, so a specific `message` here would
// otherwise silently never reach the client. An `event: error` frame inside
// a 200-status stream is how a real, mid-stream backend error is already
// delivered and handled by useStockAnalysis.ts's onmessage -- this reuses
// that same working path instead of relying on the HTTP status layer at all.
export function sseError(message: string): Response {
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
