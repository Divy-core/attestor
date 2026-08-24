import Link from 'next/link';

import { AppShell } from '@/components/layout/AppShell';
import { ReviewCards } from '@/components/review/ReviewCards';
import { Failure, cx } from '@/components/ui/primitives';
import { ApiError, api, type ReviewCard } from '@/lib/api/client';

export const dynamic = 'force-dynamic';

/**
 * Every review, as cards, ordered by what needs a person.
 *
 * ## Why archived is a filter and not a delete
 *
 * Eight of the reviews in this project are debris from the Phase 6.5 quota work: runs whose
 * drafting partitions exhausted their delivery attempts hours before anyone looked. Before
 * Phase 7 they were the first thing on the landing page, and the honest reading of a list
 * that is majority `failed` is "this system does not work".
 *
 * They are still real history — `docs/proof/` references several of them by id, and the
 * measured record is the point of this repository — so they are hidden, not removed, and
 * the control says how many there are rather than pretending there are none.
 *
 * ## The filters live in the URL
 *
 * `?state=awaiting_human&archived=1` is a link a person can send. A filter held in component
 * state cannot be shared, cannot be reloaded into, and cannot be linked to from the command
 * palette — and "here is what I am looking at" is the most common thing anyone wants to do
 * with a console.
 */

type Search = { state?: string; archived?: string };

export default async function ReviewsPage({
  searchParams,
}: {
  searchParams: Promise<Search>;
}) {
  const { state: stateFilter, archived } = await searchParams;
  const showArchived = archived === '1';

  let all: ReviewCard[] = [];
  let error: string | null = null;
  try {
    // Archived rows are fetched too, because the control that reveals them has to name how
    // many there are, and a server that had already filtered them out could not say.
    all = await api.reviewBoard(100, true);
  } catch (cause) {
    error = cause instanceof ApiError ? cause.human : String(cause);
  }

  const archivedCount = all.filter((review) => review.archived).length;
  const visible = all.filter((review) => (showArchived ? true : !review.archived));
  const rows = visible.filter((review) => (stateFilter ? review.state === stateFilter : true));
  const held = visible.reduce((total, review) => total + (review.held ?? 0), 0);

  // States present in what is CURRENTLY VISIBLE, plus whichever is active. Listing every
  // state in the collection puts `failed` on screen while no failed review is shown, and
  // clicking it finds nothing unless archived is also on — the same defect as a filter chip
  // reading `Denied 0`: a control that cannot do anything, beside ones that can.
  const present = new Set<string>(visible.map((review) => review.state));
  // The active filter stays listed even when it matches nothing, so the control that got
  // you here is still there to clear.
  if (stateFilter) present.add(stateFilter);
  const states = [...present].sort();

  function href(next: Partial<Search>): string {
    const params = new URLSearchParams();
    const state = next.state !== undefined ? next.state : stateFilter;
    const arch = next.archived !== undefined ? next.archived : showArchived ? '1' : '';
    if (state) params.set('state', state);
    if (arch) params.set('archived', '1');
    const query = params.toString();
    return query ? `/reviews?${query}` : '/reviews';
  }

  return (
    <AppShell
      pathname="/reviews"
      title="Reviews"
      meta={held > 0 ? `${held} answers waiting on a person` : `${rows.length} of ${all.length}`}
      reviews={all.filter((review) => !review.archived)}
    >
      <div className="mx-auto flex w-full max-w-page flex-col gap-6 px-6 py-8">
        {error !== null ? (
          <Failure what="The control plane could not be reached." detail={error} />
        ) : (
          <>
            <nav className="flex flex-wrap items-center gap-2" aria-label="Filters">
              <Chip href={href({ state: '' })} active={!stateFilter}>
                All states
              </Chip>
              {states.map((state) => (
                <Chip key={state} href={href({ state })} active={stateFilter === state}>
                  {state.replace(/_/g, ' ')}
                </Chip>
              ))}
              {archivedCount > 0 ? (
                <Link
                  href={href({ archived: showArchived ? '' : '1' })}
                  className={cx(
                    'ml-auto rounded-sm px-2 py-1 text-sm no-underline hover:no-underline',
                    showArchived ? 'bg-active text-primary' : 'text-secondary hover:bg-hover',
                  )}
                  title="Reviews taken out of the working set"
                >
                  {showArchived ? 'Hide' : 'Show'} archived ({archivedCount})
                </Link>
              ) : null}
            </nav>

            <ReviewCards
              cards={rows}
              emptyHint={
                archivedCount > 0 && !showArchived
                  ? `${archivedCount} archived review${archivedCount === 1 ? '' : 's'} are hidden. Show them, or clear the state filter.`
                  : 'Start one from New review, or email the watched mailbox.'
              }
            />
          </>
        )}
      </div>
    </AppShell>
  );
}

function Chip({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={cx(
        'rounded-sm px-2 py-1 text-sm no-underline hover:no-underline',
        active ? 'bg-active text-primary' : 'text-secondary hover:bg-hover',
      )}
    >
      {children}
    </Link>
  );
}
