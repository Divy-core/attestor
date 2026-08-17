import Link from 'next/link';

import { AppShell } from '@/components/layout/AppShell';
import { NewReviewButton } from '@/components/review/NewReview';
import { Card, EmptyState, ErrorState, Mono } from '@/components/ui/primitives';
import { ApiError, api, type ReviewRow } from '@/lib/api/client';
import { absolute, ago } from '@/lib/format';
import { reviewStateTone } from '@/lib/states';

export const dynamic = 'force-dynamic';

export default async function ReviewsPage() {
  let reviews: ReviewRow[] = [];
  let error: string | null = null;
  try {
    reviews = await api.listReviews(50);
  } catch (cause) {
    error = cause instanceof ApiError ? cause.human : String(cause);
  }

  return (
    <AppShell
      pathname="/reviews"
      title="Reviews"
      meta={error === null ? `${reviews.length} on file` : undefined}
      actions={<NewReviewButton />}
    >
      <div className="p-5">
        <Card className="overflow-hidden">
          {error !== null ? (
            <ErrorState title="Reviews could not be listed." detail={error} />
          ) : reviews.length === 0 ? (
            <EmptyState
              title="No reviews yet"
              action={<NewReviewButton />}
            >
              Upload a customer questionnaire and Attestor triages it, drafts against the corpus
              on the deployed department engines, and holds back whatever it will not stand behind
              alone. Starting one publishes <Mono dim>intake_document</Mono> to{' '}
              <Mono dim>attestor.work</Mono>; everything after that is message-driven.
            </EmptyState>
          ) : (
            <ul className="flex flex-col">
              <li className="flex items-center gap-4 border-b border-subtle px-4 py-2 text-xs uppercase tracking-wide text-muted">
                <span className="min-w-0 flex-1">Customer</span>
                <span className="w-28 shrink-0">State</span>
                <span className="w-16 shrink-0 text-right">Round</span>
                <span className="w-24 shrink-0">Framework</span>
                <span className="w-28 shrink-0 text-right">Intake</span>
              </li>
              {reviews.map((review) => {
                const tone = reviewStateTone(review.state);
                return (
                  <li key={review.review_id} className="border-b border-subtle last:border-b-0">
                    <Link
                      href={`/reviews/${review.review_id}`}
                      className="flex items-center gap-4 px-4 py-2 no-underline transition-colors hover:bg-hover"
                    >
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate text-sm text-primary">{review.customer}</span>
                        <Mono dim>{review.review_id}</Mono>
                      </span>
                      <span className="w-28 shrink-0 text-sm">
                        <span
                          className={
                            tone === 'blocked' ? 'font-medium text-primary' : 'text-secondary'
                          }
                        >
                          {review.state}
                        </span>
                      </span>
                      <span className="w-16 shrink-0 text-right font-mono text-sm tabular-nums text-secondary">
                        {review.current_round}
                      </span>
                      <span className="w-24 shrink-0 text-sm text-muted">{review.framework}</span>
                      <span
                        className="w-28 shrink-0 text-right text-sm text-muted"
                        title={absolute(review.created_at)}
                      >
                        {ago(review.created_at)}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
