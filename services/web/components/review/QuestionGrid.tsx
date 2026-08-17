'use client';

import { useMemo, useRef, useState } from 'react';

import { ConfidenceInline } from '@/components/review/ConfidenceMeter';
import { Mono, StateBadge, cx } from '@/components/ui/primitives';
import { STATE_ORDER, STATES, type StateKey, stateFor } from '@/lib/states';
import type { AnswerRow, Department, QuestionRow } from '@/lib/api/client';

/**
 * 312 rows, filterable, and virtualised because 312 rows of DOM with a badge and a meter in
 * each is enough to make scrolling stutter on a recording.
 *
 * ## The virtualisation is deliberately hand-rolled and deliberately simple
 *
 * Fixed row height, a spacer above and below, and a slice in the middle. That is the whole
 * technique. It works because every row here IS the same height — the design fixes it at
 * `--row-height` precisely so this stays true — and a fixed-height windower has no measurement
 * pass, no ResizeObserver, and nothing that can produce a scroll position that drifts. A
 * general-purpose virtual list would be a dependency, a bundle, and a class of bug that only
 * shows up while someone is filming.
 *
 * `OVERSCAN` rows above and below the viewport are rendered so a fast scroll does not reveal
 * blank space.
 */

const ROW_HEIGHT = 34;
const OVERSCAN = 8;

type Row = {
  question: QuestionRow;
  answer: AnswerRow | null;
  state: StateKey;
};

const DEPARTMENTS: ReadonlyArray<Department | 'all'> = [
  'all',
  'security',
  'legal',
  'engineering',
  'unassigned',
];

export function QuestionGrid({
  questions,
  answers,
  selectedId,
  onSelect,
}: {
  questions: QuestionRow[];
  answers: Map<string, AnswerRow>;
  selectedId: string | null;
  onSelect: (questionId: string) => void;
}) {
  const [department, setDepartment] = useState<Department | 'all'>('all');
  const [stateFilter, setStateFilter] = useState<StateKey | 'all'>('all');
  const [query, setQuery] = useState('');
  const [scrollTop, setScrollTop] = useState(0);
  const viewport = useRef<HTMLDivElement | null>(null);

  const rows = useMemo<Row[]>(
    () =>
      questions.map((question) => {
        const answer = answers.get(question.question_id) ?? null;
        return {
          question,
          answer,
          state: stateFor(answer?.status ?? null, answer?.citations.length ?? 0).key,
        };
      }),
    [questions, answers],
  );

  // Counts are computed over the UNFILTERED rows on purpose. A filter chip that shows how many
  // rows match after the filter is applied always reads as the current selection's size, which
  // is the one number the user already knows.
  const counts = useMemo(() => {
    const byState = new Map<StateKey, number>();
    for (const row of rows) byState.set(row.state, (byState.get(row.state) ?? 0) + 1);
    return byState;
  }, [rows]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (department !== 'all' && row.question.department !== department) return false;
      if (stateFilter !== 'all' && row.state !== stateFilter) return false;
      if (needle.length > 0) {
        const haystack = `${row.question.text} ${row.question.question_id}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [rows, department, stateFilter, query]);

  const height = viewport.current?.clientHeight ?? 600;
  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visible = Math.ceil(height / ROW_HEIGHT) + OVERSCAN * 2;
  const slice = filtered.slice(first, first + visible);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-2 border-b border-subtle px-4 py-2.5">
        <div className="flex items-center gap-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter questions"
            aria-label="Filter questions"
            className={cx(
              'w-full rounded-sm border border-subtle bg-sunken px-2 py-1 text-sm',
              'text-primary placeholder:text-muted',
            )}
          />
          <Mono dim className="shrink-0 tabular-nums">
            {filtered.length}/{rows.length}
          </Mono>
        </div>

        <div className="flex flex-wrap items-center gap-1">
          {DEPARTMENTS.map((value) => (
            <Chip
              key={value}
              label={value === 'all' ? 'All departments' : value}
              active={department === value}
              onClick={() => setDepartment(value)}
            />
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-1">
          <Chip label="Any state" active={stateFilter === 'all'} onClick={() => setStateFilter('all')} />
          {STATE_ORDER.map((key) => (
            <Chip
              key={key}
              label={STATES[key].label}
              count={counts.get(key) ?? 0}
              active={stateFilter === key}
              onClick={() => setStateFilter(key)}
              // Every chip carries its own state dot, so the vocabulary is legible without a
              // separate legend lookup while scanning.
              state={key}
            />
          ))}
        </div>
      </div>

      <div
        ref={viewport}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {filtered.length === 0 ? (
          <p className="px-4 py-8 text-sm text-secondary">
            No question matches this filter. Clear it to see all {rows.length}.
          </p>
        ) : (
          <div style={{ height: filtered.length * ROW_HEIGHT, position: 'relative' }}>
            <div style={{ transform: `translateY(${first * ROW_HEIGHT}px)` }}>
              {slice.map((row) => {
                const state = STATES[row.state];
                const selected = row.question.question_id === selectedId;
                return (
                  <button
                    key={row.question.question_id}
                    onClick={() => onSelect(row.question.question_id)}
                    aria-current={selected ? 'true' : undefined}
                    style={{ height: ROW_HEIGHT }}
                    className={cx(
                      'flex w-full items-center gap-3 border-b border-subtle px-4 text-left',
                      'transition-colors',
                      selected ? 'bg-active' : 'hover:bg-hover',
                    )}
                  >
                    <StateBadge state={state} compact />
                    <span className="min-w-0 flex-1 truncate text-sm text-primary">
                      {row.question.text}
                    </span>
                    <span className="w-24 shrink-0 truncate text-xs text-muted">
                      {row.question.department}
                    </span>
                    <span className="w-20 shrink-0 text-right">
                      {row.answer ? <ConfidenceInline answer={row.answer} /> : null}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Chip({
  label,
  count,
  active,
  onClick,
  state,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
  state?: StateKey;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-xs',
        'transition-colors',
        active ? 'border-strong bg-active text-primary' : 'border-subtle text-muted hover:bg-hover',
      )}
    >
      {state ? <StateBadge state={STATES[state]} compact /> : null}
      <span>{label}</span>
      {typeof count === 'number' ? (
        <span className="font-mono tabular-nums text-muted">{count}</span>
      ) : null}
    </button>
  );
}
