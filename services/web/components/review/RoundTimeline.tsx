import { Mono, cx } from '@/components/ui/primitives';
import { absolute, ago } from '@/lib/format';
import { reviewStateTone } from '@/lib/states';
import type { RoundRow } from '@/lib/api/client';

/**
 * Rounds along a horizontal rule, with the gaps between them drawn to scale.
 *
 * The dormancy is the point. A vendor review is not one sitting: round 1 goes out, three weeks
 * pass, the customer comes back with follow-ups, and the answers have to be consistent with
 * what was said the first time. That gap is the reason Memory Bank is in this architecture at
 * all, and a timeline with evenly spaced ticks hides exactly the thing worth showing.
 *
 * So the spacing is proportional to elapsed time and the gap is labelled in days. Twenty-two
 * days of nothing, rendered as twenty-two days of nothing, makes the resume beat legible in one
 * glance instead of requiring a sentence of narration.
 *
 * No colour on the states: a review being in `assembling` is a position in a sequence, not a
 * verdict, and the six state hues are spoken for by answers.
 */
export function RoundTimeline({
  rounds,
  createdAt,
}: {
  rounds: RoundRow[];
  createdAt: string;
}) {
  if (rounds.length === 0) return null;

  const ordered = [...rounds].sort((a, b) => a.ordinal - b.ordinal);
  const start = Date.parse(createdAt);
  const now = Date.now();
  const span = Math.max(now - start, 1);

  return (
    <section
      aria-label="Rounds"
      className="flex flex-col gap-2 border-b border-subtle px-5 py-3"
    >
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wide text-muted">Rounds</span>
        <span className="text-xs text-muted">
          {Math.round(span / 86_400_000)} days since intake
        </span>
      </div>

      <div className="relative h-8">
        {/* The rule the rounds hang from. Solid, not dashed: the review is continuous even when
            nothing is happening, which is the claim the dormancy makes. */}
        <div className="absolute left-0 right-0 top-3 h-px bg-line" />

        {ordered.map((round, index) => {
          const at = Date.parse(round.received_at);
          const offset = Number.isNaN(at) ? 0 : ((at - start) / span) * 100;
          const tone = reviewStateTone(round.state);
          const previous = index > 0 ? ordered[index - 1] : undefined;
          const gapDays =
            previous !== undefined
              ? Math.round((at - Date.parse(previous.received_at)) / 86_400_000)
              : 0;

          return (
            <div
              key={round.round_id}
              className="absolute top-0 flex flex-col items-start"
              // Clamped so the last round on a live review does not sit half off the right edge.
              style={{ left: `${Math.min(92, Math.max(0, offset))}%` }}
            >
              <span
                aria-hidden
                className={cx(
                  'mt-2.5 h-2 w-2 rounded-full',
                  tone === 'terminal'
                    ? 'bg-primary'
                    : tone === 'blocked'
                      ? 'border-2 border-primary bg-base'
                      : 'bg-strong',
                )}
              />
              <div className="mt-1 flex items-baseline gap-2 whitespace-nowrap">
                <span className="text-xs font-medium text-primary">R{round.ordinal}</span>
                <span className="text-xs text-muted" title={absolute(round.received_at)}>
                  {ago(round.received_at)}
                </span>
                {gapDays >= 1 ? (
                  <span
                    className="text-xs text-secondary"
                    title="Elapsed since the previous round. This is the interval a commitment has to survive."
                  >
                    +{gapDays}d
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {ordered.map((round) => (
          <span key={round.round_id} className="flex items-baseline gap-2">
            <span className="text-xs text-muted">R{round.ordinal}</span>
            <span className="text-xs text-secondary">{round.state}</span>
            <Mono dim>{round.round_id}</Mono>
          </span>
        ))}
      </div>
    </section>
  );
}
