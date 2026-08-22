'use client';

import { useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { AnswerCard } from '@/components/review/AnswerCard';
import { ApprovalQueue } from '@/components/review/ApprovalQueue';
import { FleetActivity } from '@/components/review/FleetActivity';
import { EMPTY_FILTERS, QuestionGrid, type GridFilters } from '@/components/review/QuestionGrid';
import { StreamIndicator } from '@/components/review/StreamIndicator';
import { Button, Empty, Failure, cx } from '@/components/ui/primitives';
import type { AnswerRow, AuditEvent, Department, QuestionRow } from '@/lib/api/client';
import { createPoller } from '@/lib/poll';
import { openRunStream, type RunEventFrame, type StreamHealth } from '@/lib/sse';
import type { StateKey } from '@/lib/states';

/**
 * The review workspace, which **is** the product. Everything else is navigation to it.
 *
 * ## Three panes, not three pages
 *
 * A list of questions, the selected answer with its evidence, and a live band across the top
 * saying what the fleet is doing. All visible at once, because the job is scanning 312 rows
 * and stopping on the ones that need a person — and a design where reading an answer means
 * losing the list is a design that makes that job harder.
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
 * frame carries the whole event, and there is no endpoint that returns "the last four
 * decisions" to refetch from. Nothing derived from them is used to render an answer.
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
 * bursts as three partitions drafted in parallel. That is what produced the `429 on refresh`
 * banner in the screen recording: not the polling fallback (which is stopped while the stream
 * is live) and not Cloud Run's instance cap on its own, but the page asking for the same 312
 * rows a thousand times in twelve minutes.
 *
 * `scheduleRefetch` collapses a burst into one read on a trailing edge, with a floor between
 * reads. The grid still fills in visibly — a second of latency is invisible next to a
 * forty-second drafting call — and the request count drops by roughly two orders of magnitude.
 *
 * ## Filters live in the URL
 *
 * `?q=encryption&dept=legal&state=flagged&sel=<question id>` is a link a person can send, and
 * "here is the row I am looking at" is the most common thing anyone wants to do with a
 * console. `router.replace` rather than `push`, so filtering does not fill the back button
 * with every keystroke.
 */

type Props = {
  roundId: string;
  runId: string | null;
  initialQuestions: QuestionRow[];
  initialAnswers: AnswerRow[];
  /** Set when the server-side read failed. The page renders the error rather than an empty grid. */
  loadError: string | null;
  /**
   * A row another surface asked for. The thread names a question in an expanded block and
   * the evidence panel names one per document; both land here, on that row, rather than at
   * the top of a list of 312 with the reader left to find it.
   */
  focusQuestion?: string | null;
};

/**
 * No more than one read per this many milliseconds, however many events arrive.
 *
 * 1,200ms rather than something smaller because the thing being watched takes ~45 seconds per
 * question. Sub-second freshness on a twelve-minute process is precision nobody can perceive,
 * bought with requests that trip a rate limit.
 */
const MIN_REFETCH_INTERVAL_MS = 1_200;

/** Kept in step with `FleetActivity`'s own set, which is the one that renders them. */
const JUDGEMENT_KINDS = new Set(['plan_selected', 'retry_decided', 'run_completed']);

export function ReviewWorkspace({
  roundId,
  runId,
  initialQuestions,
  initialAnswers,
  loadError,
  focusQuestion,
}: Props) {
  const params = useSearchParams();

  const [questions, setQuestions] = useState(initialQuestions);
  const [answers, setAnswers] = useState(initialAnswers);
  // Accumulated from the stream rather than seeded from a server read. Seeding it cost a
  // full audit read of the review on every page load, taken to render at most four lines;
  // the band fills in from the stream within a second of a run doing anything, and on a
  // finished run there is nothing live to show anyway.
  const [judgements, setJudgements] = useState<AuditEvent[]>([]);

  // The URL seeds this state and then mirrors it. It is deliberately NOT the source of
  // truth, and the first version of this component made it one -- `router.replace` per
  // keystroke, with `dynamic = 'force-dynamic'` on the page, so every press of `j` was an
  // RSC round trip. Pressing it three times in a row moved the selection once and then
  // stopped, which is how this was found: by pressing the key, not by reading the code.
  //
  // So navigation is local state, and `history.replaceState` writes the shareable URL
  // afterwards. Same link, no round trip, and the grid responds at the speed of a keypress.
  // Two views, not four. Export and Artifacts were tabs here and are now tabs of the
  // review itself, one level up: they are properties of the *review*, not of the question
  // the cursor happens to be on, and nesting them under a selected row was a filing
  // mistake that put the deliverable three clicks from the landing surface.
  const [view, setView] = useState<'answer' | 'queue'>(() =>
    params.get('view') === 'queue' ? 'queue' : 'answer',
  );
  const [selected, setSelected] = useState<string | null>(
    () => params.get('sel') ?? initialQuestions[0]?.question_id ?? null,
  );
  const [filters, setFilters] = useState<GridFilters>(() => ({
    query: params.get('q') ?? '',
    department: (params.get('dept') as Department | 'all') ?? EMPTY_FILTERS.department,
    state: (params.get('state') as StateKey | 'all') ?? EMPTY_FILTERS.state,
  }));

  const showQueue = view === 'queue';

  // A row another surface asked for wins over whatever was selected here.
  useEffect(() => {
    if (focusQuestion) setSelected(focusQuestion);
  }, [focusQuestion]);

  useEffect(() => {
    const next = new URLSearchParams(window.location.search);
    next.delete('q');
    next.delete('dept');
    next.delete('state');
    next.delete('view');
    next.delete('sel');
    if (filters.query) next.set('q', filters.query);
    if (filters.department !== 'all') next.set('dept', filters.department);
    if (filters.state !== 'all') next.set('state', filters.state);
    if (view !== 'answer') next.set('view', view);
    if (selected) next.set('sel', selected);
    const query = next.toString();
    const url = query ? `${window.location.pathname}?${query}` : window.location.pathname;
    if (url !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', url);
    }
  }, [filters, view, selected]);

  const [health, setHealth] = useState<StreamHealth>('closed');
  const [healthDetail, setHealthDetail] = useState('Not watching.');
  const [lastSeq, setLastSeq] = useState(0);
  const [gaps, setGaps] = useState(0);
  const [polling, setPolling] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  // Rendered as monospace metadata: the count of events observed against the count of reads
  // they caused. It is the coalescing being visible rather than asserted, and on a 949-event
  // run it is the difference between ~1,900 reads and a few dozen.
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

    // Already queued, or one is in the air: this burst is covered. A trailing edge rather than
    // a leading one, so the read that lands is the read after the last event rather than one
    // taken before the burst finished writing.
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
        // coalesced -- a gap is rare and is the one case where being current matters more than
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

  /**
   * `a` in the grid. Opens the queue on the selected row rather than approving it outright.
   *
   * A keystroke that fires an irreversible decision with no confirmation is a keystroke that
   * will eventually fire on the wrong row. The shortcut takes you to where the answer, its
   * citations and the approve control are all on screen together — which is the point at
   * which a person can actually be said to have approved something.
   */
  const onApprove = useCallback(() => {
    const answer = selected ? answerIndex.get(selected) : undefined;
    if (answer?.status !== 'needs_human') return;
    setView('queue');
  }, [selected, answerIndex]);

  if (loadError !== null) {
    return (
      <div className="p-4">
        <Failure what="This round could not be read." detail={loadError} />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* The band. Live counters, which engines are drafting, and the orchestrator's own
          decisions -- the component that makes autonomy legible rather than asserted. */}
      <div className="shrink-0 border-b border-subtle px-4 py-3">
        <FleetActivity
          questions={questions}
          answers={answerIndex}
          events={judgements}
          total={questions.length}
        />
      </div>

      {refreshError !== null ? (
        <div className="shrink-0 px-4 pt-3">
          <Failure what="The last refresh failed." detail={refreshError} />
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,4fr)_minmax(0,6fr)]">
        {/* Pane two: the list. Owns its own scrolling and its own keyboard. */}
        <div className="flex min-h-0 flex-col border-r border-subtle">
          <QuestionGrid
            questions={questions}
            answers={answerIndex}
            selectedId={selected}
            onSelect={setSelected}
            filters={filters}
            onFilters={setFilters}
            onApprove={onApprove}
          />
        </div>

        {/* Pane three: the answer, its evidence, and the two things a person does with it. */}
        <div className="flex min-h-0 min-w-detail flex-col">
          <nav className="flex shrink-0 items-center gap-1 border-b border-subtle px-4 py-2">
            <ViewTab label="Answer" active={!showQueue} onClick={() => setView('answer')} />
            <ViewTab
              label="Needs a human"
              count={pending.length}
              active={showQueue}
              onClick={() => setView('queue')}
            />
            <div className="ml-auto flex items-center gap-2">
              {runId === null ? (
                <span className="text-xs text-muted">No active run</span>
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
              <Button tone="ghost" small onClick={() => poller.now()}>
                Refresh
              </Button>
            </div>
          </nav>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {showQueue ? (
              <ApprovalQueue
                pending={pending}
                focus={selected}
                onResolved={() => poller.now()}
              />
            ) : selectedQuestion === null ? (
              <Empty
                title="No question selected"
                hint={
                  questions.length === 0
                    ? 'Intake has not finished. Questions appear here as the questionnaire is parsed — there is nothing to show yet, and nothing has failed.'
                    : 'Press j or k to move through the list. The pane shows the answer, the passages behind it, and how its confidence was computed — every figure measured, none asked of a model.'
                }
              />
            ) : (
              <div className="p-4">
                <AnswerCard
                  question={selectedQuestion}
                  answer={answerIndex.get(selectedQuestion.question_id) ?? null}
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ViewTab({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className={cx(
        'inline-flex items-center gap-2 rounded-sm px-2 py-1 text-sm transition-colors',
        active ? 'bg-active font-medium text-primary' : 'text-secondary hover:bg-hover',
      )}
    >
      {label}
      {typeof count === 'number' && count > 0 ? (
        <span className="font-mono text-xs tabular-nums text-muted">{count}</span>
      ) : null}
    </button>
  );
}
