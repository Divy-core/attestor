import type { NextConfig } from 'next';

/**
 * Standalone output: the Dockerfile copies `.next/standalone` and runs `server.js`, so the
 * runtime image carries only the traced dependencies rather than all of `node_modules`.
 * This is the same reasoning as `--package` on the Python images.
 */
const nextConfig: NextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,
  // The control plane is reached from route handlers on the server, never from the
  // browser, so its URL is a server-only variable and no CORS configuration exists on
  // either side. See `app/api/attestor/[...path]/route.ts`.
  experimental: {
    // SSE responses must not be buffered or the stream arrives in one lump at close.
    proxyTimeout: 3_600_000,
  },
  eslint: {
    ignoreDuringBuilds: false,
  },
  typescript: {
    ignoreBuildErrors: false,
  },
};

export default nextConfig;
