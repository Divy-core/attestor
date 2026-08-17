import type { NextRequest } from 'next/server';

import { env } from '@/lib/env';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * The export download, streamed rather than relayed.
 *
 * Its own route handler rather than a line on the JSON proxy's allowlist, for one reason: this
 * response is a multi-megabyte binary and the JSON proxy would be the wrong shape for it —
 * different content type, different headers to forward, and a `Content-Disposition` that has
 * to survive intact or the browser saves the file as `route.ts`.
 *
 * ## Why it is proxied at all
 *
 * The obvious alternative is to link the browser straight at the control plane's own URL. That
 * is one fewer hop and it was the first version. It was rejected because it would put
 * `CONTROL_PLANE_URL` into the rendered HTML, and `lib/env.ts` keeps that value server-side on
 * purpose: no CORS surface on either service, and the control plane's origin does not appear in
 * view source during a recorded demo. Phase 6.5 made that origin a *write* endpoint, so
 * publishing it is a worse trade now than it was when everything on it was a read.
 *
 * ## Streamed, not buffered
 *
 * `new Response(upstream.body)` hands the browser the upstream stream. Nothing accumulates in
 * this process, so a 300-page evidence pack costs a held connection rather than 40MB of heap.
 * That is the distinction that makes proxying acceptable here while an *upload* through this
 * service would still be wrong — an upload has no upstream to stream from until the whole body
 * has arrived.
 *
 * ## No token
 *
 * The export is a read. It returns what `GET /rounds/{id}/answers` already returns, formatted
 * for a human, so it is not behind the write guard and nothing here adds a header.
 */

/** Only what the export endpoint takes. Anything else is dropped rather than relayed. */
const FORWARDED = new Set(['format', 'round_id']);

/** Generous: a 312-question evidence pack is a few hundred pages of composition. */
const TIMEOUT_MS = 120_000;

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ reviewId: string }> },
): Promise<Response> {
  const { reviewId } = await params;

  const target = new URL(
    `${env.controlPlaneUrl}/reviews/${encodeURIComponent(reviewId)}/export`,
  );
  for (const [key, value] of new URL(request.url).searchParams) {
    if (FORWARDED.has(key)) target.searchParams.set(key, value);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const upstream = await fetch(target, { signal: controller.signal, cache: 'no-store' });

    if (!upstream.ok) {
      // An error body is small JSON, and the control plane's own words are the diagnostic --
      // its 409 says *why* the workbook cannot be built. Passed through unchanged.
      return new Response(upstream.body, {
        status: upstream.status,
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
      });
    }

    const headers = new Headers({ 'Cache-Control': 'no-store' });
    for (const name of [
      'content-type',
      'content-disposition',
      'content-length',
      'x-attestor-rows',
      'x-attestor-sendable',
      'x-attestor-source',
    ]) {
      const value = upstream.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    return new Response(upstream.body, { status: 200, headers });
  } catch (cause) {
    const detail =
      cause instanceof Error && cause.name === 'AbortError'
        ? `The control plane did not finish the export within ${TIMEOUT_MS / 1000}s.`
        : cause instanceof Error
          ? cause.message
          : String(cause);
    return Response.json({ detail }, { status: 504 });
  } finally {
    // Cleared on the way out. The stream is already handed to the platform by then; the timer
    // guards the time to *first* response, which is when the control plane is composing.
    clearTimeout(timer);
  }
}
