const API = process.env.API_URL ?? 'http://localhost:8000';

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const res = await fetch(`${API}/api/validate/${symbol}`, { cache: 'no-store' });
    const data = await res.json();
    return Response.json(data);
  } catch {
    return Response.json(
      { found: false, valid: false, symbol, company: '', suggestions: [], error: 'Backend unavailable' },
      { status: 503 },
    );
  }
}
