'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

import { ConfidenceInline } from '@/components/review/ConfidenceMeter';
import { Key, Mono, StateDot, cx } from '@/components/ui/primitives';
import { STATE_ORDER, STATES, type StateKey, stateFor } from '@/lib/states';
import type { AnswerRow, Department, QuestionRow } from '@/lib/api/client';

/**
 * The middle pane: 312 rows, filterable, keyboard-navigable, virtualised.
 *
 * ## Keyboard first, and why that is a product decision rather than a flourish
 *
 * A compliance operator working through 312 rows does not reach for a mouse, and a judge can
 * tell within two seconds whether an interface expects them to. `j`/`k` move, `/` focuses the
 * filter, `a` approves the selected answer, `e` opens its evidence. Every one of those is
 * shown in the footer rather than hidden in a help modal, because a shortcut nobody knows
 * about is a shortcut that does not exist.
 *
 * The handler ignores bare letters while focus is in a text field. Without that check, typing
 * "just" into the filter would move the selection four times and open an approval.
 *
 * ## The virtualisation is deliberately hand-rolled and deliberately simple
 *
 * Fixed row height, a spacer above and below, and a slice in the middle. That is the whole
 * technique. It works because every row here IS the same height — the design fixes it at
 * `--row-height-dense` precisely so this stays true — and a fixed-height windower has no
 * measurement pass, no ResizeObserver, and nothing that can produce a scroll position that
 * drifts. A general-purpose virtual list would be a dependency, a bundle, and a class of bug
 * that only shows up while someone is filming.
 */

const ROW_HEIGHT = 32;
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

export type GridFilters = {
  department: Department | 'all';
  state: StateKey | 'all';
  query: string;
};

export const EMPTY_FILTERS: GridFilters = { department: 'all', state: 'all', query: '' };

function isTextEntry(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable;
}

export function QuestionGrid({
  questions,
  answers,
  selectedId,
  onSelect,
  filters,
  onFilters,
  onApprove,
}: {
  questions: QuestionRow[];
  answers: Map<string, AnswerRow>;
  selectedId: string | null;
  onSelect: (questionId: string) => void;
  filters: GridFilters;
  onFilters: (next: GridFilters) => void;
  /** Fired by `a`. The workspace decides whether the selected answer can be approved. */
  onApprove: () => void;
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const viewport = useRef<HTMLDivElement | null>(null);
  const search = useRef<HTMLInputElement | null>(null);

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
    const needle = filters.query.trim().toLowerCase();
    return rows.filter((row) => {
      if (filters.department !== 'all' && row.question.department !== filters.department)
        return false;
      if (filters.state !== 'all' && row.state !== filters.state) return false;
      if (needle.length > 0) {
        const haystack = `${row.question.text} ${row.question.question_id}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [rows, filters]);

  const index = filtered.findIndex((row) => row.question.question_id === selectedId);

  // -- keyboard ------------------------------------------------------------------------
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === '/' ) {
        if (isTextEntry(event.target)) return;
        event.preventDefault();
        search.current?.focus();
        return;
      }
      if (isTextEntry(event.target)) return;

      const move = (delta: number) => {
        if (filtered.length === 0) return;
        const next = Math.max(0, Math.min(filtered.length - 1, (index === -1 ? 0 : index) + delta));
        const row = filtered[next];
        if (row === undefined) return;
        onSelect(row.question.question_id);
        // Keep the cursor in view without smooth scrolling, which lags behind a held key.
        const top = next * ROW_HEIGHT;
        const element = viewport.current;
        if (element) {
          if (top < element.scrollTop) element.scrollTop = top;
          else if (top + ROW_HEIGHT > element.scrollTop + element.clientHeight) {
            element.scrollTop = top + ROW_HEIGHT - element.clientHeight;
          }
        }
      };

      if (event.key === 'j' || event.key === 'ArrowDown') {
        event.preventDefault();
        move(1);
      } else if (event.key === 'k' || event.key === 'ArrowUp') {
        event.preventDefault();
        move(-1);
      } else if (event.key === 'a') {
        event.preventDefault();
        onApprove();
      }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [filtered, index, onSelect, onApprove]);

  const height = viewport.current?.clientHeight ?? 600;
  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visible = Math.ceil(height / ROW_HEIGHT) + OVERSCAN * 2;
  const slice = filtered.slice(first, first + visible);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/*
        Thirteen controls stacked in three rows is what a filter bar looks like when every
        option gets a chip. Two changes halve it without losing anything: departments become
        one select, and a state chip only appears when the round actually has answers in that
        state. On a real round that is three or four rather than seven -- and a chip reading
        `Denied 0` is a control that cannot do anything, taking up space next to ones that can.
      */}
      <div className="flex flex-col gap-3 border-b border-subtle px-4 py-3">
        <div className="flex items-center gap-2">
          <input
            ref={search}
            value={filters.query}
            onChange={(event) => onFilters({ ...filters, query: event.target.value })}
            onKeyDown={(event) => {
              if (event.key === 'Escape') event.currentTarget.blur();
            }}
            placeholder="Filter questions"
            aria-label="Filter questions"
            className="h-row-dense min-w-0 flex-1 rounded-sm border border-subtle bg-transparent px-3 text-sm text-primary outline-none transition-colors placeholder:text-muted focus:border-line"
          />
          <select
            value={filters.department}
            aria-label="Department"
            onChange={(event) =>
              onFilters({ ...filters, department: event.target.value as Department | 'all' })
            }
            className="h-row-dense shrink-0 rounded-sm border border-subtle bg-transparent px-2 text-sm text-secondary outline-none"
          >
            {DEPARTMENTS.map((value) => (
              <option key={value} value={value}>
                {value === 'all' ? 'All departments' : value}
              </option>
            ))}
          </select>
          <Mono dim className="shrink-0">
            {filtered.length}/{rows.length}
          </Mono>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Chip
            label="Any state"
            active={filters.state === 'all'}
            onClick={() => onFilters({ ...filters, state: 'all' })}
          />
          {STATE_ORDER.filter((key) => (counts.get(key) ?? 0) > 0 || filters.state === key).map(
            (key) => (
              <Chip
                key={key}
                label={STATES[key].label}
                count={counts.get(key) ?? 0}
                active={filters.state === key}
                onClick={() => onFilters({ ...filters, state: key })}
                state={key}
              />
            ),
          )}
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
                      'flex w-full items-center gap-3 border-b border-subtle px-4 text-left transition-colors',
                      selected ? 'bg-active' : 'hover:bg-hover',
                    )}
                  >
                    <span className={cx('flex shrink-0 items-center', state.ink)} title={state.meaning}>
                      <StateDot form={state.form} />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-primary">
                      {row.question.text}
                    </span>
                    <span className="hidden shrink-0 text-xs text-muted xl:block">
                      {row.question.department}
                    </span>
                    <span className="shrink-0">
                      {row.answer ? <ConfidenceInline answer={row.answer} /> : null}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <footer className="flex shrink-0 items-center gap-3 border-t border-subtle px-4 py-2 text-xs text-muted">
        <span className="flex items-center gap-1">
          <Key>j</Key>
          <Key>k</Key> move
        </span>
        <span className="flex items-center gap-1">
          <Key>/</Key> filter
        </span>
        <span className="flex items-center gap-1">
          <Key>a</Key> approve
        </span>
        <span className="ml-auto tabular-nums">
          {index >= 0 ? `${index + 1} of ${filtered.length}` : '—'}
        </span>
      </footer>
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
        'inline-flex items-center gap-2 rounded-sm px-2 py-1 text-xs transition-colors',
        active ? 'bg-active text-primary' : 'text-muted hover:bg-hover',
      )}
    >
      {state ? (
        <span className={STATES[state].ink}>
          <StateDot form={STATES[state].form} />
        </span>
      ) : null}
      <span>{label}</span>
      {typeof count === 'number' ? <span className="font-mono tabular-nums">{count}</span> : null}
    </button>
  );
}
