import Link from 'next/link';

import { AppShell } from '@/components/layout/AppShell';
import { ReviewSurface } from '@/components/review/ReviewSurface';
import { RoundTimeline } from '@/components/review/RoundTimeline';
import { Button, Failure, Mono } from '@/components/ui/primitives';
import {
  ApiError,
  api,
  type AnswerRow,
  type QuestionRow,
  type ReviewDetailRow,
} from '@/lib/api/client';
import { absolute, ago } from '@/lib/format';
import { isTab, type Tab } from '@/lib/tabs';
import type { ThreadPayload } from '@/lib/types/thread';

export const dynamic = 'force-dynamic';

/**
 * A review, opening on its thread.
 *
 * Server component: the first paint carries real data from the deployed control plane, and
 * the client components keep it live from there. Server-rendering the first read rather
 * than fetching on mount matters for the recording — a client-fetched page shows its
 * skeleton for the length of a round trip on every navigation, which on a 1080p take is a
 * second of nothing at the moment the presenter starts talking.
 *
 * ## Four reads, and each one degrades on its own
 *
 * The review, its questions, its answers, its thread. A failed thread read renders as a
 * failed thread read, never as a review with nothing in it; a failed grid read is the same.
 * That distinction has been made five times in the Python half of this build and is worth
 * every repetition: an empty surface during a recorded demo, caused by a 503 nobody
 * surfaced, is the worst version of it.
 *
 * The audit trail is **not** among them. It is a thousand documents that only the Audit tab
 * renders, and that tab fetches it when it is opened.
 */
export default async function ReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ reviewId: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { reviewId } = await params;
  const { tab } = await searchParams;
  const initialTab: Tab = isTab(tab) ? tab : 'thread';

  let review: ReviewDetailRow | null = null;
  let questions: QuestionRow[] = [];
  let answers: AnswerRow[] = [];
  let thread: ThreadPayload | null = null;
  let loadError: string | null = null;
  let threadError: string | null = null;

  try {
    review = await api.getReview(reviewId);
    // The latest round is the one being worked. `rounds` comes back ordered by the
    // repository, but ordering by ordinal here rather than trusting position keeps this
    // correct if that ever changes.
    const latest = [...review.rounds].sort((a, b) => b.ordinal - a.ordinal)[0] ?? null;
    if (latest !== null) {
      const [questionResult, answerResult, threadResult] = await Promise.allSettled([
        api.listQuestions(latest.round_id),
        api.listAnswers(latest.round_id),
        api.getThread(reviewId),
      ]);

      if (questionResult.status === 'fulfilled') questions = questionResult.value;
      if (answerResult.status === 'fulfilled') answers = answerResult.value;
      if (questionResult.status === 'rejected') loadError = describe(questionResult.reason);
      else if (answerResult.status === 'rejected') loadError = describe(answerResult.reason);

      if (threadResult.status === 'fulfilled') thread = threadResult.value;
      else threadError = describe(threadResult.reason);
    }
  } catch (cause) {
    loadError = describe(cause);
  }

  const latestRound =
    review === null
      ? null
      : ([...review.rounds].sort((a, b) => b.ordinal - a.ordinal)[0] ?? null);

  if (review === null || latestRound === null) {
    return (
      <AppShell pathname={`/reviews/${reviewId}`} title="Review">
        <div className="p-6">
          {loadError !== null ? (
            <Failure
              what="This review could not be read."
              detail={loadError}
              action={
                <Link href="/reviews">
                  <Button>Back to reviews</Button>
                </Link>
              }
            />
          ) : (
            <div className="flex flex-col items-start gap-2 px-4 py-10">
              <div className="w-full border-t border-dashed border-line" />
              <h3 className="pt-3 text-sm font-medium text-primary">This review has no rounds</h3>
              <p className="max-w-prose text-sm text-secondary">
                A round appears when a questionnaire is parsed and{' '}
                <Mono dim>intake_document</Mono> is published. Nothing here is created by this
                interface — the review advances by Pub/Sub message.
              </p>
            </div>
          )}
        </div>
      </AppShell>
    );
  }

  const pendingCount = answers.filter((answer) => answer.status === 'needs_human').length;

  // The run id and "did this arrive by email" both come off the thread, which already read
  // the trail to build itself. They used to cost a second full audit read of up to a
  // thousand documents, taken purely to pick two facts out of it.
  const runId = thread?.run_id ?? null;
  const arrivedByEmail = thread?.arrived_by_email ?? false;

  return (
    <AppShell
      // The one page that scrolls itself: every tab manages its own panes.
      scroll={false}
      pathname={`/reviews/${reviewId}`}
      title={review.customer}
      meta={
        <>
          {review.state.replace(/_/g, ' ')} · round {review.current_round} ·{' '}
          <span title={absolute(review.created_at)}>{ago(review.created_at)}</span>
        </>
      }
      reviews={[review]}
    >
      <div className="flex h-full flex-col">
        <RoundTimeline rounds={review.rounds} createdAt={review.created_at} />
        <div className="min-h-0 flex-1">
          <ReviewSurface
            reviewId={reviewId}
            roundId={latestRound.round_id}
            runId={runId}
            initialTab={initialTab}
            initialThread={thread}
            initialQuestions={questions}
            initialAnswers={answers}
            threadError={threadError}
            loadError={loadError}
            arrivedByEmail={arrivedByEmail}
            pendingCount={pendingCount}
          />
        </div>
      </div>
    </AppShell>
  );
}

function describe(cause: unknown): string {
  return cause instanceof ApiError ? cause.human : String(cause);
}
