import { clientIpHeaders } from '@/lib/proxy-headers';

const API = process.env.API_URL ?? 'http://localhost:8000';

function sseErrorResponse(message: string, status = 503) {
  const payload = `data: ${JSON.stringify({ event: 'error', message })}\n\n`;

  return new Response(payload, {
    status,
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
      `${API}/api/analyse/${symbol}?force=${force}`,
      { headers: clientIpHeaders(req), cache: 'no-store' },
    );
  } catch {
    return sseErrorResponse('Backend unavailable. Please make sure the analysis service is running.');
  }

  if (!upstream.ok || !upstream.body) {
    const message = upstream.status >= 500
      ? 'Analysis backend is unavailable right now. Please try again shortly.'
      : `Analysis request failed with status ${upstream.status}.`;
    return sseErrorResponse(message, upstream.status || 502);
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
