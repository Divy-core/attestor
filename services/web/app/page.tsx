import Link from 'next/link';

import { AppShell } from '@/components/layout/AppShell';
import { Card, CardHeader, EmptyState, ErrorState, Mono } from '@/components/ui/primitives';
import { ApiError, api, type ReviewRow } from '@/lib/api/client';
import { ago } from '@/lib/format';
import { BLOCKED_REVIEW_STATES, TERMINAL_REVIEW_STATES } from '@/lib/states';

export const dynamic = 'force-dynamic';

/**
 * The fleet overview.
 *
 * Deliberately not a dashboard of gauges. The three numbers worth a glance are how many reviews
 * are in flight, how many are blocked on a person, and how many are done — because those are the
 * three things that change what someone does next. Everything else is a link.
 *
 * No hero, no sparklines, no "welcome back". A console opens onto work.
 */
export default async function OverviewPage() {
  let reviews: ReviewRow[] = [];
  let error: string | null = null;
  try {
    reviews = await api.listReviews(50);
  } catch (cause) {
    error = cause instanceof ApiError ? cause.human : String(cause);
  }

  const blocked = reviews.filter((r) => BLOCKED_REVIEW_STATES.has(r.state));
  const done = reviews.filter((r) => TERMINAL_REVIEW_STATES.has(r.state));
  const active = reviews.filter(
    (r) => !BLOCKED_REVIEW_STATES.has(r.state) && !TERMINAL_REVIEW_STATES.has(r.state),
  );

  return (
    <AppShell pathname="/" title="Fleet">
      <div className="flex flex-col gap-3 p-5">
        {error !== null ? (
          <ErrorState title="The control plane could not be reached." detail={error} />
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3">
              <Figure label="In flight" value={active.length} hint="Being worked by the fleet" />
              <Figure
                label="Blocked on a human"
                value={blocked.length}
                hint="The system will not stand behind these alone"
                emphasise={blocked.length > 0}
              />
              <Figure label="Delivered" value={done.length} hint="Closed, with commitments recorded" />
            </div>

            <Card className="overflow-hidden">
              <CardHeader
                title="Reviews"
                meta={`${reviews.length} on file`}
                actions={
                  <Link href="/reviews" className="text-sm text-secondary no-underline hover:text-primary">
                    All reviews
                  </Link>
                }
              />
              {reviews.length === 0 ? (
                <EmptyState title="Nothing on file">
                  A review appears once <Mono dim>intake_document</Mono> is published to{' '}
                  <Mono dim>attestor.work</Mono>. This interface reads; it does not start work.
                </EmptyState>
              ) : (
                <ul className="flex flex-col">
                  {reviews.slice(0, 12).map((review) => (
                    <li key={review.review_id} className="border-b border-subtle last:border-b-0">
                      <Link
                        href={`/reviews/${review.review_id}`}
                        className="flex items-center gap-4 px-4 py-2 no-underline transition-colors hover:bg-hover"
                      >
                        <span className="min-w-0 flex-1 truncate text-sm text-primary">
                          {review.customer}
                        </span>
                        <span
                          className={
                            BLOCKED_REVIEW_STATES.has(review.state)
                              ? 'w-32 shrink-0 text-sm font-medium text-primary'
                              : 'w-32 shrink-0 text-sm text-secondary'
                          }
                        >
                          {review.state}
                        </span>
                        <span className="w-28 shrink-0 text-right text-sm text-muted">
                          {ago(review.created_at)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <div className="flex flex-col gap-1.5 px-4 py-3">
                <h2 className="text-sm font-medium text-primary">What this console reads</h2>
                <p className="max-w-prose text-sm text-secondary">
                  Every figure here is read from the deployed control plane, which reads Firestore.
                  Nothing on any page is computed in the browser, and nothing is started from it —
                  reviews advance by Pub/Sub message into Cloud Run, with drafting on the deployed
                  department engines under their own Agent Identities.
                </p>
              </div>
            </Card>
          </>
        )}
      </div>
    </AppShell>
  );
}

function Figure({
  label,
  value,
  hint,
  emphasise = false,
}: {
  label: string;
  value: number;
  hint: string;
  emphasise?: boolean;
}) {
  return (
    <Card>
      <div className="flex flex-col gap-1 px-4 py-3">
        <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
        {/* Tabular, so a figure updating live does not shift the layout under it. */}
        <span
          className={
            emphasise
              ? 'font-mono text-xl font-medium tabular-nums text-primary'
              : 'font-mono text-xl tabular-nums text-primary'
          }
        >
          {value}
        </span>
        <span className="text-xs text-muted">{hint}</span>
      </div>
    </Card>
  );
}
