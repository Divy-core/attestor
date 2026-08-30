'use client';

import { Mono, cx } from '@/components/ui/primitives';
import type { ThreadPost as Post } from '@/lib/types/thread';

/**
 * The thread's visual vocabulary: one mark per agent, one figure per kind of post.
 *
 * ## Why an agent needs a mark and not just a name
 *
 * Ten agents post into one column. Read as a list of names in identical type, the eye has
 * to parse a word to know who is speaking, on every post, and the sequence reads as one
 * voice with a changing byline. A mark is recognised before it is read, so a person
 * scanning the thread sees *shape changing* — which is what a fleet looks like from
 * outside, and what a pipeline does not.
 *
 * The marks are glyphs in the existing type, not images: nothing to load, legible at any
 * size, and they inherit the theme with everything else.
 *
 * ## Why the figures are drawn rather than tabulated
 *
 * A verdict distribution is four numbers that only mean something against each other —
 * `18 supported, 17 partially, 1 unsupported, 9 unchecked` is a sentence you have to do
 * arithmetic on, and the same four numbers as a bar is a shape you read in one glance. The
 * numbers stay beside it, because a bar nobody can put a number to is decoration.
 *
 * Everything here is a token colour. Nothing hardcodes a hue, so both themes are the same
 * component and `check-tokens` keeps it that way.
 */

/** Who is speaking, as a mark the eye can catch before it reads the name. */
const MARKS: Record<string, { glyph: string; tone: string }> = {
  Orchestrator: { glyph: '◇', tone: 'text-accent-text' },
  TriageAgent: { glyph: '⋔', tone: 'text-secondary' },
  InboxAgent: { glyph: '✉', tone: 'text-accent-text' },
  SecurityAgent: { glyph: '▲', tone: 'text-secondary' },
  LegalAgent: { glyph: '■', tone: 'text-secondary' },
  EngineeringAgent: { glyph: '●', tone: 'text-secondary' },
  EvidenceAgent: { glyph: '❋', tone: 'text-secondary' },
  VerifierAgent: { glyph: '✓', tone: 'text-primary' },
  AssemblerAgent: { glyph: '▤', tone: 'text-primary' },
  ArmorGuard: { glyph: '⊘', tone: 'text-denied' },
  Dispatcher: { glyph: '⇢', tone: 'text-muted' },
};

export function AgentMark({ actor, working }: { actor: string; working: boolean }) {
  const mark = MARKS[actor];
  if (mark === undefined) {
    return (
      <span
        aria-hidden
        className={cx(
          'relative mt-2 h-2 w-2 rounded-sm',
          working ? 'animate-pulse bg-accent' : 'bg-strong',
        )}
      />
    );
  }
  return (
    <span
      aria-hidden
      className={cx(
        'relative mt-[2px] select-none text-[13px] leading-none',
        working ? 'animate-pulse text-accent-text' : mark.tone,
      )}
    >
      {mark.glyph}
    </span>
  );
}

type Segment = { label: string; count: number; className: string };

/**
 * A stacked proportion bar. Segments with a zero count are dropped rather than drawn at
 * zero width, because a legend entry with no bar beside it reads as a rendering fault.
 */
function Bar({ segments, total }: { segments: Segment[]; total: number }) {
  const shown = segments.filter((segment) => segment.count > 0);
  if (total <= 0 || shown.length === 0) return null;
  return (
    <div className="mt-2 flex flex-col gap-2">
      <span
        role="img"
        aria-label={shown.map((s) => `${s.count} ${s.label}`).join(', ')}
        className="flex h-1.5 w-full overflow-hidden rounded-sm bg-track"
      >
        {shown.map((segment) => (
          <span
            key={segment.label}
            className={cx('block h-full transition-[width] duration-state', segment.className)}
            style={{ width: `${((segment.count / total) * 100).toFixed(2)}%` }}
          />
        ))}
      </span>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        {shown.map((segment) => (
          <span key={segment.label} className="flex items-baseline gap-1.5 text-xs">
            <span className={cx('h-2 w-2 shrink-0 rounded-sm', segment.className)} aria-hidden />
            <Mono>{segment.count}</Mono>
            <span className="text-muted">{segment.label}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * The verifier's distribution, read off its own summary line.
 *
 * The projection composes that line from the audit trail; parsing it back is cheaper and
 * safer than adding a parallel numeric field that could disagree with the sentence beside
 * it. If the sentence ever stops carrying numbers the bar disappears, which is the correct
 * failure: no bar rather than a bar of zeros.
 */
export function VerdictBar({ summary }: { summary: string }) {
  const read = (word: string): number => {
    const found = summary.match(new RegExp(`(\\d+)\\s+${word}`, 'i'));
    return found === null ? 0 : Number(found[1]);
  };
  const supported = read('supported');
  const partially = read('partially');
  const unsupported = read('unsupported');
  const unchecked = read('could not be checked') || read('unchecked');
  const total = supported + partially + unsupported + unchecked;
  return (
    <Bar
      total={total}
      segments={[
        { label: 'supported', count: supported, className: 'bg-fill-cited' },
        { label: 'partially', count: partially, className: 'bg-fill-degraded' },
        { label: 'unsupported', count: unsupported, className: 'bg-fill-denied' },
        { label: 'not checked', count: unchecked, className: 'bg-fill-no-evidence' },
      ]}
    />
  );
}

/**
 * Coverage for one drafting partition: how many of its answers carry a citation.
 *
 * Counted from the post's own progress bars and its flagged line, so it cannot disagree
 * with the counters printed next to it.
 */
export function CoverageBar({ post }: { post: Post }) {
  const bar = post.progress[0];
  if (bar === undefined || bar.total <= 0) return null;
  const flaggedLine = post.lines.find((line) => line.includes('no citation'));
  const flagged = flaggedLine ? Number(flaggedLine.match(/^(\d+)/)?.[1] ?? 0) : 0;
  const cited = Math.max(0, bar.done - flagged);
  return (
    <Bar
      total={bar.done}
      segments={[
        { label: 'cited', count: cited, className: 'bg-fill-cited' },
        { label: 'no evidence, flagged', count: flagged, className: 'bg-fill-flagged' },
      ]}
    />
  );
}

/**
 * The escalation, given the weight of the thing that is blocking the round.
 *
 * Every other post on this surface is prose on a spine. This one is the only place where
 * the fleet is stopped and waiting, and a reader who scrolls past it has missed the state
 * the whole review is in — so it gets a rule and a ground, which nothing else has.
 */
export function HeldCallout({ count, children }: { count: number; children: React.ReactNode }) {
  if (count <= 0) return <>{children}</>;
  return (
    <div className="mt-1 rounded-sm border border-line bg-surface px-3 py-2.5">{children}</div>
  );
}
