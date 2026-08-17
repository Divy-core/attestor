import type { NextRequest } from 'next/server';

import { env } from '@/lib/env';

/**
 * SSE proxy: browser -> this route -> the deployed control plane.
 *
 * Node runtime, not Edge: the Edge runtime's fetch has its own buffering behaviour and this
 * response must not be buffered anywhere.
 */
export const runtime = 'nodejs';
/** Never prerendered or cached. A cached event stream is a contradiction. */
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

/**
 * Three things this has to get right, all of them learned the hard way in the Python half.
 *
 * **The body is passed through, not read.** `response.body` is piped straight to the client.
 * Reading it into a string here — even to inspect it — would hold every frame until the
 * upstream closed, which on a twelve-minute review is twelve minutes of a page that looks
 * broken. The control plane already sends an immediate `: open` comment and a heartbeat every
 * 15s for the same reason; buffering here would defeat both.
 *
 * **`X-Accel-Buffering: no` is re-sent on the way out.** Cloud Run's own frontend and any
 * proxy in front of it will buffer a `text/event-stream` by default. The header is set by the
 * control plane on the inbound response and it does not survive being re-emitted by a new
 * `Response`, so it is set again explicitly.
 *
 * **`Last-Event-ID` is forwarded.** It is how a reconnect resumes at the right `seq` instead
 * of replaying the run or skipping the middle of it. The browser sends it automatically on
 * its own reconnects; `lib/sse.ts` also passes `?since=` for a fresh `EventSource`, which the
 * browser will not do. Both routes to the same resume point, and both are needed.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
): Promise<Response> {
  const { runId } = await params;
  const incoming = new URL(request.url);

  const target = new URL(
    `${env.controlPlaneUrl}/runs/${encodeURIComponent(runId)}/events`,
  );
  // `use_listener=false` exists so the polling fallback can be exercised deliberately, which
  // is an exit criterion. Only these two params are forwarded; nothing else from the query
  // string reaches the control plane.
  const useListener = incoming.searchParams.get('use_listener');
  if (useListener !== null) target.searchParams.set('use_listener', useListener);
  const since = incoming.searchParams.get('since');

  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const lastEventId = request.headers.get('last-event-id') ?? since;
  if (lastEventId) headers['Last-Event-ID'] = lastEventId;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      headers,
      // The client going away must tear down the upstream connection too, or a closed tab
      // leaves a Cloud Run instance holding a stream open until its own timeout.
      signal: request.signal,
      cache: 'no-store',
    });
  } catch (cause) {
    // A 503 with an SSE content type, so the browser's EventSource reports an error the
    // watchdog can act on rather than a parse failure it cannot.
    const reason = cause instanceof Error ? cause.message : String(cause);
    return new Response(`event: error\ndata: ${JSON.stringify({ reason })}\n\n`, {
      status: 503,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
    });
  }

  if (!upstream.ok || upstream.body === null) {
    return new Response(
      `event: error\ndata: ${JSON.stringify({ status: upstream.status })}\n\n`,
      {
        status: upstream.status === 200 ? 502 : upstream.status,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      },
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
