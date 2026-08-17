/**
 * The event stream, and the three ways it fails.
 *
 * A review is driven entirely by Pub/Sub, so the browser is a spectator with no request in
 * flight to tell it anything. The stream is the only thing making the page live, which makes
 * its failure modes the interesting part of this module rather than an afterthought.
 *
 * ## 1. It errors
 *
 * The easy one. `EventSource` fires `onerror`, and it reconnects on its own — but only for
 * transport errors, and it loses its place doing so. Handled by reconnecting with
 * `Last-Event-ID` (see `seq`, below).
 *
 * ## 2. It goes quiet without erroring
 *
 * The one that actually happens, and the reason `lib/poll.ts` exists. A buffering proxy, a
 * Cloud Run instance recycling, a listener that stops delivering — in every case the socket
 * stays open, `onerror` never fires, and the page sits there looking calm and being wrong.
 *
 * So this module runs a **heartbeat watchdog** rather than trusting `onerror`. The control
 * plane sends a `heartbeat` frame every 15 seconds; if none arrives within
 * `STALE_AFTER_MS`, the stream is declared stale regardless of what the socket says. A
 * fallback wired to `onerror` would sit idle through precisely this failure, which is why the
 * exit criterion asks for the disabled case and the silent case separately.
 *
 * ## 3. It reconnects and skips events
 *
 * Every event in the protocol carries a monotonic `seq` for one run. On reconnect the browser
 * sends `Last-Event-ID`, the control plane resumes from `since_seq`, and this module still
 * checks: if the next `seq` is not the one expected, the gap is reported so the caller can
 * backfill from the REST read rather than rendering a review with a hole in it.
 *
 * Detecting the gap and detecting the reconnect are different things. A gap can open without
 * a reconnect — an event dropped between listener and client — and a reconnect can happen
 * with no gap at all. Only `seq` can tell them apart, and it is in the frozen protocol
 * precisely so this check is possible.
 */

import type { AttestorEvent } from '@/lib/types/generated';

/** No heartbeat for this long and the stream is treated as dead, open socket or not.
 *  15s heartbeat interval, so this is two missed beats plus slack. */
export const STALE_AFTER_MS = 40_000;

/** Reconnect backoff, capped. `EventSource` has its own retry, but a stream that is being
 *  refused needs to back off rather than hammer. */
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 15_000;

export type StreamHealth = 'connecting' | 'live' | 'stale' | 'closed';

export type StreamHandlers = {
  /** One protocol event, in order, with gaps already reported. */
  onEvent: (event: AttestorEvent) => void;
  /** Health changed. Drives the indicator and arms the polling fallback. */
  onHealth: (health: StreamHealth, detail: string) => void;
  /**
   * A `seq` gap was observed. The caller refetches rather than guessing what was missed.
   * `from` is the last seq seen, `to` the seq that arrived.
   */
  onGap: (from: number, to: number) => void;
};

export type StreamHandle = { close: () => void };

/**
 * Subscribe to one run's events.
 *
 * Returns a handle whose `close()` is idempotent and safe from a React cleanup that may run
 * twice under Strict Mode.
 */
export function openRunStream(
  runId: string,
  handlers: StreamHandlers,
  options: { useListener?: boolean } = {},
): StreamHandle {
  let source: EventSource | null = null;
  let watchdog: ReturnType<typeof setTimeout> | null = null;
  let reconnect: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0;
  let lastSeq = 0;
  let closed = false;

  const path = `/api/attestor/stream/${encodeURIComponent(runId)}${
    options.useListener === false ? '?use_listener=false' : ''
  }`;

  function armWatchdog(): void {
    if (watchdog) clearTimeout(watchdog);
    watchdog = setTimeout(() => {
      if (closed) return;
      // Deliberately NOT closing the socket. It may still be alive and may resume; what
      // matters is that the page stops believing it is current, so the fallback takes over
      // and the indicator tells the truth. Closing here would also destroy the
      // `Last-Event-ID` continuity that makes the resume cheap.
      handlers.onHealth('stale', `No heartbeat for ${Math.round(STALE_AFTER_MS / 1000)}s.`);
    }, STALE_AFTER_MS);
  }

  function scheduleReconnect(reason: string): void {
    if (closed) return;
    source?.close();
    source = null;
    attempts += 1;
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempts - 1), RECONNECT_MAX_MS);
    handlers.onHealth('stale', `${reason} Reconnecting in ${Math.round(delay / 1000)}s.`);
    reconnect = setTimeout(connect, delay);
  }

  function connect(): void {
    if (closed) return;
    handlers.onHealth('connecting', 'Opening the event stream.');

    // The browser sends `Last-Event-ID` itself on ITS OWN reconnects, but not on a fresh
    // EventSource — so the resume point is passed explicitly. Without it, a reconnect after
    // a stale period replays from zero or resumes from nothing depending on the server, and
    // both are wrong.
    const url = lastSeq > 0 ? `${path}${path.includes('?') ? '&' : '?'}since=${lastSeq}` : path;
    const es = new EventSource(url);
    source = es;

    es.onopen = () => {
      attempts = 0;
      handlers.onHealth('live', 'Streaming.');
      armWatchdog();
    };

    es.onmessage = (message: MessageEvent<string>) => {
      // Any frame at all proves the pipe is moving, heartbeat or not.
      armWatchdog();
      if (source === es) handlers.onHealth('live', 'Streaming.');

      let parsed: unknown;
      try {
        parsed = JSON.parse(message.data);
      } catch {
        // A malformed frame is not a reason to tear down a working stream, and it is not a
        // reason to pretend it was an event either.
        return;
      }
      if (!parsed || typeof parsed !== 'object') return;
      const event = parsed as AttestorEvent;

      const seq = typeof event.seq === 'number' ? event.seq : 0;
      if (seq > 0) {
        if (lastSeq > 0 && seq > lastSeq + 1) handlers.onGap(lastSeq, seq);
        // Guard against a replayed frame after a reconnect: the resume is inclusive on some
        // paths, and re-delivering an event the page has already applied would double-count
        // a citation.
        if (seq <= lastSeq) return;
        lastSeq = seq;
      }

      // Heartbeats keep the watchdog fed and are not application events.
      if (event.type === 'heartbeat') return;
      handlers.onEvent(event);
    };

    es.onerror = () => {
      if (closed || source !== es) return;
      // `readyState === CLOSED` means the browser has given up and will not retry; anything
      // else means it is retrying on its own and a second EventSource would duplicate the
      // subscription.
      if (es.readyState === EventSource.CLOSED) {
        scheduleReconnect('The stream closed.');
      } else {
        handlers.onHealth('stale', 'The stream dropped. The browser is retrying.');
      }
    };
  }

  connect();

  return {
    close(): void {
      if (closed) return;
      closed = true;
      if (watchdog) clearTimeout(watchdog);
      if (reconnect) clearTimeout(reconnect);
      source?.close();
      source = null;
      handlers.onHealth('closed', 'Stopped.');
    },
  };
}
