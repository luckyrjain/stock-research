const API = process.env.API_URL ?? 'http://localhost:8000';

function sseError(message: string) {
  return new Response(`data: ${JSON.stringify({ event: 'error', message })}\n\n`, {
    status: 503,
    headers: {
      'Content-Type':      'text/event-stream',
      'Cache-Control':     'no-cache',
      'Connection':        'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}

export async function GET() {
  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/market-picks`, { cache: 'no-store' });
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
