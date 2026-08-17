/**
 * Formatting rules, in one place because they are decisions rather than utilities.
 *
 * Every figure this system shows is either a magnitude a person will compare against another
 * magnitude, or an identifier they will read character by character. Those want opposite
 * treatments, and getting them backwards is what makes an interface feel careless.
 */

/** Relevance scores, always three decimals, always the same width.
 *
 * Cosine over `text-embedding-005` does not use the full 0..1 range -- unrelated text in the
 * same domain still scores ~0.6 -- so the interesting variation lives in the third decimal
 * and rounding to two would flatten the entire distribution the confidence thresholds are
 * calibrated against. */
export function score(value: number): string {
  return value.toFixed(3);
}

/** A duration a person will compare, not a timestamp. */
export function duration(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${String(rest).padStart(2, '0')}s`;
}

/**
 * How long ago, in the coarsest unit that is still true.
 *
 * The 22-day dormancy is the point of the resume beat, and "22 days ago" lands where
 * "2026-07-26T09:14:02Z" does not. Absolute time is still available in the `title`, because
 * for an audit trail the exact instant is the thing that matters and a relative label is only
 * a reading aid.
 */
export function ago(iso: string | null | undefined): string {
  if (!iso) return '--';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '--';
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  const days = Math.floor(seconds / 86400);
  return days === 1 ? 'yesterday' : `${days} days ago`;
}

/** UTC, always, and said so. A compliance timeline in the reader's local zone is a timeline
 *  that two readers disagree about. */
export function absolute(iso: string | null | undefined): string {
  if (!iso) return '--';
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '--';
  return `${when.toISOString().replace('T', ' ').replace(/\.\d+Z$/, '')} UTC`;
}

/**
 * Shorten a long identifier for a dense cell, keeping both ends.
 *
 * The ends are what distinguish two engine resource names; the middle is
 * `projects/.../locations/.../reasoningEngines/` on every one of them. Truncating from the
 * right -- the default everywhere -- removes precisely the part that identifies the thing.
 */
export function ident(value: string, keep = 10): string {
  if (value.length <= keep * 2 + 1) return value;
  return `${value.slice(0, keep)}…${value.slice(-keep)}`;
}

/** The trailing segment of a resource path: `reasoningEngines/6333637226001334272` -> the id. */
export function lastSegment(value: string): string {
  const parts = value.split('/').filter(Boolean);
  return parts[parts.length - 1] ?? value;
}

/** A GCS object's filename, for a citation the reader has to recognise at a glance. */
export function documentName(uri: string): string {
  return lastSegment(uri).replace(/\.(txt|md|pdf|docx?)$/i, '');
}

/** Percentages as integers. A citation rate of 86.67% is 87% on screen; the extra digits
 *  imply a precision that 30 questions do not support. */
export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** `snake_case` event kinds as sentence-case prose. The audit plane stores enum values; a
 *  person reading a timeline should not have to. */
export function humanKind(kind: string): string {
  const words = kind.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}
