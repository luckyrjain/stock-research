import { clientIpHeaders } from '@/lib/proxy-headers';
import { sseError } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

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
