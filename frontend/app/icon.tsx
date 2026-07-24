import { ImageResponse } from 'next/og';

export const size = { width: 32, height: 32 };
export const contentType = 'image/png';

export default function Icon() {
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
            fontSize: 15,
            fontWeight: 900,
            color: '#4d7fff',
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
