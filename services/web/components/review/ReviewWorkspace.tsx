'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { AnswerCard } from '@/components/review/AnswerCard';
import { ApprovalQueue } from '@/components/review/ApprovalQueue';
import { ExportPanel } from '@/components/review/ExportPanel';
import { FleetActivity } from '@/components/review/FleetActivity';
import { QuestionGrid } from '@/components/review/QuestionGrid';
import { StreamIndicator } from '@/components/review/StreamIndicator';
import { Button, Card, ErrorState, StateLegend, Tabs } from '@/components/ui/primitives';
import { createPoller } from '@/lib/poll';
import { openRunStream, type RunEventFrame, type StreamHealth } from '@/lib/sse';
import { STATE_ORDER } from '@/lib/states';
import type { AnswerRow, AuditEvent, QuestionRow } from '@/lib/api/client';

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
 * The one exception is the orchestrator's judgement events, which `FleetActivity` accumulates
 * from the stream directly. Those are not rows to refetch — they are an append-only log, the
 * frame carries the whole event, and there is no endpoint that returns "the last four decisions"
 * to refetch from. Nothing derived from them is used to render an answer.
 *
 * ## The three failure modes, wired
 *
 * - The stream errors → `lib/sse.ts` reconnects with the resume point.
 * - The stream goes quiet without erroring → the heartbeat watchdog reports `stale`, and the
 *   poller starts. This is the one that actually happens, and a fallback armed on `onerror`
 *   would sit idle through it.
 * - The stream reconnects and skips → a `seq` gap fires `onGap`, which forces a full refetch
 *   rather than leaving a hole.
 *
 * ## Why refetches are coalesced
 *
 * A 312-question review emits ~949 audit events, and every one of them means the round moved.
 * The first version of this component called `refetch()` on each — two reads apiece, so roughly
 * 1,900 requests through the proxy to a control plane running `--max-instances 4`, arriving in
 * bursts as three partitions drafted in parallel. That is what produced the
 * `429 on refresh` banner in the screen recording: not the polling fallback (which is stopped
 * while the stream is live) and not Cloud Run's instance cap on its own, but the page asking
 * for the same 312 rows a thousand times in twelve minutes.
 *
 * `scheduleRefetch` collapses a burst into one read on a trailing edge, with a floor between
 * reads. The grid still fills in visibly — a second of latency is invisible next to a
 * forty-second drafting call — and the request count drops by roughly two orders of magnitude.
 */

type Props = {
  reviewId: string;
  roundId: string;
  runId: string | null;
  initialQuestions: QuestionRow[];
  initialAnswers: AnswerRow[];
  /** The orchestrator's judgement events as of the server render. Filtered server-side. */
  initialJudgements: AuditEvent[];
  /** Set when the server-side read failed. The page renders the error rather than an empty grid. */
  loadError: string | null;
};

type Tab = 'questions' | 'approvals' | 'export';

/**
 * No more than one read per this many milliseconds, however many events arrive.
 *
 * 1,200ms rather than something smaller because the thing being watched takes ~45 seconds per
 * question. Sub-second freshness on a twelve-minute process is precision nobody can perceive,
 * bought with requests that trip a rate limit.
 */
const MIN_REFETCH_INTERVAL_MS = 1_200;

export function ReviewWorkspace({
  reviewId,
  roundId,
  runId,
  initialQuestions,
  initialAnswers,
  initialJudgements,
  loadError,
}: Props) {
  const [questions, setQuestions] = useState(initialQuestions);
  const [answers, setAnswers] = useState(initialAnswers);
  const [judgements, setJudgements] = useState<AuditEvent[]>(initialJudgements);
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
  // Rendered as monospace metadata: the count of events observed against the count of reads they
  // caused. It is the coalescing being visible rather than asserted, and on a 949-event run it is
  // the difference between ~1,900 reads and a few dozen.
  const [observed, setObserved] = useState(0);
  const [reads, setReads] = useState(0);

  // A ref rather than state: the fetch compares against the previous payload to decide whether
  // anything changed, and reading that through state would capture a stale closure inside the
  // poller, which never re-creates itself.
  const fingerprint = useRef('');

  const refetch = useCallback(async (): Promise<boolean> => {
    setReads((count) => count + 1);
    const [questionResponse, answerResponse] = await Promise.all([
      fetch(`/api/attestor/rounds/${encodeURIComponent(roundId)}/questions`, {
        cache: 'no-store',
      }),
      fetch(`/api/attestor/rounds/${encodeURIComponent(roundId)}/answers`, { cache: 'no-store' }),
    ]);
    if (!questionResponse.ok || !answerResponse.ok) {
      const status = !questionResponse.ok ? questionResponse.status : answerResponse.status;
      setRefreshError(
        status === 429
          ? 'The control plane rate-limited a refresh. The next one will be spaced further apart.'
          : `The control plane returned ${status} on refresh.`,
      );
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

  // -- coalescing ------------------------------------------------------------------------
  //
  // Refs rather than state throughout: this runs inside the stream's event handler, which is
  // created once, and anything read through state there would be the value from first render.
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

    // Already queued, or one is in the air: this burst is covered. A trailing edge rather than a
    // leading one, so the read that lands is the read after the last event rather than one taken
    // before the burst finished writing.
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
        if (JUDGEMENT_KINDS.has(event.kind)) {
          // Appended, not refetched. See the module note: these are log entries, not rows.
          setJudgements((previous) => [...previous, event as AuditEvent].slice(-24));
        }
        // Every event means the round moved. Coalesced rather than immediate; see above.
        scheduleRefetch();
      },
      onHealth: (next, detail) => {
        setHealth(next);
        setHealthDetail(detail);
        // Armed by staleness, not by an error. The whole reason `lib/poll.ts` exists.
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
        // A gap means events were missed; only a full read can say what they were. Not
        // coalesced — a gap is rare and is the one case where being current matters more than
        // being sparing.
        void refetch().catch(() => {});
      },
    });

    return () => {
      stream.close();
      poller.stop();
    };
  }, [runId, refetch, poller, scheduleRefetch]);

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
          action={<Button onClick={() => window.location.reload()}>Try again</Button>}
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
              observed={observed}
              reads={reads}
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

      <Card>
        <div className="px-4 py-3">
          <FleetActivity
            questions={questions}
            answers={answerIndex}
            events={judgements}
            total={questions.length}
          />
        </div>
      </Card>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,5fr)_minmax(0,7fr)] gap-3">
        <Card className="flex min-h-0 flex-col overflow-hidden">
          <Tabs
            tabs={[
              { id: 'questions', label: 'Questions', count: questions.length },
              { id: 'approvals', label: 'Needs a human', count: pending.length },
              { id: 'export', label: 'Export' },
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
          ) : tab === 'approvals' ? (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <ApprovalQueue pending={pending} onResolved={() => poller.now()} />
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <ExportPanel reviewId={reviewId} roundId={roundId} />
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

/** Kept in step with `FleetActivity`'s own set, which is the one that renders them. */
const JUDGEMENT_KINDS = new Set(['plan_selected', 'retry_decided', 'run_completed']);
