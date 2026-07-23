import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Produces a minimal .next/standalone build (only the files actually
  // needed at runtime) — used by frontend/Dockerfile's multi-stage build.
  output: 'standalone',
};

export default nextConfig;
