/**
 * The fallback, and why it is armed by a clock rather than by an error handler.
 *
 * Mynd's lesson, restated in the plan of record: a realtime channel can silently no-op. The
 * failure that actually happens is not a stream that errors — it is a stream that goes quiet
 * while reporting nothing at all, and a fallback wired to `onerror` sits idle through exactly
 * that, which is the only case where a fallback was needed.
 *
 * So the trigger is staleness, measured by `lib/sse.ts`'s heartbeat watchdog, and this module
 * only knows how to poll politely once told to start.
 *
 * ## Self-scheduling, never overlapping
 *
 * `setInterval` fires on a fixed cadence regardless of whether the previous request has come
 * back. Against a slow control plane that queues requests, and the queue never drains: each
 * tick adds a request, and the responses arrive out of order so the newest answer can be
 * overwritten by an older one. This schedules the *next* poll from the completion of the
 * previous one, so exactly one request is ever in flight and the ordering is total.
 *
 * ## It backs off while nothing is happening
 *
 * A review parked in `awaiting_human` for three weeks does not need a request every two
 * seconds. The interval grows when a poll returns nothing new and resets the moment it does,
 * so an idle review costs almost nothing and an active one is responsive.
 */

export type PollHandle = {
  /** Begin polling, or reset the interval if already running. Idempotent. */
  start: () => void;
  /** Stop. Idempotent, and safe to call from a React cleanup that runs twice. */
  stop: () => void;
  /** Poll once, immediately, without disturbing the schedule. For a manual refresh. */
  now: () => void;
  readonly running: boolean;
};

export type PollOptions = {
  /** Cadence when something changed on the last poll. */
  activeMs?: number;
  /** Ceiling once nothing has changed for a while. */
  idleMs?: number;
  /** How many unchanged polls before the interval starts growing. */
  patience?: number;
};

/**
 * @param fetchOnce Runs one poll. Resolves `true` if anything changed — that is what keeps
 *   the cadence fast. Rejections are swallowed *for scheduling purposes only*: one failed
 *   poll must not stop the loop, and the caller is responsible for surfacing the error it
 *   already received. This is not the "failure becomes empty" mistake — nothing here reports
 *   success, it only decides when to try again.
 */
export function createPoller(
  fetchOnce: () => Promise<boolean>,
  { activeMs = 2_500, idleMs = 30_000, patience = 4 }: PollOptions = {},
): PollHandle {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running = false;
  let inFlight = false;
  let quietRuns = 0;

  function interval(): number {
    if (quietRuns < patience) return activeMs;
    // Doubling from the active cadence, capped. Reaches the ceiling in a few unchanged polls
    // rather than crawling there.
    const grown = activeMs * 2 ** (quietRuns - patience + 1);
    return Math.min(grown, idleMs);
  }

  function schedule(): void {
    if (!running) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(run, interval());
  }

  async function run(): Promise<void> {
    if (!running || inFlight) return;
    inFlight = true;
    try {
      const changed = await fetchOnce();
      quietRuns = changed ? 0 : quietRuns + 1;
    } catch {
      // Treated as "nothing changed" so the interval backs off instead of retrying a broken
      // dependency every 2.5 seconds. The caller has the error.
      quietRuns += 1;
    } finally {
      inFlight = false;
      schedule();
    }
  }

  return {
    start(): void {
      quietRuns = 0;
      if (running) {
        // Already going: reset to the fast cadence rather than starting a second loop.
        schedule();
        return;
      }
      running = true;
      void run();
    },
    stop(): void {
      running = false;
      if (timer) clearTimeout(timer);
      timer = null;
    },
    now(): void {
      quietRuns = 0;
      void run();
    },
    get running(): boolean {
      return running;
    },
  };
}
