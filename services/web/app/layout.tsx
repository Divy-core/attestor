import { GeistMono } from 'geist/font/mono';
import { GeistSans } from 'geist/font/sans';
import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { THEME_BOOTSTRAP } from '@/components/layout/ThemeToggle';

import './globals.css';

export const metadata: Metadata = {
  title: 'Attestor',
  description:
    'An enterprise agent fleet that answers vendor security reviews with evidence, memory, and a defensible audit trail.',
};

/**
 * `suppressHydrationWarning` on `<html>` is required and not a shortcut: the bootstrap script
 * below sets `data-theme` before React runs, so the server's markup and the client's first
 * render legitimately differ on exactly that attribute. Suppressing it here is narrower than
 * it looks — the warning is per-element, not per-tree.
 *
 * The two fonts are loaded through `next/font`, which self-hosts them and emits the CSS
 * variables `styles/tokens.css` reads. No request leaves the page for a font, so there is no
 * flash of fallback on a cold load — which on a recorded demo happens on the first frame,
 * where it is most visible.
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <head>
        {/* Before first paint. See ThemeToggle for why this cannot be a component. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
        {/* `color-scheme` makes the browser's own surfaces -- scrollbars, form controls, the
            overscroll gutter -- match the page. Without it a dark page has a white gutter
            when you scroll past the end, which shows up in a recording. */}
        <meta name="color-scheme" content="light dark" />
      </head>
      <body>{children}</body>
    </html>
  );
}
