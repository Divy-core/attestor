/**
 * A review's five views, named once.
 *
 * ## Why this is its own module and not a couple of exports on `ReviewSurface`
 *
 * `ReviewSurface` declares `'use client'`, and everything exported from a client module is
 * a client reference — including a three-line pure function. The server component that
 * reads `?tab=` from the URL called `isTab` and got *"Attempted to call isTab() from the
 * server but isTab is on the client"*, at request time, with `tsc --noEmit` clean
 * throughout. That is the same trap `components/ui/primitives.tsx` documents from Phase
 * 6.5, found again the first time this page rendered.
 *
 * So the vocabulary lives here, where both halves may read it, and the component that
 * renders it stays on the client.
 */

export const TABS = ['thread', 'questions', 'evidence', 'artifacts', 'audit'] as const;

export type Tab = (typeof TABS)[number];

export function isTab(value: string | null | undefined): value is Tab {
  return typeof value === 'string' && (TABS as readonly string[]).includes(value);
}
