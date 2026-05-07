import { NextRequest } from 'next/server';

const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(req: NextRequest) {
  const symbols = req.nextUrl.searchParams.get('symbols') ?? '';
  try {
    const res = await fetch(`${API}/api/prices?symbols=${encodeURIComponent(symbols)}`, {
      cache: 'no-store',
    });
    const data = await res.json();
    return Response.json(data);
  } catch {
    return Response.json({ prices: {} }, { status: 503 });
  }
}
