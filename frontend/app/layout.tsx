import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import { JetBrains_Mono } from 'next/font/google';
import ServiceWorkerRegistration from '@/components/service-worker-registration';
import './globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-sans' });
const mono  = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono' });

export const metadata: Metadata = {
  title: 'AlphaPulse — AI Stock Research',
  description: 'Institutional-grade AI stock research for Indian markets — NSE & BSE',
  appleWebApp: {
    title: 'AlphaPulse',
    statusBarStyle: 'black-translucent',
  },
};

export const viewport: Viewport = {
  themeColor: '#0b1120',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="font-sans">
        <ServiceWorkerRegistration />
        {children}
        {/* Rendered at full `muted` (6.12:1 on bg), not the /50 it used to be
            (2.06:1 with the old #6b7fa8 token). A disclaimer is only worth
            anything if it is legible — this was the least readable text in
            the product. */}
        <footer className="max-w-6xl mx-auto px-6 py-6 text-[11px] text-muted text-center">
          AlphaPulse is <strong className="font-semibold">not registered with SEBI</strong> as a
          Research Analyst or Investment Adviser. It generates recommendations with AI models from
          public data for informational purposes only. Nothing here is investment advice — verify
          independently and consult a SEBI-registered adviser before trading.
        </footer>
      </body>
    </html>
  );
}
