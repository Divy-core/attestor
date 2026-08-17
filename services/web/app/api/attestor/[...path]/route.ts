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
 * ## The allowlist grew in Phase 6.5, and that is a posture change rather than a fix
 *
 * It used to carry seven reads and one write, with a note saying that `POST /uploads`,
 * `POST /reviews` and `POST /reviews/{id}/rounds` were deliberately absent because nothing in
 * the UI started work. That was true and it was the problem: every review on the live site had
 * been created from a terminal, and the interface was a viewer for work a developer ran.
 *
 * So the product now has an entrance, and three write paths are on the list. What has *not*
 * changed is the shape of the protection:
 *
 *   - the web service account still holds only `roles/logging.logWriter`;
 *   - every write still executes under the control plane's identity, never the browser's;
 *   - the paths are still an explicit method-and-path allowlist, not a pass-through;
 *   - and the control plane now requires a shared token on all of them, which this handler
 *     adds server-side and the browser never sees.
 *
 * The blast radius argument is unchanged. What changed is that a person can hand work in.
 */
const ALLOWED: ReadonlyArray<{ method: 'GET' | 'POST'; pattern: RegExp }> = [
  { method: 'GET', pattern: /^reviews$/ },
  { method: 'GET', pattern: /^reviews\/[^/]+$/ },
  { method: 'GET', pattern: /^reviews\/[^/]+\/audit$/ },
  { method: 'GET', pattern: /^reviews\/[^/]+\/armor$/ },
  { method: 'GET', pattern: /^reviews\/[^/]+\/export\/manifest$/ },
  { method: 'GET', pattern: /^rounds\/[^/]+\/questions$/ },
  { method: 'GET', pattern: /^rounds\/[^/]+\/answers$/ },
  { method: 'GET', pattern: /^registry$/ },
  { method: 'POST', pattern: /^rounds\/[^/]+\/answers\/[^/]+\/approval$/ },
  // The entrance. In the order the New review flow calls them.
  { method: 'POST', pattern: /^uploads$/ },
  { method: 'POST', pattern: /^reviews$/ },
  { method: 'POST', pattern: /^reviews\/[^/]+\/rounds$/ },
];

/**
 * `GET /reviews/{id}/export` is deliberately **not** proxied.
 *
 * It returns a multi-megabyte binary, and relaying that through this handler would buffer a
 * spreadsheet through a Node process for no benefit — the same reasoning that keeps uploads
 * off the control plane. The review page links to the control plane's own URL instead, which
 * is a read and needs no token. The manifest, which is small JSON the download control reads
 * before the click, is proxied.
 */

/** Query parameters worth forwarding. Everything else is dropped rather than relayed. */
const FORWARDED_PARAMS = new Set(['limit', 'round_id', 'format']);

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

  // The token is attached here and only here. It comes from the server environment, so it is
  // never in the client bundle and never in the browser's network tab.
  const headers: Record<string, string> = {};
  if (method === 'POST') {
    headers['Content-Type'] = 'application/json';
    if (env.writeToken) headers['X-Attestor-Token'] = env.writeToken;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const upstream = await fetch(target, {
      method,
      headers: Object.keys(headers).length > 0 ? headers : undefined,
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
