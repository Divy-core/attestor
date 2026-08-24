'use client';

import Link from 'next/link';

import { ThemeToggle } from '@/components/layout/ThemeToggle';
import { NewReviewRailAction } from '@/components/review/NewReview';
import { Mono, cx } from '@/components/ui/primitives';
import type { ReviewCard } from '@/lib/api/client';
import { ago } from '@/lib/format';

/**
 * One conversation per review, newest attention first.
 *
 * This replaced the section rail. A console's sidebar lists the pages it has; a chat
 * application's sidebar lists the conversations you are having, and the pages go
 * underneath in small type. That inversion is the whole of what makes this read as a
 * product rather than as a dashboard.
 *
 * ## Collapsing
 *
 * Under 1200px the rail keeps only the status dot, and the customer name lives in the
 * `title`. Below that width the centre column is already at its 768px cap, so every pixel
 * the rail gives back is one the reader gets — and a name is recoverable from a hover,
 * where a truncated column of text is not recoverable from anything.
 */

/** What a conversation is waiting on, in the words a person would use. */
function line(review: ReviewCard): string {
  const held = review.held ?? 0;
  if (held > 0) return `${held} waiting on you`;
  switch (review.state) {
    case 'intake':
      return 'Parsing';
    case 'triaging':
      return 'Routing';
    case 'drafting':
      return 'Drafting';
    case 'awaiting_evidence':
      return 'Waiting on evidence';
    case 'awaiting_human':
      return 'Waiting on a person';
    case 'assembling':
      return 'Assembling';
    case 'delivered':
      return 'Delivered';
    case 'follow_up':
      return 'Follow-up';
    case 'blocked':
      return 'Halted';
    case 'failed':
      return 'Stopped';
  }
}

const WORKING = new Set(['intake', 'triaging', 'drafting', 'assembling']);

export function ConversationRail({
  reviews,
  activeId,
}: {
  reviews: ReviewCard[];
  activeId: string | null;
}) {
  const ordered = [...reviews].sort((a, b) => {
    const held = (b.held ?? 0) - (a.held ?? 0);
    if (held !== 0) return held;
    return String(b.created_at).localeCompare(String(a.created_at));
  });

  return (
    <nav
      aria-label="Conversations"
      className={cx(
        'flex h-full shrink-0 flex-col border-r border-subtle',
        'w-conversations-collapsed xl:w-conversations',
      )}
    >
      <div className="flex shrink-0 flex-col gap-1 px-2 py-4 xl:px-3">
        <Link
          href="/"
          className="mb-4 truncate px-2 text-md text-primary no-underline hover:no-underline"
        >
          <span className="hidden xl:inline">Attestor</span>
          <span className="xl:hidden">A</span>
        </Link>
        <NewReviewRailAction />
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 xl:px-3">
        {ordered.map((review) => {
          const active = review.review_id === activeId;
          const held = review.held ?? 0;
          return (
            <li key={review.review_id}>
              <Link
                href={`/reviews/${review.review_id}`}
                aria-current={active ? 'page' : undefined}
                title={`${review.customer} — ${line(review)}`}
                className={cx(
                  'flex items-center gap-2 rounded-sm px-2 py-2 no-underline transition-colors',
                  'hover:no-underline',
                  active ? 'bg-active' : 'hover:bg-hover',
                )}
              >
                <span
                  aria-hidden
                  className={cx(
                    'h-2 w-2 shrink-0 rounded-sm',
                    held > 0
                      ? 'bg-flagged'
                      : WORKING.has(review.state)
                        ? 'bg-cited pulse-working'
                        : 'bg-track',
                  )}
                />
                <span className="hidden min-w-0 flex-1 flex-col xl:flex">
                  <span
                    className={cx(
                      'truncate text-sm',
                      active ? 'text-primary' : 'text-secondary',
                    )}
                  >
                    {review.customer}
                  </span>
                  <span className="truncate text-xs text-muted">{line(review)}</span>
                </span>
                <span className="hidden shrink-0 xl:block">
                  <Mono dim>{ago(review.created_at)}</Mono>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>

      <div className="shrink-0 border-t border-subtle px-2 py-3 xl:px-3">
        <ul className="flex flex-col">
          {SECTIONS.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                title={item.label}
                className="block truncate rounded-sm px-2 py-1 text-xs text-muted no-underline transition-colors hover:bg-hover hover:text-primary hover:no-underline"
              >
                <span className="hidden xl:inline">{item.label}</span>
                <span className="xl:hidden">{item.short}</span>
              </Link>
            </li>
          ))}
        </ul>
        <div className="hidden pt-3 xl:block">
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}

const SECTIONS: ReadonlyArray<{ href: string; label: string; short: string }> = [
  { href: '/fleet', label: 'Fleet', short: 'Fl' },
  { href: '/connections', label: 'Connections', short: 'Cx' },
  { href: '/registry', label: 'Registry', short: 'Rg' },
  { href: '/traces', label: 'Audit', short: 'Au' },
  { href: '/about', label: 'About', short: 'Ab' },
];
