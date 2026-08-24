'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApprovalQueue } from '@/components/review/ApprovalQueue';
import { StreamIndicator } from '@/components/review/StreamIndicator';
import { ThreadPost } from '@/components/thread/ThreadPost';
import { Button, Empty, Failure, Label, Mono, cx } from '@/components/ui/primitives';
import type { AnswerRow, QuestionRow } from '@/lib/api/client';
import { useOperator } from '@/lib/operator';
import { createPoller } from '@/lib/poll';
import { openRunStream, type RunEventFrame, type StreamHealth } from '@/lib/sse';
import type { AskReply, ThreadAction, ThreadPayload, ThreadPost as Post } from '@/lib/types/thread';

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
  /** Jump to the questions grid, focused on one row. Owned by the page's tabs. */
  onOpenQuestions?: (questionId?: string) => void;
  onOpenArtifacts?: () => void;
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
        <div className="mx-auto w-full max-w-page px-6 py-6">
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

      <Composer
        reviewId={reviewId}
        onAsked={() => poller.now()}
        onOpenQuestions={onOpenQuestions}
        status={
          runId === null ? null : (
            <StreamIndicator
              health={health}
              detail={healthDetail}
              polling={polling}
              lastSeq={lastSeq}
              gaps={gaps}
              observed={observed}
              reads={reads}
            />
          )
        }
      />
    </div>
  );
}

/**
 * Where a person asks the thread something.
 *
 * The reply is composed by the control plane out of this review's own audit trail and no
 * model is called — see `attestor_platform/thread/answering.py`, which explains at length
 * why that is the whole point rather than a shortcut. The reply is rendered here
 * immediately and *also* appended to the trail, so the next reader of this thread sees the
 * exchange in place, three weeks later, with the same blocks behind it.
 */
function Composer({
  reviewId,
  onAsked,
  onOpenQuestions,
  status,
}: {
  reviewId: string;
  onAsked: () => void;
  onOpenQuestions?: (questionId?: string) => void;
  status: React.ReactNode;
}) {
  const [operator, setOperator] = useOperator();
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<AskReply | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async () => {
    const asked = question.trim();
    if (!asked || !operator.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/attestor/reviews/${encodeURIComponent(reviewId)}/ask`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: asked, asked_by: operator.trim() }),
        },
      );
      const payload: unknown = await response.json();
      if (!response.ok) {
        const detail =
          payload && typeof payload === 'object' && 'detail' in payload
            ? String((payload as { detail: unknown }).detail)
            : `The control plane returned ${response.status}.`;
        setError(detail);
        return;
      }
      setReply(payload as AskReply);
      setQuestion('');
      // The exchange is already in the trail; this pulls it into the thread above so the
      // reply the person is reading and the reply the record holds are the same object.
      onAsked();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }, [question, operator, reviewId, onAsked]);

  return (
    <div className="shrink-0 border-t border-subtle">
      {reply !== null ? (
        <div className="mx-auto w-full max-w-page px-6 pt-4">
          <div className="flex flex-col gap-2 border-l-2 border-cited pl-3">
            <p className="text-sm text-primary">{reply.answer}</p>
            {reply.lines.map((line) => (
              <p key={line} className="max-w-prose text-xs text-muted">
                {line}
              </p>
            ))}
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted">
                Composed from {reply.details.length} block
                {reply.details.length === 1 ? '' : 's'} of this review&rsquo;s record. It is
                in the thread above, and in the audit trail.
              </span>
              {reply.question_id && onOpenQuestions ? (
                <button
                  type="button"
                  onClick={() => onOpenQuestions(reply.question_id as string)}
                  className="text-xs text-accent-text hover:underline"
                >
                  Open the question
                </button>
              ) : null}
              <Button tone="ghost" small onClick={() => setReply(null)}>
                Dismiss
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {error !== null ? (
        <div className="mx-auto w-full max-w-page px-6 pt-4">
          <Failure what="That question was not recorded." detail={error} />
        </div>
      ) : null}

      <div className="mx-auto flex w-full max-w-page items-end gap-3 px-6 py-4">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder="Ask this review something — a question number, what is held, what was refused"
            aria-label="Ask the thread"
            className={cx(
              'h-row w-full rounded-sm bg-sunken px-3 text-sm text-primary outline-none',
              'placeholder:text-muted',
            )}
          />
          {operator.trim() ? null : (
            <label className="flex items-center gap-2">
              <Label>your name, recorded against what you ask</Label>
              <input
                value={operator}
                onChange={(event) => setOperator(event.target.value)}
                placeholder="who is asking"
                className="h-row-dense w-full max-w-list rounded-sm bg-sunken px-2 text-xs text-primary outline-none placeholder:text-muted"
              />
            </label>
          )}
        </div>
        <Button
          tone="primary"
          onClick={() => void submit()}
          disabled={busy || !question.trim() || !operator.trim()}
          title={operator.trim() ? undefined : 'Enter your name first'}
        >
          {busy ? 'Asking' : 'Ask'}
        </Button>
        {status ? <div className="shrink-0 pb-1">{status}</div> : null}
      </div>
    </div>
  );
}
