import Link from 'next/link';

import { Empty, Label, Mono, cx } from '@/components/ui/primitives';
import type { ReviewCard } from '@/lib/api/client';
import { ago } from '@/lib/format';

/**
 * Reviews as cards, ordered by what needs a person.
 *
 * ## Why a flat list was wrong
 *
 * The previous surface was one row per review: customer, framework, round, state, age. Every
 * fact on it was true and none of them answered the question a person actually opens this
 * page with, which is *which of these is waiting on me*. Answering that meant opening five
 * reviews to find the one with a queue, and a directory that makes you open things to find
 * out what they are is a directory rather than a console.
 *
 * ## The order is the design
 *
 * Sorted by attention, not by time. Held answers first, in descending count — an operator's
 * queue is the most actionable thing this system produces. Then reviews still working, then
 * everything settled, each by recency. A review with 43 answers waiting cannot be below one
 * that was delivered last week because the delivered one is newer.
 *
 * ## What is deliberately not shown
 *
 * A count that could not be taken renders as "not counted", never as zero. `0 held` on a
 * review that actually has 43 waiting is the single most expensive lie this page could tell,
 * and the failure that produces it — an aggregation erroring — is invisible otherwise.
 */

/** Where a review sits in the ordering. Lower sorts first. */
function attention(card: ReviewCard): number {
  if ((card.held ?? 0) > 0) return 0;
  if (card.state === 'awaiting_human' || card.state === 'awaiting_evidence') return 1;
  if (card.state === 'delivered' || card.state === 'failed') return 3;
  return 2;
}

export function sortByAttention(cards: ReviewCard[]): ReviewCard[] {
  return [...cards].sort((a, b) => {
    const rank = attention(a) - attention(b);
    if (rank !== 0) return rank;
    const held = (b.held ?? 0) - (a.held ?? 0);
    if (held !== 0) return held;
    return String(b.created_at).localeCompare(String(a.created_at));
  });
}

/** What this review is waiting on, in the words a person would use. */
function waitingOn(card: ReviewCard): string {
  if ((card.held ?? 0) > 0) {
    return `${card.held} ${card.held === 1 ? 'answer' : 'answers'} waiting on you`;
  }
  switch (card.state) {
    case 'intake':
      return 'Parsing the questionnaire';
    case 'triaging':
      return 'Routing questions to departments';
    case 'drafting':
      return 'Departments are drafting';
    case 'awaiting_evidence':
      return 'Waiting on evidence';
    case 'awaiting_human':
      return 'Waiting on a person';
    case 'assembling':
      return 'Assembling the round';
    case 'delivered':
      return 'Delivered';
    case 'follow_up':
      return 'A follow-up has arrived';
    case 'failed':
      return 'Stopped. Nothing resumes from here';
    case 'blocked':
      return 'Halted, and able to resume from where it stopped';
  }
}

const WORKING = new Set(['intake', 'triaging', 'drafting', 'assembling']);

export function ReviewCards({
  cards,
  emptyHint,
}: {
  cards: ReviewCard[];
  emptyHint: string;
}) {
  if (cards.length === 0) {
    return <Empty title="No review matches this filter." hint={emptyHint} />;
  }
  return (
    <ul className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {sortByAttention(cards).map((card) => (
        <li key={card.review_id}>
          <Card card={card} />
        </li>
      ))}
    </ul>
  );
}

function Card({ card }: { card: ReviewCard }) {
  const held = card.held ?? 0;
  const answered = card.answered ?? 0;
  const total = card.questions ?? 0;
  const fraction = total > 0 ? Math.min(1, answered / total) : 0;
  const working = WORKING.has(card.state);

  return (
    <Link
      href={`/reviews/${card.review_id}`}
      className={cx(
        'flex h-full flex-col gap-4 rounded border border-subtle bg-surface px-6 py-4',
        'no-underline transition-colors hover:border-line hover:no-underline',
        card.archived ? 'opacity-60' : '',
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="truncate text-base font-medium text-primary">{card.customer}</span>
          <span className="text-xs text-muted">
            {card.framework.toUpperCase()} · {card.residency.toUpperCase()} · round{' '}
            {card.current_round}
          </span>
        </div>
        {/* The count that decides whether this card matters, at the size that says so. */}
        {held > 0 ? (
          <div className="flex shrink-0 flex-col items-end gap-1">
            <span className="text-lg tabular-nums text-primary">{held}</span>
            <Label>held</Label>
          </div>
        ) : null}
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-4">
          <span className={cx('text-sm', held > 0 ? 'text-primary' : 'text-secondary')}>
            {waitingOn(card)}
          </span>
          {card.counted ? (
            <Mono dim>
              {answered} / {total}
            </Mono>
          ) : (
            <span
              className="text-xs text-muted"
              title="The counts for this review could not be read"
            >
              not counted
            </span>
          )}
        </div>
        {card.counted && total > 0 ? (
          <span
            role="img"
            aria-label={`${answered} of ${total} answered`}
            className="block h-1 w-full overflow-hidden rounded-sm bg-track"
          >
            <span
              className={cx('block h-full transition-[width] duration-state', 'bg-scale')}
              style={{ width: `${(fraction * 100).toFixed(1)}%` }}
            />
          </span>
        ) : null}
      </div>

      <div className="mt-auto flex flex-wrap items-baseline gap-x-6 gap-y-1">
        {card.deadline ? (
          <span className="text-xs text-flagged" title="The date the customer asked for">
            due {card.deadline}
          </span>
        ) : null}
        {working ? <span className="text-xs text-accent-text">working</span> : null}
        <span className="text-xs text-muted">{ago(card.created_at)}</span>
        {card.archived ? <span className="text-xs text-muted">archived</span> : null}
        <Mono dim className="ml-auto">
          {card.review_id}
        </Mono>
      </div>
    </Link>
  );
}
