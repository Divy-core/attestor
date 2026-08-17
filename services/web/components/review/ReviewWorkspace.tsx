'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { AnswerCard } from '@/components/review/AnswerCard';
import { ApprovalQueue } from '@/components/review/ApprovalQueue';
import { QuestionGrid } from '@/components/review/QuestionGrid';
import { StreamIndicator } from '@/components/review/StreamIndicator';
import { Button, Card, ErrorState, StateLegend, Tabs } from '@/components/ui/primitives';
import { createPoller } from '@/lib/poll';
import { openRunStream, type StreamHealth } from '@/lib/sse';
import { STATE_ORDER } from '@/lib/states';
import type { AnswerRow, QuestionRow } from '@/lib/api/client';

/**
 * The live workspace. Server-rendered data arrives as props; this keeps it current.
 *
 * ## Why the stream does not carry the data
 *
 * Events say *what happened* — a question was triaged, an answer was drafted, a citation was
 * added — and the page refetches the rows the event names. The alternative, applying event
 * payloads directly to local state, needs every event to carry a complete row and needs the
 * reducer to stay in step with the protocol forever. One dropped field and the grid shows an
 * answer that never existed in Firestore.
 *
 * So the stream is a *cache invalidation signal*, not a data channel. That also makes the
 * polling fallback trivially correct: it does exactly what an event does, just without being
 * told to.
 *
 * ## The three failure modes, wired
 *
 * - The stream errors → `lib/sse.ts` reconnects with the resume point.
 * - The stream goes quiet without erroring → the heartbeat watchdog reports `stale`, and the
 *   poller starts. This is the one that actually happens, and a fallback armed on `onerror`
 *   would sit idle through it.
 * - The stream reconnects and skips → a `seq` gap fires `onGap`, which forces a full refetch
 *   rather than leaving a hole.
 */

type Props = {
  reviewId: string;
  roundId: string;
  runId: string | null;
  initialQuestions: QuestionRow[];
  initialAnswers: AnswerRow[];
  /** Set when the server-side read failed. The page renders the error rather than an empty grid. */
  loadError: string | null;
};

type Tab = 'questions' | 'approvals';

export function ReviewWorkspace({
  reviewId,
  roundId,
  runId,
  initialQuestions,
  initialAnswers,
  loadError,
}: Props) {
  const [questions, setQuestions] = useState(initialQuestions);
  const [answers, setAnswers] = useState(initialAnswers);
  const [selected, setSelected] = useState<string | null>(
    initialQuestions[0]?.question_id ?? null,
  );
  const [tab, setTab] = useState<Tab>('questions');

  const [health, setHealth] = useState<StreamHealth>('closed');
  const [healthDetail, setHealthDetail] = useState('Not watching.');
  const [lastSeq, setLastSeq] = useState(0);
  const [gaps, setGaps] = useState(0);
  const [polling, setPolling] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  // A ref rather than state: the fetch compares against the previous payload to decide whether
  // anything changed, and reading that through state would capture a stale closure inside the
  // poller, which never re-creates itself.
  const fingerprint = useRef('');

  const refetch = useCallback(async (): Promise<boolean> => {
    const [questionResponse, answerResponse] = await Promise.all([
      fetch(`/api/attestor/rounds/${encodeURIComponent(roundId)}/questions`, {
        cache: 'no-store',
      }),
      fetch(`/api/attestor/rounds/${encodeURIComponent(roundId)}/answers`, { cache: 'no-store' }),
    ]);
    if (!questionResponse.ok || !answerResponse.ok) {
      const status = !questionResponse.ok ? questionResponse.status : answerResponse.status;
      setRefreshError(`The control plane returned ${status} on refresh.`);
      // Thrown, not returned-as-false: the poller treats a rejection as "nothing changed" for
      // scheduling, and the error is already on screen. What must not happen is the grid being
      // emptied because a read failed.
      throw new Error(`refresh failed: ${status}`);
    }
    setRefreshError(null);
    const nextQuestions = (await questionResponse.json()) as QuestionRow[];
    const nextAnswers = (await answerResponse.json()) as AnswerRow[];

    // Cheap change detection. Counts alone would miss an answer being rewritten in place by a
    // constrained redraft, which is exactly the event worth noticing.
    const next = `${nextQuestions.length}:${nextAnswers.length}:${nextAnswers
      .map((a) => `${a.question_id}${a.status}${a.citations.length}`)
      .join('')}`;
    const changed = next !== fingerprint.current;
    fingerprint.current = next;
    if (changed) {
      setQuestions(nextQuestions);
      setAnswers(nextAnswers);
    }
    return changed;
  }, [roundId]);

  const poller = useMemo(() => createPoller(refetch), [refetch]);

  useEffect(() => {
    if (runId === null) return undefined;

    const stream = openRunStream(runId, {
      onEvent: (event) => {
        if (typeof event.seq === 'number' && event.seq > 0) setLastSeq(event.seq);
        // Every event means the round moved. Refetch rather than reduce; see the module note.
        void refetch().catch(() => {});
      },
      onHealth: (next, detail) => {
        setHealth(next);
        setHealthDetail(detail);
        // Armed by staleness, not by an error. The whole reason this module exists.
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
        // A gap means events were missed; only a full read can say what they were.
        void refetch().catch(() => {});
      },
    });

    return () => {
      stream.close();
      poller.stop();
    };
  }, [runId, refetch, poller]);

  const answerIndex = useMemo(
    () => new Map(answers.map((answer) => [answer.question_id, answer])),
    [answers],
  );

  const pending = useMemo(
    () =>
      questions
        .map((question) => ({ question, answer: answerIndex.get(question.question_id) }))
        .filter(
          (row): row is { question: QuestionRow; answer: AnswerRow } =>
            row.answer !== undefined && row.answer.status === 'needs_human',
        ),
    [questions, answerIndex],
  );

  const selectedQuestion = questions.find((q) => q.question_id === selected) ?? null;

  if (loadError !== null) {
    return (
      <div className="p-5">
        <ErrorState
          title="This round could not be read."
          detail={loadError}
          action={
            <Button onClick={() => window.location.reload()}>Try again</Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <StateLegend keys={STATE_ORDER} />
        <div className="flex items-center gap-3">
          {runId === null ? (
            <span className="text-xs text-muted">
              No active run. This round is not currently being worked.
            </span>
          ) : (
            <StreamIndicator
              health={health}
              detail={healthDetail}
              polling={polling}
              lastSeq={lastSeq}
              gaps={gaps}
            />
          )}
          <Button variant="quiet" onClick={() => poller.now()}>
            Refresh
          </Button>
        </div>
      </div>

      {refreshError !== null ? (
        <ErrorState
          title="The last refresh failed."
          detail={refreshError}
          action={
            <span className="text-xs text-secondary">
              What is on screen is the last good read, not an empty result.
            </span>
          }
        />
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,5fr)_minmax(0,7fr)] gap-3">
        <Card className="flex min-h-0 flex-col overflow-hidden">
          <Tabs
            tabs={[
              { id: 'questions', label: 'Questions', count: questions.length },
              { id: 'approvals', label: 'Needs a human', count: pending.length },
            ]}
            active={tab}
            onChange={setTab}
          />
          {tab === 'questions' ? (
            <QuestionGrid
              questions={questions}
              answers={answerIndex}
              selectedId={selected}
              onSelect={setSelected}
            />
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <ApprovalQueue pending={pending} onResolved={() => poller.now()} />
            </div>
          )}
        </Card>

        <div className="min-h-0 overflow-y-auto">
          {selectedQuestion === null ? (
            <Card>
              <div className="flex flex-col items-start gap-2 px-4 py-10">
                <div className="w-full border-t border-dashed border-line" />
                <h3 className="pt-3 text-sm font-medium text-primary">No question selected</h3>
                <p className="max-w-prose text-sm text-secondary">
                  Choose a question on the left to see its answer, the passages behind it, and how
                  its confidence was computed. Nothing here is asked of a model — every figure is
                  measured.
                </p>
              </div>
            </Card>
          ) : (
            <AnswerCard
              question={selectedQuestion}
              answer={answerIndex.get(selectedQuestion.question_id) ?? null}
            />
          )}
        </div>
      </div>

      <p className="text-xs text-muted">
        Review <span className="font-mono">{reviewId}</span> · round{' '}
        <span className="font-mono">{roundId}</span>
        {runId !== null ? (
          <>
            {' '}
            · run <span className="font-mono">{runId}</span>
          </>
        ) : null}
      </p>
    </div>
  );
}
