import { ImageResponse } from 'next/og';

export const size = { width: 180, height: 180 };
export const contentType = 'image/png';

export default function AppleIcon() {
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
            fontSize: 82,
            fontWeight: 900,
            color: '#618eff', // = accent (COLOR-04, design.md — keep in sync, SRC-03)
            fontFamily: 'sans-serif',
          }}
        >
          AP
        </div>
      </div>
    ),
    { ...size },
  );
}
