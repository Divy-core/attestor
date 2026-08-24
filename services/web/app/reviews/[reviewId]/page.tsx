import Link from 'next/link';

import { ChatShell } from '@/components/chat/ChatShell';
import { ChatView } from '@/components/chat/ChatView';
import type { PanelKind } from '@/components/chat/SidePanel';
import { Button, Failure, Mono } from '@/components/ui/primitives';
import {
  ApiError,
  api,
  type AnswerRow,
  type QuestionRow,
  type ReviewCard,
  type ReviewDetailRow,
} from '@/lib/api/client';
import type { ThreadPayload } from '@/lib/types/thread';

export const dynamic = 'force-dynamic';

const PANELS = new Set(['report', 'questions', 'evidence', 'artifacts', 'audit']);

/**
 * One conversation.
 *
 * Server component: the first paint carries real data from the deployed control plane, and
 * the client keeps it live from there. A client-fetched page shows its skeleton for the
 * length of a round trip on every navigation, which on a 1080p take is a second of nothing
 * at the moment the presenter starts talking.
 *
 * ## Five reads, and each one degrades on its own
 *
 * The rail, the review, its questions, its answers, its thread. A failed thread read renders
 * as a failed thread read, never as a conversation with nothing in it. That distinction has
 * been made six times in the Python half of this build and is worth every repetition.
 *
 * The audit trail is not among them: it is over a thousand documents that only the Audit
 * panel renders, and that panel fetches it when it is opened.
 */
export default async function ReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ reviewId: string }>;
  searchParams: Promise<{ panel?: string }>;
}) {
  const { reviewId } = await params;
  const { panel } = await searchParams;
  const initialPanel: PanelKind | null =
    panel !== undefined && PANELS.has(panel) ? (panel as PanelKind) : null;

  let review: ReviewDetailRow | null = null;
  let questions: QuestionRow[] = [];
  let answers: AnswerRow[] = [];
  let thread: ThreadPayload | null = null;
  let reviews: ReviewCard[] = [];
  let loadError: string | null = null;
  let threadError: string | null = null;

  const railResult = await Promise.allSettled([api.reviewBoard(100, false)]);
  if (railResult[0].status === 'fulfilled') reviews = railResult[0].value;

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
      <ChatShell reviews={reviews} activeId={reviewId}>
        <div className="mx-auto w-full max-w-column px-6 py-10">
          {loadError !== null ? (
            <Failure
              what="This review could not be read."
              detail={loadError}
              action={
                <Link href="/">
                  <Button>Back</Button>
                </Link>
              }
            />
          ) : (
            <div className="flex flex-col items-start gap-2">
              <h3 className="text-sm font-medium text-primary">This review has no rounds</h3>
              <p className="text-sm text-secondary">
                A round appears when a questionnaire is parsed and{' '}
                <Mono dim>intake_document</Mono> is published.
              </p>
            </div>
          )}
        </div>
      </ChatShell>
    );
  }

  return (
    <ChatShell reviews={reviews} activeId={reviewId}>
      <ChatView
        reviewId={reviewId}
        roundId={latestRound.round_id}
        runId={thread?.run_id ?? null}
        review={review}
        initialThread={thread}
        initialQuestions={questions}
        initialAnswers={answers}
        initialPanel={initialPanel}
        threadError={threadError}
        loadError={loadError}
        arrivedByEmail={thread?.arrived_by_email ?? false}
      />
    </ChatShell>
  );
}

function describe(cause: unknown): string {
  return cause instanceof ApiError ? cause.human : String(cause);
}
