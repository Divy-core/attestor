import Link from 'next/link';

import { AppShell } from '@/components/layout/AppShell';
import { NewReviewButton } from '@/components/review/NewReview';
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
    <AppShell pathname="/" title="Fleet" actions={<NewReviewButton />}>
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
                <EmptyState title="Nothing on file" action={<NewReviewButton />}>
                  Hand Attestor a customer questionnaire and it works through it on its own.
                  Starting a review publishes <Mono dim>intake_document</Mono> to{' '}
                  <Mono dim>attestor.work</Mono>; nothing after that waits on a person except an
                  answer the system will not stand behind alone.
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
                <h2 className="text-sm font-medium text-primary">
                  What this console does, and what it does not
                </h2>
                {/* This paragraph used to end "and nothing is started from it". That was written
                    as a security property and it was a true one, but what it told a reader was
                    that the interface was a viewer for work a developer ran from a terminal.
                    Phase 6.5 gave the product an entrance, so the sentence had to change rather
                    than survive as a claim the New review button contradicts. */}
                <p className="max-w-prose text-sm text-secondary">
                  Uploading a questionnaire here publishes one message and returns. From that
                  point the review advances because messages are delivered — triage, then three
                  department engines drafting in parallel on Agent Runtime under their own Agent
                  Identities, then assembly — and the only thing that waits on a person is an
                  answer the system has decided it will not stand behind alone.
                </p>
                <p className="max-w-prose text-sm text-secondary">
                  Nothing on any page is computed in the browser. Every figure is read from the
                  deployed control plane, which reads Firestore, and every write executes under
                  the control plane's identity through an explicit method-and-path allowlist — the
                  web service account holds nothing but permission to write its own logs.
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
