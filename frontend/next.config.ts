import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Produces a minimal .next/standalone build (only the files actually
  // needed at runtime) — used by frontend/Dockerfile's multi-stage build.
  output: 'standalone',

  // Baseline security headers — this origin serves /login, /auth/verify,
  // and /portfolio-aggregator (broker credential entry), and previously
  // shipped none at all, leaving every page framable and with no
  // MIME-sniffing protection. Deliberately not a full Content-Security-
  // Policy here: this app has no dangerouslySetInnerHTML and no inline
  // scripts, but a CSP strict enough to matter needs verifying against a
  // real production build (nonces/hashes for Next's own injected scripts)
  // rather than added blind — tracked as a follow-up, not silently assumed
  // covered by the headers below.
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        ],
      },
    ];
  },
};

export default nextConfig;
