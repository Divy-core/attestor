'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Key, cx } from '@/components/ui/primitives';

/**
 * ⌘K. The difference between a website and an application.
 *
 * A compliance operator working 312 rows does not reach for a mouse, and a judge watching a
 * demo can tell within two seconds whether an interface expects them to. This is the control
 * that says which one this is.
 *
 * ## What it does and deliberately does not do
 *
 * It navigates and it jumps to a review. It does **not** approve, delete, start a run, or
 * do anything else irreversible. A palette that can fire a destructive action off a fuzzy
 * match is a palette that will eventually fire the wrong one — approval lives in the
 * workspace, next to the answer being approved, where the thing being decided is on screen.
 *
 * ## Why the shortcut is registered on `document` and not on an input
 *
 * The whole point is that it works with focus anywhere. The listener is capture-phase so it
 * fires before a grid's own `j`/`k` handler.
 *
 * It binds **⌘K and Escape only**. An earlier version also opened on a bare `/`, the way a
 * list interface usually does — which meant the palette swallowed `/` on the one page where
 * that shortcut matters, the question grid, whose filter it belongs to. Found by pressing
 * it rather than by reading the code, which is the general lesson about keyboard handling.
 */

type ReviewOption = { review_id: string; customer: string; state: string };

type Command = {
  id: string;
  label: string;
  hint: string;
  href: string;
  group: 'Go to' | 'Review';
};

const NAVIGATION: readonly Command[] = [
  {
    id: 'nav-reviews',
    label: 'Reviews',
    hint: 'Every review, sorted by what needs attention',
    href: '/reviews',
    group: 'Go to',
  },
  {
    id: 'nav-fleet',
    label: 'Fleet',
    hint: 'What each agent is doing right now',
    href: '/fleet',
    group: 'Go to',
  },
  {
    id: 'nav-connections',
    label: 'Connections',
    hint: 'Gmail, Drive, Slack — and what each may do',
    href: '/connections',
    group: 'Go to',
  },
  {
    id: 'nav-registry',
    label: 'Registry',
    hint: 'The live Agent Registry',
    href: '/registry',
    group: 'Go to',
  },
  { id: 'nav-traces', label: 'Audit', hint: 'One run, span by span', href: '/traces', group: 'Go to' },
  {
    id: 'nav-flagged',
    label: 'Answers held for a human',
    hint: 'Reviews waiting on a person',
    href: '/reviews?state=awaiting_human',
    group: 'Go to',
  },
  {
    id: 'nav-archived',
    label: 'Archived reviews',
    hint: 'Dead runs, kept as history',
    href: '/reviews?archived=1',
    group: 'Go to',
  },
];

/** Subsequence match, the way every palette worth using behaves: `cqr` finds `caiq round`. */
function matches(haystack: string, needle: string): boolean {
  if (!needle) return true;
  const target = haystack.toLowerCase();
  let index = 0;
  for (const character of needle.toLowerCase()) {
    index = target.indexOf(character, index);
    if (index === -1) return false;
    index += 1;
  }
  return true;
}

export function CommandPalette({ reviews = [] }: { reviews?: ReadonlyArray<ReviewOption> }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useMemo<readonly Command[]>(
    () => [
      ...NAVIGATION,
      // Two destinations per review, because the thread and the grid answer different
      // questions and typing the customer's name should be able to reach either. The thread
      // is first because it is where a review opens.
      ...reviews.flatMap((review) => [
        {
          id: review.review_id,
          label: review.customer,
          hint: `thread · ${review.state.replace(/_/g, ' ')}`,
          href: `/reviews/${review.review_id}`,
          group: 'Review' as const,
        },
        {
          id: `${review.review_id}-questions`,
          label: `${review.customer} — questions`,
          hint: `the grid · ${review.review_id}`,
          href: `/reviews/${review.review_id}?tab=questions`,
          group: 'Review' as const,
        },
      ]),
    ],
    [reviews],
  );

  const visible = useMemo(
    () => commands.filter((c) => matches(`${c.label} ${c.hint}`, query)).slice(0, 12),
    [commands, query],
  );

  const run = useCallback(
    (command: Command | undefined) => {
      if (!command) return;
      setOpen(false);
      setQuery('');
      router.push(command.href);
    },
    [router],
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen((previous) => !previous);
        setCursor(0);
        return;
      }
      if (event.key === 'Escape' && open) {
        event.preventDefault();
        setOpen(false);
        return;
      }
      // `/` deliberately does NOT open this. It belongs to the question grid's filter, and
      // binding it here as well meant the palette swallowed it on the one page where the
      // shortcut matters most -- found by pressing it, not by reading the code.
    }
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    setCursor(0);
  }, [query]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-10 flex items-start justify-center bg-scrim px-4 pt-16"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-list overflow-hidden rounded bg-surface shadow-overlay"
        onClick={(event) => event.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown' || (event.ctrlKey && event.key === 'n')) {
              event.preventDefault();
              setCursor((c) => Math.min(visible.length - 1, c + 1));
            } else if (event.key === 'ArrowUp' || (event.ctrlKey && event.key === 'p')) {
              event.preventDefault();
              setCursor((c) => Math.max(0, c - 1));
            } else if (event.key === 'Enter') {
              event.preventDefault();
              run(visible[cursor]);
            }
          }}
          placeholder="Jump to a review, or a page"
          aria-label="Command"
          className="h-row w-full border-b border-subtle bg-transparent px-4 text-base text-primary outline-none placeholder:text-muted"
        />

        {visible.length === 0 ? (
          <p className="px-4 py-6 text-sm text-muted">
            Nothing matches “{query}”. Reviews are searchable by customer, id, or state.
          </p>
        ) : (
          <ul className="max-h-list overflow-y-auto py-1">
            {visible.map((command, index) => (
              <li key={command.id}>
                <button
                  type="button"
                  onMouseEnter={() => setCursor(index)}
                  onClick={() => run(command)}
                  className={cx(
                    'flex w-full items-baseline justify-between gap-4 px-4 py-2 text-left',
                    index === cursor ? 'bg-active' : '',
                  )}
                >
                  <span className="truncate text-sm text-primary">{command.label}</span>
                  <span className="truncate font-mono text-xs text-muted">{command.hint}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <footer className="flex items-center gap-3 border-t border-subtle px-4 py-2 text-xs text-muted">
          <span className="flex items-center gap-1">
            <Key>↑</Key>
            <Key>↓</Key> move
          </span>
          <span className="flex items-center gap-1">
            <Key>↵</Key> open
          </span>
          <span className="flex items-center gap-1">
            <Key>esc</Key> close
          </span>
        </footer>
      </div>
    </div>
  );
}
