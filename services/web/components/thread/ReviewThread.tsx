'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApprovalQueue } from '@/components/review/ApprovalQueue';
import { StreamIndicator } from '@/components/review/StreamIndicator';
import { ThreadPost } from '@/components/thread/ThreadPost';
import { Button, Empty, Failure, Mono } from '@/components/ui/primitives';
import type { AnswerRow, QuestionRow } from '@/lib/api/client';
import { createPoller } from '@/lib/poll';
import { openRunStream, type RunEventFrame, type StreamHealth } from '@/lib/sse';
import type { ThreadAction, ThreadPayload, ThreadPost as Post } from '@/lib/types/thread';

/**
 * The review, as the conversation that produced it. The primary surface of the product.
 *
 * ## It is not a chatbot
 *
 * There is a text box at the bottom and that is where the resemblance stops. The thread is
 * a **shared working record**: the fleet posts as itself while it works, a person watches,
 * asks, and approves, and the composer's replies come out of the audit trail rather than
 * out of a model. Nothing here is a bubble on the right.
 *
 * ## Live and history are the same view
 *
 * The identical component renders a review that is drafting right now and a review that
 * finished three weeks ago, because both are the same projection over the same records.
 * There is no separate history mode to fall out of step, and a thread opened later shows
 * exactly what happened, in order, with the same expandable blocks.
 *
 * ## Why the stream does not carry the posts
 *
 * Same reasoning as the workspace grid, and it matters more here. Events say *what
 * happened*; the thread is refetched when they arrive. Applying event payloads to local
 * posts would need every event to carry a whole post and would need this reducer to stay in
 * step with the projection forever — one dropped field and the thread shows an exchange
 * that never happened, on the one surface whose entire premise is that it did.
 *
 * So the stream is a cache-invalidation signal, coalesced on a trailing edge. On a
 * 949-event run that is the difference between a few dozen reads and a few thousand.
 */

type Props = {
  reviewId: string;
  roundId: string;
  runId: string | null;
  initialThread: ThreadPayload | null;
  /** For the inline approval. Kept fresh by the same refetch that keeps the thread fresh. */
  initialQuestions: QuestionRow[];
  initialAnswers: AnswerRow[];
  /** Set when the server-side read failed. The failure is rendered, never an empty thread. */
  loadError: string | null;
  /** Jump to the questions grid, focused on one row. Owned by the page's panel. */
  onOpenQuestions?: (questionId?: string) => void;
  onOpenArtifacts?: () => void;
  /**
   * Bumped by the composer after it records or dispatches something.
   *
   * The composer lives outside this component now -- it is the surface's, not the thread's
   * -- so "something happened, re-read" arrives as a changing number rather than as a
   * callback handed downward and back up again.
   */
  refreshToken?: number;
  /** Rendered under the last post: stream health, and the composer itself. */
  footer?: React.ReactNode;
};

/** No more than one read per this many milliseconds, however many events arrive. */
const MIN_REFETCH_INTERVAL_MS = 1_200;

export function ReviewThread({
  reviewId,
  roundId,
  runId,
  initialThread,
  initialQuestions,
  initialAnswers,
  loadError,
  onOpenQuestions,
  onOpenArtifacts,
  refreshToken = 0,
  footer,
}: Props) {
  const [thread, setThread] = useState<ThreadPayload | null>(initialThread);
  const [questions, setQuestions] = useState(initialQuestions);
  const [answers, setAnswers] = useState(initialAnswers);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const [health, setHealth] = useState<StreamHealth>('closed');
  const [healthDetail, setHealthDetail] = useState('Not watching.');
  const [lastSeq, setLastSeq] = useState(0);
  const [gaps, setGaps] = useState(0);
  const [polling, setPolling] = useState(false);
  const [observed, setObserved] = useState(0);
  const [reads, setReads] = useState(0);

  /** Which post has its inline approval open. One at a time; it is a full working panel. */
  const [approving, setApproving] = useState<string | null>(null);

  const refetch = useCallback(async (): Promise<boolean> => {
    setReads((count) => count + 1);
    const [threadResponse, answerResponse] = await Promise.all([
      fetch(`/api/attestor/reviews/${encodeURIComponent(reviewId)}/thread`, {
        cache: 'no-store',
      }),
      fetch(`/api/attestor/rounds/${encodeURIComponent(roundId)}/answers`, {
        cache: 'no-store',
      }),
    ]);
    if (!threadResponse.ok || !answerResponse.ok) {
      const status = !threadResponse.ok ? threadResponse.status : answerResponse.status;
      setRefreshError(
        status === 429
          ? 'The control plane rate-limited a refresh. The next one will be spaced further apart.'
          : `The control plane returned ${status} on refresh.`,
      );
      // Thrown rather than returned false: the poller reads a rejection as "nothing
      // changed" for scheduling, and the error is already on screen. What must not happen
      // is the thread emptying because one read failed.
      throw new Error(`refresh failed: ${status}`);
    }
    setRefreshError(null);
    const nextThread = (await threadResponse.json()) as ThreadPayload;
    const nextAnswers = (await answerResponse.json()) as AnswerRow[];

    const fingerprint = `${nextThread.posts.length}:${nextThread.events_read}:${nextThread.posts
      .map((post) => `${post.post_id}${post.summary}`)
      .join('')}`;
    const changed = fingerprint !== previous.current;
    previous.current = fingerprint;
    setAnswers(nextAnswers);
    if (changed) setThread(nextThread);
    return changed;
  }, [reviewId, roundId]);

  const previous = useRef('');
  const poller = useMemo(() => createPoller(refetch), [refetch]);

  // Coalescing. Refs throughout: this runs inside the stream's handler, which is created
  // once, and anything read through state there would be the value from first render.
  const lastRun = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef(false);

  const scheduleRefetch = useCallback(() => {
    const run = () => {
      timer.current = null;
      lastRun.current = Date.now();
      inFlight.current = true;
      void refetch()
        .catch(() => {})
        .finally(() => {
          inFlight.current = false;
        });
    };
    if (timer.current !== null) return;
    const since = Date.now() - lastRun.current;
    if (inFlight.current || since < MIN_REFETCH_INTERVAL_MS) {
      timer.current = setTimeout(run, Math.max(0, MIN_REFETCH_INTERVAL_MS - since));
      return;
    }
    run();
  }, [refetch]);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  // The composer said something landed. Not coalesced: a person pressed a key and is
  // waiting to see the result, which is the one case where being current beats being
  // sparing. Skipped on the first render, where `initialThread` is already fresh.
  const firstRefresh = useRef(true);
  useEffect(() => {
    if (firstRefresh.current) {
      firstRefresh.current = false;
      return;
    }
    void refetch().catch(() => {});
  }, [refreshToken, refetch]);

  useEffect(() => {
    if (runId === null) return undefined;
    const stream = openRunStream(runId, {
      onEvent: (event: RunEventFrame) => {
        if (typeof event.seq === 'number' && event.seq > 0) setLastSeq(event.seq);
        setObserved((count) => count + 1);
        scheduleRefetch();
      },
      onHealth: (next, detail) => {
        setHealth(next);
        setHealthDetail(detail);
        // Armed by staleness, not by an error. A stream that goes quiet without erroring is
        // the failure that actually happens, and a fallback wired to `onerror` sleeps
        // through it.
        if (next === 'stale') {
          setPolling(true);
          poller.start();
        } else if (next === 'live') {
          setPolling(false);
          poller.stop();
        }
      },
      onGap: (from, to) => {
        setGaps((count) => count + (to - from - 1));
        void refetch().catch(() => {});
      },
    });
    return () => {
      stream.close();
      poller.stop();
    };
  }, [runId, refetch, poller, scheduleRefetch]);

  const pending = useMemo(() => {
    const byQuestion = new Map(answers.map((answer) => [answer.question_id, answer]));
    return questions
      .map((question) => ({ question, answer: byQuestion.get(question.question_id) }))
      .filter(
        (row): row is { question: QuestionRow; answer: AnswerRow } =>
          row.answer !== undefined && row.answer.status === 'needs_human',
      );
  }, [questions, answers]);

  // Questions are re-read only when the thread says the round grew; they do not change
  // during drafting and a second request per refresh for a static 312-row payload is waste.
  useEffect(() => {
    setQuestions(initialQuestions);
  }, [initialQuestions]);

  const onAction = useCallback(
    (post: Post, action: ThreadAction) => {
      if (action.kind === 'approve') {
        setApproving((current) => (current === post.post_id ? null : post.post_id));
        return;
      }
      if (action.kind === 'questions') onOpenQuestions?.();
      if (action.kind === 'artifacts') onOpenArtifacts?.();
      if (action.kind === 'export') onOpenArtifacts?.();
    },
    [onOpenQuestions, onOpenArtifacts],
  );

  if (loadError !== null) {
    return (
      <div className="p-4">
        <Failure what="This review's record could not be read." detail={loadError} />
      </div>
    );
  }

  const posts = thread?.posts ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-column px-6 py-8">
          {refreshError !== null ? (
            <div className="pb-4">
              <Failure what="The last refresh failed." detail={refreshError} />
            </div>
          ) : null}

          {thread?.truncated ? (
            <p className="max-w-prose pb-4 text-xs text-muted">
              This review has more audit events than one read takes in, so the narrative
              below covers <Mono dim>{thread.events_read}</Mono> of them. Every count a post
              quotes is taken from the answers themselves and is exact.
            </p>
          ) : null}

          {posts.length === 0 ? (
            <Empty
              title="Nothing has been recorded against this review yet."
              hint="Posts appear here as each stage reports."
            />
          ) : (
            <div className="flex flex-col">
              {posts.map((post) => (
                <div key={post.post_id}>
                  <ThreadPost
                    post={post}
                    onAction={(action) => onAction(post, action)}
                    onQuestion={(questionId) => onOpenQuestions?.(questionId)}
                    defaultOpen={post.kind === 'answered'}
                  />
                  {approving === post.post_id ? (
                    <div className="mb-6 ml-8 rounded border border-line bg-surface">
                      <div className="flex items-center justify-between gap-4 border-b border-subtle px-4 py-3">
                        <h4 className="text-sm font-medium text-primary">
                          {pending.length} held for you
                        </h4>
                        <Button tone="ghost" small onClick={() => setApproving(null)}>
                          Close
                        </Button>
                      </div>
                      <ApprovalQueue pending={pending} onResolved={() => poller.now()} />
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {footer !== undefined ? (
        <div className="shrink-0 border-t border-subtle">
          <div className="mx-auto flex w-full max-w-column flex-col gap-2 px-6 py-4">
            {footer}
            {runId !== null ? (
              <div className="flex items-center justify-end">
                <StreamIndicator
                  health={health}
                  detail={healthDetail}
                  polling={polling}
                  lastSeq={lastSeq}
                  gaps={gaps}
                  observed={observed}
                  reads={reads}
                />
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
