'use client';

import { ConversationRail } from '@/components/chat/ConversationRail';
import { CommandPalette } from '@/components/layout/CommandPalette';
import type { ReviewCard } from '@/lib/api/client';

/**
 * The front door: conversations on the left, one column in the middle, a panel when there
 * is something to put in it.
 *
 * ## The column is capped at 768px and that is the whole layout
 *
 * The review page before this ran the full width of the window. At 1920px that is a measure
 * of roughly two hundred characters, and the eye loses its place on every return sweep,
 * which is the real reason the old page was hard to read rather than the density.
 * Everything that genuinely needs width — the 312-row grid above all — went into the panel.
 *
 * ## Chrome
 *
 * A rail, a hairline, and nothing else. No header bar over the column: the conversation is
 * named in the rail, and repeating it above the thread costs 56px of the one dimension that
 * matters here.
 *
 * Layout only. The views own their own state, because the composer and the panel are the
 * same two pieces on both of them and a shell that held either would have to hand it back
 * down through props it does not otherwise need.
 */

export function ChatShell({
  reviews,
  activeId,
  children,
  panel,
}: {
  reviews: ReviewCard[];
  activeId: string | null;
  /** The thread and its composer. Owns its own scrolling. */
  children: React.ReactNode;
  /** Rendered only when a panel is open. Absent, not hidden. */
  panel?: React.ReactNode;
}) {
  return (
    <div className="relative flex h-screen w-full overflow-hidden bg-base">
      <ConversationRail reviews={reviews} activeId={activeId} />
      <div className="flex min-w-0 flex-1">
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
        {panel}
      </div>
      <CommandPalette reviews={reviews} />
    </div>
  );
}
