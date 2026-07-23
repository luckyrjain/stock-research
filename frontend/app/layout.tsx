import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import { JetBrains_Mono } from 'next/font/google';
import './globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-sans' });
const mono  = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono' });

export const metadata: Metadata = {
  title: 'AlphaPulse — AI Stock Research',
  description: 'Institutional-grade AI stock research for Indian markets — NSE & BSE',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="font-sans">
        {children}
        <footer className="max-w-6xl mx-auto px-6 py-6 text-[11px] text-muted/50 text-center">
          AlphaPulse generates recommendations with AI models from public data. Nothing here is
          investment advice — verify independently and consult a registered financial advisor
          before trading.
        </footer>
      </body>
    </html>
  );
}
