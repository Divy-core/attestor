import { cx } from '@/components/ui/primitives';
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
  observed,
  reads,
}: {
  health: StreamHealth;
  detail: string;
  polling: boolean;
  lastSeq: number;
  gaps: number;
  /** Events seen on the stream this session. */
  observed: number;
  /** Reads those events caused. Fewer, because bursts are coalesced. */
  reads: number;
}) {
  const label = polling && health !== 'live' ? 'polling' : health;
  const solid = health === 'live';

  // Everything except the dot and the word is in the tooltip. The sequence number, the
  // backfills and the events-to-reads ratio are all worth keeping -- `seq` is what makes
  // gap detection checkable rather than asserted, and the ratio is the coalescing that
  // stopped a 429 on a 949-event run -- but a person watching their questionnaire being
  // answered is not the audience for any of them.
  const instrumentation = [
    detail,
    `seq ${lastSeq}`,
    gaps > 0 ? `${gaps} gap${gaps === 1 ? '' : 's'} backfilled` : '',
    observed > 0 ? `${observed} events coalesced into ${reads} reads` : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="flex items-center gap-2" title={instrumentation}>
      <span
        aria-hidden
        className={cx(
          'inline-block h-2 w-2 rounded-sm',
          solid ? 'bg-primary' : 'border border-strong bg-transparent',
        )}
      />
      <span className={cx('text-xs', solid ? 'text-primary' : 'text-muted')}>{label}</span>
    </div>
  );
}
