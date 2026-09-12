import { NextRequest } from 'next/server';
import { clientIpHeaders } from '@/lib/proxy-headers';
import { proxyJson } from '@/lib/proxy';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: NextRequest) {
  const symbols = req.nextUrl.searchParams.get('symbols') ?? '';

  return proxyJson(
    `${API}/api/prices?symbols=${encodeURIComponent(symbols)}`,
    { headers: clientIpHeaders(req), cache: 'no-store' },
    { prices: {} },
  );
}
