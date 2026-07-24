import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'AlphaPulse — AI Stock Research',
    short_name: 'AlphaPulse',
    description: 'Institutional-grade AI stock research for Indian markets — NSE & BSE',
    start_url: '/',
    display: 'standalone',
    background_color: '#0b1120',
    theme_color: '#0b1120',
    icons: [
      { src: '/manifest-icons/192', sizes: '192x192', type: 'image/png' },
      { src: '/manifest-icons/512', sizes: '512x512', type: 'image/png' },
    ],
  };
}
