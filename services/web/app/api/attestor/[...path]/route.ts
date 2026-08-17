import type { NextRequest } from 'next/server';

import { env } from '@/lib/env';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * The JSON proxy for everything the browser reads or writes at runtime.
 *
 * Server components fetch through `lib/api/client.ts` directly and never touch this. This
 * exists for the client-side paths: the polling fallback, a manual refresh, and the approval
 * POST. One route rather than a handler per endpoint, because the alternative is eleven files
 * that differ only in a URL.
 *
 * ## An allowlist, not a pass-through
 *
 * A `[...path]` proxy that forwards whatever it is given is an open relay into the control
 * plane's private origin — including its write endpoints, from any page on the internet that
 * can reach this service. So the path is matched against an explicit allowlist of the
 * endpoints the client genuinely needs, keyed by method. Anything else is 404 here and never
 * leaves the process.
 *
 * `POST /uploads`, `POST /reviews` and `POST /reviews/{id}/state` are deliberately NOT on it.
 * Nothing in this UI creates a review or drives a state transition by hand — those are driven
 * by Pub/Sub and by the seeding tools — so exposing them would be surface with no caller.
 */
const ALLOWED: ReadonlyArray<{ method: 'GET' | 'POST'; pattern: RegExp }> = [
  { method: 'GET', pattern: /^reviews$/ },
  { method: 'GET', pattern: /^reviews\/[^/]+$/ },
  { method: 'GET', pattern: /^reviews\/[^/]+\/audit$/ },
  { method: 'GET', pattern: /^reviews\/[^/]+\/armor$/ },
  { method: 'GET', pattern: /^rounds\/[^/]+\/questions$/ },
  { method: 'GET', pattern: /^rounds\/[^/]+\/answers$/ },
  { method: 'GET', pattern: /^registry$/ },
  { method: 'POST', pattern: /^rounds\/[^/]+\/answers\/[^/]+\/approval$/ },
];

/** Query parameters worth forwarding. Everything else is dropped rather than relayed. */
const FORWARDED_PARAMS = new Set(['limit']);

const TIMEOUT_MS = 30_000;

async function proxy(request: NextRequest, segments: string[], method: 'GET' | 'POST') {
  // Rebuilt from the decoded segments rather than taken from the raw URL, so `..` and
  // encoded separators cannot walk out of the allowlisted shape.
  const path = segments.map((s) => encodeURIComponent(s)).join('/');
  const decoded = segments.join('/');

  if (!ALLOWED.some((rule) => rule.method === method && rule.pattern.test(decoded))) {
    return Response.json({ detail: `not a proxied endpoint: ${method} /${decoded}` }, { status: 404 });
  }

  const target = new URL(`${env.controlPlaneUrl}/${path}`);
  for (const [key, value] of new URL(request.url).searchParams) {
    if (FORWARDED_PARAMS.has(key)) target.searchParams.set(key, value);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const upstream = await fetch(target, {
      method,
      headers: method === 'POST' ? { 'Content-Type': 'application/json' } : undefined,
      body: method === 'POST' ? await request.text() : undefined,
      signal: controller.signal,
      cache: 'no-store',
    });
    // The upstream status and body are passed through unchanged. The registry's 503 carries
    // the diagnostic that makes it actionable, and flattening it to a generic error here would
    // throw that away.
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        'Content-Type': upstream.headers.get('content-type') ?? 'application/json',
        'Cache-Control': 'no-store',
      },
    });
  } catch (cause) {
    const detail =
      cause instanceof Error && cause.name === 'AbortError'
        ? `The control plane did not respond within ${TIMEOUT_MS / 1000}s.`
        : cause instanceof Error
          ? cause.message
          : String(cause);
    return Response.json({ detail }, { status: 504 });
  } finally {
    clearTimeout(timer);
  }
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await params;
  return proxy(request, path, 'GET');
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await params;
  return proxy(request, path, 'POST');
}
