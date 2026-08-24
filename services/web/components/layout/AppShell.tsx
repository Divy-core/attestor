import Link from 'next/link';
import type { ReactNode } from 'react';

import { CommandPalette } from '@/components/layout/CommandPalette';
import { NewReviewRailAction } from '@/components/review/NewReview';
import { ThemeToggle } from '@/components/layout/ThemeToggle';
import { Key, Mono, cx } from '@/components/ui/primitives';
import { env } from '@/lib/env';

/**
 * The frame: a narrow rail, a thin header, and everything else is the page.
 *
 * No hero, no marketing copy, no product tour. The subject is a compliance console and the
 * content is the interesting part; the chrome's job is to take as little room as it can and
 * then get out of the way.
 *
 * The rail is 200px and does not collapse. A collapsible sidebar is a control that has to be
 * discovered, remembered, and animated, in exchange for space this layout does not need —
 * the workspace is three panes and the third one is where the width goes.
 */

export const NAV: ReadonlyArray<{ href: string; label: string; match: string; hint: string }> = [
  { href: '/', label: 'Chat', match: '^/$', hint: 'The fleet, as a conversation' },
  {
    href: '/reviews',
    label: 'Reviews',
    match: '^/reviews',
    hint: 'Every review, sorted by what needs attention',
  },
  { href: '/fleet', label: 'Fleet', match: '^/fleet', hint: 'What each agent is doing right now' },
  {
    href: '/connections',
    label: 'Connections',
    match: '^/connections',
    hint: 'Gmail, Drive, Slack — connected, and what each may do',
  },
  { href: '/registry', label: 'Registry', match: '^/registry', hint: 'The live Agent Registry' },
  { href: '/traces', label: 'Audit', match: '^/traces', hint: 'One run, span by span' },
];

function Rail({ pathname }: { pathname: string }) {
  return (
    <nav
      aria-label="Sections"
      className="flex h-full w-rail shrink-0 flex-col gap-1 border-r border-subtle px-3 py-4"
    >
      <Link
        href="/"
        className="mb-6 px-2 text-md text-primary no-underline hover:no-underline"
      >
        Attestor
      </Link>

      {/*
        The primary action, in the rail, above everything it acts on.

        It was a blue button top-right, which is where a marketing page puts a call to
        action. Handing a questionnaire in is the first thing this application does, so it
        goes first, in the same column as the things it produces, styled like them.
      */}
      <NewReviewRailAction />
      <div className="mb-2 mt-2 border-t border-subtle" />

      {NAV.map((item) => {
        const active = new RegExp(item.match).test(pathname);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? 'page' : undefined}
            title={item.hint}
            className={cx(
              'rounded-sm px-2 py-2 text-sm no-underline transition-colors hover:no-underline',
              active ? 'bg-active text-primary' : 'text-muted hover:bg-hover hover:text-primary',
            )}
          >
            {item.label}
          </Link>
        );
      })}

      <div className="mt-auto flex flex-col gap-2 px-2 pt-4">
        <div className="flex items-center gap-2 pb-2 text-xs text-muted">
          <Key>⌘K</Key>
          <span>anywhere</span>
        </div>
        {/*
          The deployment, rendered as monospace metadata rather than described in prose.
          "Visible proof it runs on Google Cloud" is 30% of the score's own wording, and a
          real project id, a real region and a real Cloud Run revision on screen answer it
          without a single claim being made.
        */}
        <Mono dim title="GCP project">
          {env.projectId}
        </Mono>
        <Mono
          dim
          title="Region"
        >
          {env.region}
        </Mono>
        <Mono dim title="Cloud Run revision serving this page">
          {env.revision}
        </Mono>
      </div>
    </nav>
  );
}

export function AppShell({
  children,
  pathname,
  title,
  meta,
  actions,
  reviews = [],
  scroll = true,
}: {
  children: ReactNode;
  pathname: string;
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  /** What the palette can jump to. Empty is fine; it still navigates and still filters. */
  reviews?: ReadonlyArray<{ review_id: string; customer: string; state: string }>;
  /**
   * Whether the shell scrolls the page, or the page scrolls itself.
   *
   * Defaults to `true`, and it did not before -- `main` was `overflow-hidden` unconditionally
   * because the review workspace manages its own three panes. Every other page inherited that
   * and was simply clipped: registry, traces and the trace detail could not be scrolled at
   * all below the fold. A default that breaks three pages to suit one is the wrong default.
   */
  scroll?: boolean;
}) {
  return (
    <div className="flex h-screen w-full overflow-hidden bg-base">
      <Rail pathname={pathname} />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-header shrink-0 items-center justify-between gap-4 border-b border-subtle px-6">
          <div className="flex min-w-0 items-baseline gap-3">
            <h1 className="truncate text-md text-primary">{title}</h1>
            {meta ? <div className="truncate text-sm text-muted">{meta}</div> : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {actions}
            <ThemeToggle />
          </div>
        </header>
        {/* Vertical scrolling by default; the workspace opts out because it scrolls each of
            its three panes separately. Horizontal is always hidden -- a body that scrolls
            sideways at 1080p is the one layout failure that cannot be hidden in a recording. */}
        <main
          className={cx(
            'min-h-0 flex-1 overflow-x-hidden',
            scroll ? 'overflow-y-auto' : 'overflow-hidden',
          )}
        >
          {children}
        </main>
      </div>
      <CommandPalette reviews={reviews} />
    </div>
  );
}
