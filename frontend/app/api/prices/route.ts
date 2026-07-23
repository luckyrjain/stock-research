import { NextRequest } from 'next/server';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: NextRequest) {
  const symbols = req.nextUrl.searchParams.get('symbols') ?? '';

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/prices?symbols=${encodeURIComponent(symbols)}`, {
      cache: 'no-store',
    });
  } catch {
    return Response.json({ prices: {} }, { status: 503 });
  }

  try {
    const data = await upstream.json();
    return Response.json(data, { status: upstream.status });
  } catch {
    return Response.json({ prices: {} }, { status: 502 });
  }
}
