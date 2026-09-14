import { NextRequest } from 'next/server';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: NextRequest) {
  const date = req.nextUrl.searchParams.get('date');
  const qs = date ? `?date=${encodeURIComponent(date)}` : '';

  return proxyJson(
    `${API}/api/market-picks/history${qs}`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { error: 'Backend unavailable. Make sure the analysis service is running.' },
    { error: 'Malformed upstream response.' },
  );
}
