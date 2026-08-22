/**
 * The six semantic states, in one place, with their token, form and label.
 *
 * This is the module the palette decision in `PROGRESS.md` compiles down to. It exists so
 * that "what colour is quarantined" has exactly one answer and no component has to know it:
 * a component asks for the descriptor and renders what it is given.
 *
 * ## Why `form` is a field and not a style choice
 *
 * Three of the six carry their identity in shape rather than hue — a hollow ring for the
 * honest blank, a hatched fill for containment, a half-filled dot for partial capability.
 * That is what makes the set survive greyscale, and it is what makes it survive video
 * compression, which subsamples chroma before luma. If the only difference between "flagged"
 * and "denied" were hue, the design would have failed its own exit criterion.
 *
 * ## Why every state has a `meaning`
 *
 * A badge that says `flagged_no_evidence` is a database value shown to a person. The meaning
 * string is what a security engineer or counsel actually needs: not the enum, but what the
 * system did and why. These are rendered as the badge's title and in legends, in sentence
 * case, active voice.
 */

import type { AnswerStatus } from '@/lib/types/generated';

export type StateForm = 'solid' | 'ring' | 'hatched' | 'half';

export type StateKey =
  | 'cited'
  | 'flagged'
  | 'denied'
  | 'quarantined'
  | 'no-evidence'
  | 'degraded';

export type StateDescriptor = {
  key: StateKey;
  label: string;
  meaning: string;
  form: StateForm;
  /** Tailwind text colour utility. Resolves to a `--state-*` token. */
  ink: string;
  /** Tailwind background utility for the badge fill. Resolves to a `--fill-*` token. */
  fill: string;
};

export const STATES: Record<StateKey, StateDescriptor> = {
  cited: {
    key: 'cited',
    label: 'Cited',
    meaning: 'Answered, with at least one retrieved passage behind every claim.',
    form: 'solid',
    ink: 'text-cited',
    fill: 'bg-fill-cited',
  },
  flagged: {
    key: 'flagged',
    label: 'Needs a human',
    meaning: 'Drafted, but held for review. The only state with a queue behind it.',
    form: 'solid',
    ink: 'text-flagged',
    fill: 'bg-fill-flagged',
  },
  denied: {
    key: 'denied',
    label: 'Denied',
    meaning: 'The agent was refused this resource. The refusal is the system working.',
    form: 'solid',
    ink: 'text-denied',
    fill: 'bg-fill-denied',
  },
  quarantined: {
    key: 'quarantined',
    label: 'Quarantined',
    meaning: 'Model Armor blocked the content. It is isolated, not answered.',
    form: 'hatched',
    ink: 'text-quarantined',
    fill: 'bg-fill-quarantined',
  },
  'no-evidence': {
    key: 'no-evidence',
    label: 'No evidence',
    meaning: 'The corpus does not support an answer, and the system says so rather than guessing.',
    form: 'ring',
    ink: 'text-no-evidence',
    fill: 'bg-fill-no-evidence',
  },
  degraded: {
    key: 'degraded',
    label: 'Degraded',
    meaning: 'Answered on a fallback path. Real, but the machinery was not at full strength.',
    form: 'half',
    ink: 'text-degraded',
    fill: 'bg-fill-degraded',
  },
};

/**
 * Map a stored `AnswerStatus` onto a display state.
 *
 * The mapping is not one-to-one and the places it collapses are deliberate:
 *
 * `drafted` and `delivered` both become **cited** only when a citation exists. An answer with
 * no citations is never shown as cited whatever its status field says — provenance is the
 * product, and a green badge over an unsourced assertion is the exact failure this whole
 * system is built to prevent.
 *
 * `draft` is transitional and shows as degraded rather than inventing a seventh state: a row
 * still mid-flight is genuinely "not at full strength yet", and the half-filled dot reads
 * correctly for it.
 */
export function stateFor(status: AnswerStatus | null, citationCount: number): StateDescriptor {
  if (status === 'quarantined') return STATES.quarantined;
  if (status === 'rejected') return STATES.denied;
  if (status === 'flagged_no_evidence') return STATES['no-evidence'];
  if (status === 'needs_human') return STATES.flagged;
  if (status === null || status === 'draft') return STATES.degraded;
  // approved / drafted / delivered
  if (citationCount > 0) return STATES.cited;
  // Answered, claims to be finished, and carries nothing to stand on. Not cited.
  return STATES['no-evidence'];
}

/** Every descriptor, in the order the legend reads. Lightest first, which is also the order
 *  they separate in greyscale. */
export const STATE_ORDER: readonly StateKey[] = [
  'cited',
  'flagged',
  'no-evidence',
  'degraded',
  'quarantined',
  'denied',
] as const;

/**
 * Review states, which are a different vocabulary and deliberately not colour-coded.
 *
 * A review being in `drafting` is not good or bad, it is a position in a sequence — so it
 * gets weight and a rule, not a hue. Six state colours already carry meaning; adding a
 * seventh through ninth for lifecycle positions would dilute all of them.
 */
export const TERMINAL_REVIEW_STATES = new Set(['delivered', 'failed']);
export const BLOCKED_REVIEW_STATES = new Set([
  'awaiting_human',
  'awaiting_evidence',
  // The state machine's own recoverable halt, which this set had never listed. It is the
  // most blocked a review can be without being terminal.
  'blocked',
]);

export function reviewStateTone(state: string): 'active' | 'blocked' | 'terminal' {
  if (TERMINAL_REVIEW_STATES.has(state)) return 'terminal';
  if (BLOCKED_REVIEW_STATES.has(state)) return 'blocked';
  return 'active';
}
