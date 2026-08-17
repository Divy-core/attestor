import type { Config } from 'tailwindcss';

/**
 * Tailwind is configured to expose ONLY the semantic tokens.
 *
 * The default palette is deleted rather than extended. That is the enforcement mechanism
 * for "zero hardcoded colour strings": `bg-red-500` and `text-gray-400` do not exist as
 * utilities, so a component cannot reach for one by habit — it fails at build time instead
 * of shipping an off-palette colour that only shows up in the recording.
 *
 * `scripts/check-tokens.mjs` covers what Tailwind cannot: hex literals in arbitrary values
 * and inline styles.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  darkMode: ['variant', [
    '@media (prefers-color-scheme: dark) { &:not(:where([data-theme="light"] *)) }',
    '&:where([data-theme="dark"] *)',
  ]],
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
    extend: {
      fontFamily: {
        sans: 'var(--font-ui)',
        mono: 'var(--font-mono)',
      },
      fontSize: {
        // No `text-[10px]` anywhere: 13px is the dense floor for 1080p and there is no
        // utility below it to reach for.
        xs: ['var(--text-xs)', { lineHeight: '1.35' }],
        sm: ['var(--text-sm)', { lineHeight: '1.45' }],
        base: ['var(--text-base)', { lineHeight: '1.5' }],
        md: ['var(--text-md)', { lineHeight: '1.5' }],
        lg: ['var(--text-lg)', { lineHeight: '1.35' }],
        xl: ['var(--text-xl)', { lineHeight: '1.25' }],
      },
      fontWeight: {
        // Two weights. A compliance console does not need six.
        normal: '400',
        medium: '530',
      },
      borderRadius: {
        none: '0',
        sm: 'var(--radius-sm)',
        DEFAULT: 'var(--radius-md)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        full: '999px',
      },
      // No `boxShadow` scale at all. Depth is hairlines and background steps; a drop shadow
      // over a dark surface reads as blur under video compression.
      boxShadow: {
        none: 'none',
      },
      spacing: {
        row: 'var(--row-height)',
        'row-dense': 'var(--row-height-dense)',
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
