import { clientIpHeaders } from '@/lib/proxy-headers';
import { sseError } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

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
    return sseError('Backend unavailable. Please make sure the analysis service is running.');
  }

  if (!upstream.ok || !upstream.body) {
    const message = upstream.status >= 500
      ? 'Analysis backend is unavailable right now. Please try again shortly.'
      : `Analysis request failed with status ${upstream.status}.`;
    return sseError(message);
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
