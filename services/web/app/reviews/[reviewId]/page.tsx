import Link from 'next/link';

import { AppShell } from '@/components/layout/AppShell';
import { ReviewWorkspace } from '@/components/review/ReviewWorkspace';
import { RoundTimeline } from '@/components/review/RoundTimeline';
import { Button, ErrorState, Mono } from '@/components/ui/primitives';
import { ApiError, api, type AnswerRow, type QuestionRow, type ReviewDetailRow } from '@/lib/api/client';
import { absolute, ago } from '@/lib/format';

export const dynamic = 'force-dynamic';

/**
 * The workspace. Server component: the first paint carries real data from the deployed control
 * plane, and `ReviewWorkspace` keeps it live from there.
 *
 * Server-rendering the first read rather than fetching on mount matters for the recording. A
 * client-fetched page shows its skeleton for the length of a round trip on every navigation,
 * which on a 1080p take is a second of nothing at the moment the presenter starts talking.
 */
export default async function ReviewPage({
  params,
}: {
  params: Promise<{ reviewId: string }>;
}) {
  const { reviewId } = await params;

  let review: ReviewDetailRow | null = null;
  let questions: QuestionRow[] = [];
  let answers: AnswerRow[] = [];
  let loadError: string | null = null;
  let runId: string | null = null;

  try {
    review = await api.getReview(reviewId);
    // The latest round is the one being worked. `rounds` comes back ordered by the repository,
    // but ordering by ordinal here rather than trusting position keeps this correct if that
    // changes.
    const latest = [...review.rounds].sort((a, b) => b.ordinal - a.ordinal)[0] ?? null;
    if (latest !== null) {
      [questions, answers] = await Promise.all([
        api.listQuestions(latest.round_id),
        api.listAnswers(latest.round_id),
      ]);
      // The run id is not on the round: it is on the audit events the run wrote. Reading it
      // from the most recent event is how the page knows which stream to open, and if there is
      // no event yet there is no run to watch — which the workspace says rather than opening a
      // stream to nothing.
      const audit = await api.listAudit(reviewId, 50);
      runId = audit.find((event) => event.run_id)?.run_id ?? null;
    }
  } catch (cause) {
    loadError = cause instanceof ApiError ? cause.human : String(cause);
  }

  const latestRound =
    review === null
      ? null
      : ([...review.rounds].sort((a, b) => b.ordinal - a.ordinal)[0] ?? null);

  if (review === null || latestRound === null) {
    return (
      <AppShell pathname={`/reviews/${reviewId}`} title="Review">
        <div className="p-5">
          {loadError !== null ? (
            <ErrorState
              title="This review could not be read."
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
                A round appears when a questionnaire is uploaded and{' '}
                <Mono dim>intake_document</Mono> is published. Nothing here is created by this
                interface — the review advances by Pub/Sub message.
              </p>
            </div>
          )}
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      pathname={`/reviews/${reviewId}`}
      title={review.customer}
      meta={
        <>
          {review.state} · round {review.current_round} ·{' '}
          <span title={absolute(review.created_at)}>{ago(review.created_at)}</span>
        </>
      }
      actions={
        runId !== null ? (
          <Link href={`/traces/${runId}`}>
            <Button variant="quiet">Audit trail</Button>
          </Link>
        ) : null
      }
    >
      <div className="flex flex-col">
        <RoundTimeline rounds={review.rounds} createdAt={review.created_at} />
        <div className="min-h-0 flex-1">
          <ReviewWorkspace
            reviewId={reviewId}
            roundId={latestRound.round_id}
            runId={runId}
            initialQuestions={questions}
            initialAnswers={answers}
            loadError={loadError}
          />
        </div>
      </div>
    </AppShell>
  );
}
