'use client';

import { useState } from 'react';

import { AgentMark, CoverageBar, HeldCallout, VerdictBar } from '@/components/thread/blocks';
import { Mono, cx } from '@/components/ui/primitives';
import { absolute } from '@/lib/format';
import type { ThreadDetail, ThreadPost as Post, ThreadAction } from '@/lib/types/thread';

/**
 * One participant's post: a line you can read in a second, and everything behind it one
 * triangle away.
 *
 * ## The whole design argument, in one component
 *
 * The surface this replaces put 312 question-and-answer rows on a page because it had no
 * way to be brief without also being unsupported. That is a false choice, and this is the
 * shape that dissolves it: collapsed, twelve posts summarise a twelve-minute run and are
 * scannable end to end; expanded, every figure in every summary is traceable to the
 * retrieval, the passages, the scores and the audit rows it was counted from.
 *
 * Nothing is summarised *away*. A block that shows eight of forty-three says so in its own
 * note, because a truncation nobody mentions reads as a complete list.
 *
 * ## Why the whole post is one disclosure rather than one per block
 *
 * Per-block triangles put four to six controls on every collapsed post, which is six times
 * twelve controls on a page whose premise is that it can be read in ten seconds. One
 * control per post, and expanding shows what happened — which is how the question is
 * actually asked ("what did the verifier do?"), not block by block.
 *
 * ## Only the human's turns get a container
 *
 * Everything the fleet says is prose on the page. A bubble around an agent's turn makes it
 * look like a chat participant of the same kind as the person, and there are ten of them
 * to one of you — the page would be almost entirely containers, which is a shape that reads
 * as a transcript rather than as a record. The person's own turns are boxed, so the eye can
 * find where they intervened by scanning for the only thing that is boxed.
 */

export function ThreadPost({
  post,
  onAction,
  onQuestion,
  defaultOpen = false,
}: {
  post: Post;
  /** Inline controls. Approvals happen here, not on another page. */
  onAction?: (action: ThreadAction) => void;
  /** A row that names a question jumps to it. */
  onQuestion?: (questionId: string) => void;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const expandable = post.details.length > 0;

  // A person's own turn. Boxed, right-aligned label, no spine: it is not a step the fleet
  // took, it is the thing that interrupted them.
  if (post.kind === 'asked') {
    return (
      <article className="flex flex-col items-end gap-1 pb-6">
        <div className="max-w-[85%] rounded border border-line bg-surface px-4 py-3">
          <p className="whitespace-pre-wrap text-base text-primary">{post.summary}</p>
        </div>
        <span className="flex items-baseline gap-2 text-xs text-muted">
          {post.actor}
          <span title={absolute(post.at)}>
            <Mono dim>{clock(post.at)}</Mono>
          </span>
        </span>
      </article>
    );
  }

  return (
    <article className="grid grid-cols-[16px_minmax(0,1fr)] gap-4">
      {/* The spine. A rule plus a mark per post, so the eye reads a sequence rather than a
          stack of cards — and it costs one pixel of width rather than a border per post. */}
      <div className="relative flex justify-center" aria-hidden>
        <span className="absolute inset-y-0 w-px bg-subtle" />
        <AgentMark actor={post.actor} working={post.working} />
      </div>

      <div className="min-w-0 pb-6">
        <header className="flex items-baseline gap-3">
          <h3 className="truncate text-sm font-medium text-primary">{post.actor}</h3>
          {post.working ? (
            <span className="shrink-0 text-xs text-accent-text">working</span>
          ) : null}
          <span className="ml-auto shrink-0" title={absolute(post.at)}>
            <Mono dim>{clock(post.at)}</Mono>
          </span>
        </header>

        <HeldCallout count={post.kind === 'assembly' ? held(post) : 0}>
        <button
          type="button"
          onClick={() => expandable && setOpen((value) => !value)}
          aria-expanded={expandable ? open : undefined}
          disabled={!expandable}
          className={cx(
            'group mt-1 flex w-full items-start gap-2 rounded-sm text-left transition-colors',
            expandable ? 'hover:bg-hover' : 'cursor-default',
          )}
        >
          <span
            aria-hidden
            className={cx(
              'mt-1 shrink-0 font-mono text-xs leading-none transition-transform',
              expandable ? 'text-muted' : 'text-transparent',
              open ? 'rotate-90' : '',
            )}
          >
            ▸
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-base leading-relaxed text-primary">{post.summary}</span>
            {post.lines.map((line) => (
              <span key={line} className="mt-1 block text-sm leading-relaxed text-muted">
                {line}
              </span>
            ))}
          </span>
        </button>
        </HeldCallout>

        {post.progress.length > 0 ? <Counters post={post} /> : null}
        {post.kind === 'drafting' ? <CoverageBar post={post} /> : null}
        {post.kind === 'verification' ? <VerdictBar summary={post.summary} /> : null}

        {open ? (
          <div className="mt-3 flex flex-col gap-4 border-l border-subtle pl-4">
            {post.details.map((detail) => (
              <Block key={detail.heading} detail={detail} onQuestion={onQuestion} />
            ))}
            {post.events > 1 ? (
              <p className="text-xs text-muted">
                <Mono dim>{post.events}</Mono> audit events, folded into this post
                {post.through ? (
                  <>
                    {' '}
                    · through <Mono dim>{clock(post.through)}</Mono>
                  </>
                ) : null}
                .
              </p>
            ) : null}
          </div>
        ) : null}

        {post.actions.length > 0 && onAction ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {post.actions.map((action) => (
              <button
                key={action.kind}
                type="button"
                onClick={() => onAction(action)}
                className={cx(
                  'inline-flex h-row-dense items-center gap-2 rounded-sm border px-2 text-xs',
                  'transition-colors',
                  action.kind === 'approve'
                    ? 'border-line text-primary hover:bg-hover'
                    : 'border-subtle text-secondary hover:bg-hover hover:text-primary',
                )}
              >
                {action.label}
                {action.count > 0 ? (
                  <span className="font-mono tabular-nums text-muted">{action.count}</span>
                ) : null}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}

/**
 * The live counters: `82 · 45 · 38 answered`, rising while the fleet drafts.
 *
 * Rendered collapsed, not behind the disclosure, because a number moving is the one thing
 * on this surface a person should not have to open anything to see.
 */
function Counters({ post }: { post: Post }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-4">
      {post.progress.map((bar) => {
        const fraction = bar.total > 0 ? Math.min(1, bar.done / bar.total) : 0;
        return (
          <div key={bar.label} className="flex min-w-0 items-center gap-2">
            <Mono>{bar.done}</Mono>
            <span className="text-xs text-muted">of {bar.total}</span>
            <span
              role="img"
              aria-label={`${bar.done} of ${bar.total} ${bar.label}`}
              className="block h-1 w-16 overflow-hidden rounded-sm bg-track"
            >
              <span
                className="block h-full bg-scale transition-[width] duration-state"
                style={{ width: `${(fraction * 100).toFixed(1)}%` }}
              />
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** One expanded block: a heading, its rows, and whatever the rows cannot say themselves. */
function Block({
  detail,
  onQuestion,
}: {
  detail: ThreadDetail;
  onQuestion?: (questionId: string) => void;
}) {
  return (
    <section>
      <h4 className="text-xs text-muted">{detail.heading}</h4>
      {detail.rows.length > 0 ? (
        <dl className="mt-2 flex flex-col gap-1">
          {detail.rows.map((row, index) => {
            const jump = row.question_id && onQuestion;
            const body = (
              <>
                <dt className="w-list max-w-list shrink-0 truncate text-xs text-secondary">
                  {row.label}
                </dt>
                <dd
                  className={cx(
                    'min-w-0 flex-1 text-sm',
                    row.mono ? 'break-all font-mono text-xs text-secondary' : 'text-primary',
                  )}
                >
                  {row.value}
                </dd>
              </>
            );
            return (
              <div key={`${row.label}-${index}`} className="flex items-baseline gap-4">
                {jump ? (
                  <button
                    type="button"
                    onClick={() => onQuestion(row.question_id as string)}
                    className="flex w-full items-baseline gap-4 rounded-sm text-left hover:bg-hover"
                    title="Open this question in the grid"
                  >
                    {body}
                  </button>
                ) : (
                  body
                )}
              </div>
            );
          })}
        </dl>
      ) : null}
      {detail.note ? (
        <p className="mt-2 max-w-prose text-xs text-muted">{detail.note}</p>
      ) : null}
    </section>
  );
}

/** How many answers this post says are waiting, or zero. Drives the callout, nothing else. */
function held(post: Post): number {
  const action = post.actions.find((candidate) => candidate.kind === 'approve_all');
  return action === undefined ? 0 : action.count;
}

/**
 * `09:14`, or `17 Aug 09:14` when the post is not from today.
 *
 * A thread read three weeks later must not label everything with a bare time of day —
 * three posts reading `14:48` on three different days is the kind of small dishonesty that
 * makes a history view useless.
 */
function clock(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '—';
  const today = new Date();
  const sameDay =
    when.getFullYear() === today.getFullYear() &&
    when.getMonth() === today.getMonth() &&
    when.getDate() === today.getDate();
  const time = when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return time;
  const date = when.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  return `${date} ${time}`;
}
