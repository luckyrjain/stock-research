import { ImageResponse } from 'next/og';
import { NextRequest } from 'next/server';

export const runtime = 'edge';

const VALID_SIZES = new Set([192, 512]);

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ size: string }> },
) {
  const { size: sizeParam } = await params;
  const size = Number(sizeParam);
  if (!VALID_SIZES.has(size)) {
    return new Response('Not found', { status: 404 });
  }

  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0b1120',
        }}
      >
        <div
          style={{
            fontSize: size * 0.46,
            fontWeight: 900,
            color: '#618eff', // = accent (COLOR-04, design.md — keep in sync, SRC-03)
            fontFamily: 'sans-serif',
            letterSpacing: -size * 0.02,
          }}
        >
          AP
        </div>
      </div>
    ),
    { width: size, height: size },
  );
}
