import type { Config } from 'tailwindcss';

/**
 * Tailwind is configured to expose ONLY what the design system permits.
 *
 * Three scales are **replaced** rather than extended — colours, spacing, and radii — and
 * that replacement is the enforcement mechanism, not a stylistic preference:
 *
 *   - `bg-red-500` and `text-gray-400` do not exist, so a component cannot reach for an
 *     off-palette colour by habit. It fails at build time instead of shipping a hue that
 *     only shows up in the recording.
 *   - `p-5`, `gap-7`, and `mt-2.5` do not exist. The spacing scale is 4, 8, 12, 16, 24, 32,
 *     40, 48, 64 and nothing else, so "roughly aligned" is not reachable — two elements
 *     either share a step or visibly do not.
 *   - `rounded-xl` and `rounded-full` do not exist. Two radii, 4px and 6px. The 9999px pill
 *     is a marketing signature rather than an app control.
 *
 * `scripts/check-tokens.mjs` covers what Tailwind cannot: hex literals in arbitrary values,
 * inline styles, raw ramp references that bypass the semantic layer, and the two claims the
 * token file makes about its own greys.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  darkMode: [
    'variant',
    [
      '@media (prefers-color-scheme: dark) { &:not(:where([data-theme="light"] *)) }',
      '&:where([data-theme="dark"] *)',
    ],
  ],
  theme: {
    // `colors` replaces rather than extends. See above.
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      inherit: 'inherit',

      base: 'var(--bg-base)',
      surface: 'var(--bg-surface)',
      raised: 'var(--bg-raised)',
      sunken: 'var(--bg-sunken)',
      hover: 'var(--bg-hover)',
      active: 'var(--bg-active)',
      code: 'var(--bg-code)',

      primary: 'var(--text-primary)',
      secondary: 'var(--text-secondary)',
      muted: 'var(--text-muted)',
      inverse: 'var(--text-inverse)',

      subtle: 'var(--border-subtle)',
      line: 'var(--border-default)',
      strong: 'var(--border-strong)',
      focus: 'var(--border-focus)',

      // The one accent. Links, primary buttons, focus, active nav. Nothing else.
      accent: 'var(--accent)',
      'accent-hover': 'var(--accent-hover)',
      'accent-fill': 'var(--accent-fill)',
      'accent-ink': 'var(--accent-ink)',
      'accent-text': 'var(--accent-text)',

      scrim: 'var(--alpha-scrim)',

      cited: 'var(--state-cited)',
      flagged: 'var(--state-flagged)',
      denied: 'var(--state-denied)',
      quarantined: 'var(--state-quarantined)',
      'no-evidence': 'var(--state-no-evidence)',
      degraded: 'var(--state-degraded)',

      'fill-cited': 'var(--fill-cited)',
      'fill-flagged': 'var(--fill-flagged)',
      'fill-denied': 'var(--fill-denied)',
      'fill-quarantined': 'var(--fill-quarantined)',
      'fill-no-evidence': 'var(--fill-no-evidence)',
      'fill-degraded': 'var(--fill-degraded)',

      track: 'var(--scale-track)',
      scale: 'var(--scale-fill)',
    },

    // 4, 8, 12, 16, 24, 32, 40, 48, 64. The Tailwind names are kept (`p-4` is still 16px)
    // so the muscle memory is right; what is gone is everything between the steps.
    spacing: {
      0: '0px',
      px: '1px',
      1: '4px',
      2: '8px',
      3: '12px',
      4: '16px',
      6: '24px',
      8: '32px',
      10: '40px',
      12: '48px',
      16: '64px',
    },

    borderRadius: {
      none: '0px',
      sm: 'var(--radius-sm)',
      DEFAULT: 'var(--radius-md)',
      md: 'var(--radius-md)',
    },

    extend: {
      fontFamily: {
        sans: 'var(--font-ui)',
        mono: 'var(--font-mono)',
      },
      fontSize: {
        // No `text-[10px]` anywhere: 12px is the floor for 1080p and there is no utility
        // below it to reach for.
        xs: ['var(--text-xs)', { lineHeight: '1.35' }],
        sm: ['var(--text-sm)', { lineHeight: '1.45' }],
        base: ['var(--text-base)', { lineHeight: '1.5' }],
        md: ['var(--text-md)', { lineHeight: '1.4' }],
        lg: ['var(--text-lg)', { lineHeight: '1.2' }],
        xl: ['var(--text-xl)', { lineHeight: '1.1' }],
      },
      fontWeight: {
        // Three weights, none above 600. Above that a UI face stops reading as chrome.
        normal: '400',
        medium: '500',
        semibold: '600',
      },
      // No `boxShadow` scale beyond the border trick. Depth is hairlines and background
      // steps; a drop shadow over a dark surface reads as blur under video compression.
      // `shadow-line` is a 1px ring that does not participate in layout, which is what
      // lets a row keep its height whether or not it is selected.
      boxShadow: {
        none: 'none',
        line: '0 0 0 1px var(--border-subtle)',
        'line-strong': '0 0 0 1px var(--border-default)',
        focus: '0 0 0 2px var(--bg-base), 0 0 0 4px var(--border-focus)',
        overlay: '0 0 0 1px var(--border-default), 0 16px 48px var(--alpha-scrim)',
      },
      height: {
        row: 'var(--row-height)',
        'row-dense': 'var(--row-height-dense)',
        // The header. 56px is not on the spacing scale and should not be -- it is a chrome
        // dimension, chosen against the rail's type, not a gap between two things.
        header: '56px',
      },
      // Three layout widths, named for what they are. Not part of the spacing scale: the
      // rail is 200px because that is what four nav labels and a revision id need, and
      // rounding it to a spacing step would be a worse number chosen for a tidier reason.
      width: {
        // A square control on the row grid: the composer's attach button, whose height is
        // set by the row and whose width has to match it or the button is an oblong.
        row: 'var(--row-height)',
        rail: '200px',
        list: '420px',
        // The conversation rail on the chat surface. Wider than the nav rail because it
        // carries customer names and a line of status, and narrower than the list pane
        // because it is navigation, not content.
        conversations: '260px',
        // What the conversation rail collapses to under 1200px: a dot and nothing else.
        'conversations-collapsed': '56px',
        // The panel that slides in beside the thread.
        panel: '520px',
      },
      minWidth: {
        detail: '360px',
      },
      minHeight: {
        row: 'var(--row-height)',
      },
      maxWidth: {
        prose: '68ch',
        list: '520px',
        // What the side panel covers the column with below 1280px, where 768 + 520 does
        // not fit and squeezing the column is the wrong thing to give up.
        panel: '520px',
        // The chat column, and the single most important number in this layout.
        //
        // The review page before this was full width, and long lines are what made it
        // unreadable -- a 1920px monitor gave a 1600px measure, roughly 200 characters, so
        // the eye lost its place on every return sweep. 768px at this type size is a little
        // over 90 characters, which is wide enough for a table inside a disclosure and
        // narrow enough to read continuously.
        column: '768px',
        // A measure for full-width pages. Content that runs the whole width of a 1920px
        // monitor is unreadable, and a console that fills every pixel because it can is the
        // difference between dense and cluttered.
        page: '1280px',
      },
      maxHeight: {
        list: '360px',
      },
      transitionTimingFunction: {
        DEFAULT: 'var(--ease)',
      },
      transitionDuration: {
        DEFAULT: 'var(--motion-fast)',
        state: 'var(--motion-state)',
      },
    },
  },
  plugins: [],
};

export default config;
