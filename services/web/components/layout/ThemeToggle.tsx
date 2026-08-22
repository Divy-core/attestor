'use client';

import { useEffect, useState } from 'react';

import { cx } from '@/components/ui/primitives';

type Choice = 'system' | 'light' | 'dark';

const KEY = 'attestor-theme';

/**
 * Three states, not two.
 *
 * "System" is a real choice and the default one, and collapsing it into a boolean is how a
 * toggle ends up fighting the operating system: a user on auto dark mode flips to light at
 * sunrise and the app stays dark because it once wrote `dark` to storage.
 *
 * The mechanism is entirely in `styles/tokens.css`. "System" stamps no attribute at all and
 * lets `prefers-color-scheme` decide; an explicit choice stamps `data-theme`, which the token
 * file honours in both directions. This component writes one attribute and nothing else.
 */
export function ThemeToggle() {
  const [choice, setChoice] = useState<Choice>('system');
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stored = window.localStorage.getItem(KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') setChoice(stored);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    const root = document.documentElement;

    // Transitions off across the change, and back on once the new colours are committed.
    //
    // Measured on the deployed site: with them on, 21 of the 35 elements carrying a colour
    // transition kept painting the dark palette after switching to light -- the rail, the
    // New review action, every thread summary, every tab, dark-theme ink on the light page
    // at a contrast of 1.06. A custom property changing under a running transition does not
    // reliably invalidate what derives from it, and the element holds the old colour.
    //
    // Three synchronous steps, and each forced reflow is load-bearing:
    //
    //   1. stamp, and flush -- so the suppression is in effect before anything changes.
    //      Without the flush the browser coalesces both attribute writes into one style
    //      recalculation and the suppression never applies.
    //   2. change the theme, and flush -- the new colours are computed and committed with
    //      no transition to hold the old ones.
    //   3. unstamp. There is nothing left to animate, because step 2 already landed.
    //
    // Deliberately not `requestAnimationFrame`. The first version used two nested frames to
    // unstamp and the attribute stuck on permanently in a tab that was not compositing --
    // rAF does not fire there, so transitions stayed off for the life of the page. A theme
    // change is instantaneous rather than an animation, so it needs no frame at all.
    root.setAttribute('data-theme-switching', '');
    void root.offsetHeight;

    if (choice === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', choice);
    void root.offsetHeight;

    root.removeAttribute('data-theme-switching');
    window.localStorage.setItem(KEY, choice);
  }, [choice, ready]);

  const options: ReadonlyArray<{ id: Choice; label: string }> = [
    { id: 'light', label: 'Light' },
    { id: 'system', label: 'Auto' },
    { id: 'dark', label: 'Dark' },
  ];

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="inline-flex items-center gap-1"
    >
      {options.map((option) => (
        <button
          key={option.id}
          role="radio"
          // Before hydration the stored choice is unknown. Reporting `system` as selected
          // would be a guess rendered as a fact; `aria-checked` stays honest until it is
          // actually known.
          aria-checked={ready ? choice === option.id : false}
          onClick={() => setChoice(option.id)}
          className={cx(
            'rounded-sm px-2 py-1 text-xs transition-colors',
            ready && choice === option.id
              ? 'bg-active text-primary'
              : 'text-muted hover:text-secondary',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Applies the stored theme before first paint.
 *
 * Without this the page renders in the system theme, hydrates, and then flips — a white flash
 * on a dark-mode machine. On a recorded demo that flash happens on every navigation, which is
 * exactly the kind of thing a viewer notices without being able to say why.
 *
 * Inline, blocking, and tiny, because it has to run before the first paint and there is no
 * other way to do that. `suppressHydrationWarning` on `<html>` covers the attribute this adds.
 */
export const THEME_BOOTSTRAP = `(function(){try{var c=localStorage.getItem('${KEY}');if(c==='dark'||c==='light'){document.documentElement.setAttribute('data-theme',c);}}catch(e){}})();`;
