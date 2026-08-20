import Link from 'next/link';

import { AppShell } from '@/components/layout/AppShell';
import { NewReviewButton } from '@/components/review/NewReview';
import { Empty, Failure, LifecycleBadge, Mono, Panel, cx } from '@/components/ui/primitives';
import { ApiError, api, type ReviewRow } from '@/lib/api/client';
import { ago } from '@/lib/format';
import { reviewStateTone } from '@/lib/states';

export const dynamic = 'force-dynamic';

/**
 * Every review, with the archived ones behind a control that names the count.
 *
 * ## Why archived is a filter and not a delete
 *
 * Eight of the thirteen reviews in this project are debris from the Phase 6.5 quota work:
 * runs whose drafting partitions exhausted their delivery attempts hours before anyone
 * looked. Before Phase 7 they were the first thing on the landing page, and the honest
 * reading of a list that is majority `failed` is "this system does not work".
 *
 * They are still real history. `docs/proof/` references several of them by id, and the
 * measured record is the point of this repository — so they are hidden, not removed, and the
 * control says how many there are rather than pretending there are none.
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

  let all: ReviewRow[] = [];
  let error: string | null = null;
  try {
    all = await api.listReviews(100);
  } catch (cause) {
    error = cause instanceof ApiError ? cause.human : String(cause);
  }

  const archivedCount = all.filter((review) => review.archived).length;
  const rows = all
    .filter((review) => (showArchived ? true : !review.archived))
    .filter((review) => (stateFilter ? review.state === stateFilter : true));

  const states = [...new Set(all.map((review) => review.state))].sort();

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
      meta={`${rows.length} shown of ${all.length}`}
      actions={<NewReviewButton />}
      reviews={all.filter((review) => !review.archived)}
    >
      <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
        {error !== null ? (
          <Failure what="The control plane could not be reached." detail={error} />
        ) : (
          <>
            <nav className="flex flex-wrap items-center gap-2" aria-label="Filters">
              <Link
                href={href({ state: '' })}
                className={cx(
                  'rounded-sm px-2 py-1 text-sm no-underline hover:no-underline',
                  stateFilter ? 'text-secondary hover:bg-hover' : 'bg-active text-primary',
                )}
              >
                All states
              </Link>
              {states.map((state) => (
                <Link
                  key={state}
                  href={href({ state })}
                  className={cx(
                    'rounded-sm px-2 py-1 text-sm no-underline hover:no-underline',
                    stateFilter === state
                      ? 'bg-active text-primary'
                      : 'text-secondary hover:bg-hover',
                  )}
                >
                  {state.replace(/_/g, ' ')}
                </Link>
              ))}

              {archivedCount > 0 ? (
                <Link
                  href={href({ archived: showArchived ? '' : '1' })}
                  className={cx(
                    'ml-auto rounded-sm px-2 py-1 text-sm no-underline hover:no-underline',
                    showArchived ? 'bg-active text-primary' : 'text-secondary hover:bg-hover',
                  )}
                  title="Dead runs from the quota work. Kept because docs/proof references them."
                >
                  {showArchived ? 'Hide' : 'Show'} archived ({archivedCount})
                </Link>
              ) : null}
            </nav>

            <Panel flush>
              {rows.length === 0 ? (
                <Empty
                  title="No review matches this filter."
                  hint={
                    archivedCount > 0 && !showArchived
                      ? `${archivedCount} archived review${archivedCount === 1 ? '' : 's'} are hidden. Show them, or clear the state filter.`
                      : 'Start one from New review, or email the watched mailbox — a questionnaire arriving there starts a review with nobody involved.'
                  }
                />
              ) : (
                <ul>
                  {rows.map((review) => (
                    <li key={review.review_id}>
                      <Link
                        href={`/reviews/${review.review_id}`}
                        className={cx(
                          'flex items-center gap-4 border-b border-subtle px-4 py-3 no-underline last:border-0 hover:bg-hover hover:no-underline',
                          review.archived ? 'opacity-60' : '',
                        )}
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-base font-medium text-primary">
                            {review.customer}
                          </span>
                          <Mono dim>{review.review_id}</Mono>
                        </span>
                        <span className="hidden shrink-0 text-sm text-secondary sm:block">
                          {review.framework.toUpperCase()} · {review.residency.toUpperCase()}
                        </span>
                        <span className="shrink-0 text-sm tabular-nums text-muted">
                          round {review.current_round}
                        </span>
                        <span className="shrink-0">
                          <LifecycleBadge
                            state={review.state}
                            tone={reviewStateTone(review.state)}
                          />
                        </span>
                        {review.archived ? (
                          <span className="shrink-0 text-xs text-muted">archived</span>
                        ) : null}
                        <span className="hidden w-list shrink-0 text-right text-sm text-muted lg:block">
                          {ago(review.created_at)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </>
        )}
      </div>
    </AppShell>
  );
}
