import { Mono, cx } from '@/components/ui/primitives';
import type { StreamHealth } from '@/lib/sse';

/**
 * What the page currently believes about its own freshness, said out loud.
 *
 * This is the one piece of chrome worth spending space on. A review is driven entirely by
 * Pub/Sub, so a page showing stale data looks exactly like a page showing a stalled system —
 * and the failure that actually happens is a stream going quiet without erroring, which by
 * definition produces no visible symptom at all.
 *
 * So the indicator distinguishes four things a viewer needs to tell apart:
 *
 *   live      the stream is delivering
 *   polling   the stream went quiet and the fallback took over -- data is still current
 *   stale     neither is delivering; what is on screen may be old
 *   closed    nothing is being watched, because the review is finished
 *
 * "Polling" being visible rather than silent is the point. A fallback that hides itself means
 * nobody ever finds out the primary path is broken.
 *
 * No colour: this is chrome, not status, and the six state hues are spoken for. Weight and a
 * dot do the work.
 */
export function StreamIndicator({
  health,
  detail,
  polling,
  lastSeq,
  gaps,
}: {
  health: StreamHealth;
  detail: string;
  polling: boolean;
  lastSeq: number;
  gaps: number;
}) {
  const label = polling && health !== 'live' ? 'polling' : health;
  const solid = health === 'live';

  return (
    <div className="flex items-center gap-2" title={detail}>
      <span
        aria-hidden
        className={cx(
          'inline-block h-1.5 w-1.5 rounded-full',
          solid ? 'bg-primary' : 'border border-strong bg-transparent',
        )}
      />
      <span className={cx('text-xs', solid ? 'text-primary' : 'text-muted')}>{label}</span>
      {/* The last sequence number, because it is the mechanism. `seq` is monotonic per run and
          is what makes gap detection and resume-after-reconnect possible at all; showing it
          turns "trust me, it reconnected correctly" into something checkable on screen. */}
      <Mono dim title="Highest event sequence applied. Monotonic per run.">
        seq {lastSeq}
      </Mono>
      {gaps > 0 ? (
        <Mono
          dim
          title="Sequence gaps detected and backfilled from the read endpoint. A gap is normal after a reconnect; a gap that was not backfilled would be a hole in the record."
        >
          {gaps} backfilled
        </Mono>
      ) : null}
    </div>
  );
}
