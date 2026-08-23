'use client';

import { useEffect, useMemo, useState } from 'react';

import { Empty, Failure, Label, Mono, cx } from '@/components/ui/primitives';
import type { AuditEvent, QuestionRow } from '@/lib/api/client';
import { absolute, humanKind } from '@/lib/format';

/**
 * The compliance plane, unedited.
 *
 * Everything else in this product is a reading of these rows. The thread aggregates them
 * into a narrative, the grid renders what they produced, the export summarises them — and
 * this is where an auditor comes to check that the readings are honest. So it shows the
 * events as written: the kind, the identity that wrote it, the question it concerns, the
 * time, and the whole detail object.
 *
 * ## Filters, and why every one of them can do something
 *
 * The agent and kind filters are built from the events **present in this trail**, not from
 * the set of kinds the system can emit. A filter listing `tool_denied 0` beside kinds that
 * have rows is a control that cannot do anything, and Phase 7 removed three of those from
 * other surfaces for the same reason.
 */

/** How many events one read takes in. Matches the control plane's own ceiling. */
const CEILING = 4000;

/**
 * How many rows are put in the DOM at once.
 *
 * The grid next door is virtualised; this is not, because an audit row expands to a
 * variable-height detail block and a fixed-height windower cannot carry that. So the list
 * grows on request instead, and the control names how many are left rather than the list
 * simply stopping.
 */
const PAGE = 400;

export function AuditPanel({
  reviewId,
  questions,
  focus,
}: {
  reviewId: string;
  questions: QuestionRow[];
  /** A question id to start filtered on, when arriving from somewhere that named one. */
  focus?: string | null;
}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actor, setActor] = useState<string>('all');
  const [kind, setKind] = useState<string>('all');
  const [question, setQuestion] = useState<string | null>(focus ?? null);
  const [opened, setOpened] = useState<string | null>(null);
  const [shown, setShown] = useState(PAGE);

  // Fetched when this tab is opened, not with the page. It is up to a thousand documents
  // that no other tab renders, and loading it on every review page load was a second full
  // read of the trail nobody was looking at.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(
          `/api/attestor/reviews/${encodeURIComponent(reviewId)}/audit?limit=${CEILING}`,
          { cache: 'no-store' },
        );
        if (!response.ok) {
          setError(`The control plane returned ${response.status}.`);
          return;
        }
        const payload = (await response.json()) as AuditEvent[];
        if (!cancelled) setEvents(payload);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reviewId]);

  const ordered = useMemo(
    () =>
      [...events].sort((a, b) =>
        String(b.occurred_at ?? b.recorded_at ?? '').localeCompare(
          String(a.occurred_at ?? a.recorded_at ?? ''),
        ),
      ),
    [events],
  );

  const actors = useMemo(
    () => count(ordered.map((event) => event.actor ?? 'unattributed')),
    [ordered],
  );
  const kinds = useMemo(() => count(ordered.map((event) => event.kind)), [ordered]);

  const questionText = useMemo(
    () => new Map(questions.map((row) => [row.question_id, row.text])),
    [questions],
  );

  // Reset the window whenever the filters change, so narrowing a 1,200-event trail to nine
  // rows does not leave a "Show 400 more" control under a list of nine.
  useEffect(() => {
    setShown(PAGE);
  }, [actor, kind, question]);

  const rows = ordered.filter(
    (event) =>
      (actor === 'all' || (event.actor ?? 'unattributed') === actor) &&
      (kind === 'all' || event.kind === kind) &&
      (question === null || event.question_id === question),
  );

  if (error !== null) {
    return (
      <div className="p-4">
        <Failure what="The audit trail could not be read." detail={error} />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-col gap-2 border-b border-subtle px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <Chips
            label="agent"
            value={actor}
            counts={actors}
            onChange={setActor}
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <Chips label="kind" value={kind} counts={kinds} onChange={setKind} humanise />
        </div>
        {question !== null ? (
          <div className="flex items-center gap-2">
            <Label>one question</Label>
            <span className="min-w-0 flex-1 truncate text-xs text-secondary">
              {questionText.get(question) ?? question}
            </span>
            <button
              type="button"
              onClick={() => setQuestion(null)}
              className="text-xs text-accent-text hover:underline"
            >
              Clear
            </button>
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <Empty title="Reading the trail." hint="Up to a thousand events for this review." />
        ) : rows.length === 0 ? (
          <Empty
            title="No event matches this filter."
            hint="Clear the agent or kind filter. Every filter listed here has at least one event behind it, so an empty result means the combination is empty, not the trail."
          />
        ) : (
          <ul>
            {rows.slice(0, shown).map((event, index) => {
              const id = event.event_id ?? `${event.kind}-${event.occurred_at}-${index}`;
              const isOpen = opened === id;
              return (
                <li key={id} className="border-b border-subtle">
                  <button
                    type="button"
                    onClick={() => setOpened(isOpen ? null : id)}
                    className="flex w-full items-baseline gap-4 px-4 py-2 text-left hover:bg-hover"
                  >
                    <span
                      className="shrink-0"
                      title={absolute(event.occurred_at ?? event.recorded_at)}
                    >
                      <Mono dim>
                        {String(event.occurred_at ?? event.recorded_at ?? '')
                          .slice(11, 19)}
                      </Mono>
                    </span>
                    <span className="w-list max-w-list shrink-0 truncate text-sm text-primary">
                      {humanKind(event.kind)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-xs text-secondary">
                      {event.actor ?? 'unattributed'}
                    </span>
                    {event.question_id ? (
                      <span className="hidden shrink-0 lg:block">
                        <Mono dim>{event.question_id.slice(0, 8)}</Mono>
                      </span>
                    ) : null}
                  </button>
                  {isOpen ? (
                    <div className="flex flex-col gap-2 px-4 pb-3">
                      {event.question_id ? (
                        <button
                          type="button"
                          onClick={() => setQuestion(event.question_id ?? null)}
                          className="self-start text-xs text-accent-text hover:underline"
                        >
                          Show only this question
                        </button>
                      ) : null}
                      <dl className="flex flex-col gap-1">
                        {Object.entries(event.detail ?? {}).map(([key, value]) => (
                          <div key={key} className="flex items-baseline gap-4">
                            <dt className="w-list max-w-list shrink-0 truncate text-xs text-muted">
                              {key.replace(/_/g, ' ')}
                            </dt>
                            <dd className="min-w-0 flex-1 break-all font-mono text-xs text-secondary">
                              {stringify(value)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                      <div className="flex flex-wrap items-center gap-4">
                        {event.run_id ? <Mono dim>run {event.run_id}</Mono> : null}
                        {event.round_id ? <Mono dim>{event.round_id}</Mono> : null}
                      </div>
                    </div>
                  ) : null}
                </li>
              );
            })}
            {rows.length > shown ? (
              <li className="px-4 py-3">
                <button
                  type="button"
                  onClick={() => setShown((count) => count + PAGE)}
                  className="text-sm text-accent-text hover:underline"
                >
                  Show {Math.min(PAGE, rows.length - shown)} more of {rows.length - shown}
                </button>
              </li>
            ) : null}
          </ul>
        )}
      </div>

      <p className="shrink-0 border-t border-subtle px-4 py-2 text-xs text-muted">
        <Mono dim>{Math.min(shown, rows.length)}</Mono> shown of{' '}
        <Mono dim>{rows.length}</Mono> matching, <Mono dim>{ordered.length}</Mono> read.
        {ordered.length >= CEILING ? (
          <>
            {' '}
            This review has more events than one read takes in, so this is the first{' '}
            <Mono dim>{CEILING}</Mono> of them.
          </>
        ) : null}{' '}
        Append-only: the repository has no update and no delete, so nothing here was edited
        after it was written.
      </p>
    </div>
  );
}

function Chips({
  label,
  value,
  counts,
  onChange,
  humanise = false,
}: {
  label: string;
  value: string;
  counts: Array<[string, number]>;
  onChange: (next: string) => void;
  humanise?: boolean;
}) {
  return (
    <>
      <Label>{label}</Label>
      <nav className="flex flex-wrap items-center gap-1" aria-label={`Filter by ${label}`}>
        <Chip active={value === 'all'} onClick={() => onChange('all')}>
          all
        </Chip>
        {counts.map(([name, total]) => (
          <Chip key={name} active={value === name} onClick={() => onChange(name)}>
            {humanise ? humanKind(name) : name}
            <span className="font-mono tabular-nums text-muted">{total}</span>
          </Chip>
        ))}
      </nav>
    </>
  );
}

function Chip({
  children,
  active,
  onClick,
}: {
  children: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className={cx(
        'inline-flex items-center gap-2 rounded-sm px-2 py-1 text-xs transition-colors',
        active ? 'bg-active text-primary' : 'text-secondary hover:bg-hover hover:text-primary',
      )}
    >
      {children}
    </button>
  );
}

function count(values: string[]): Array<[string, number]> {
  const totals = new Map<string, number>();
  for (const value of values) totals.set(value, (totals.get(value) ?? 0) + 1);
  return [...totals.entries()].sort((a, b) => b[1] - a[1]);
}

function stringify(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.map(stringify).join(', ') || '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
