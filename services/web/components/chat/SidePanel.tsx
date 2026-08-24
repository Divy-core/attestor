'use client';

import { cx } from '@/components/ui/primitives';

/**
 * The panel beside the thread, present only when there is something in it.
 *
 * The thread is the reasoning trail; this is the product of the session — the report, the
 * grid, the evidence, the pack. Keeping them apart is what lets the centre column stay at
 * a readable measure while a 312-row grid is still one click away.
 *
 * Absent, not hidden: when nothing is open the element is not rendered, so the column
 * re-centres in the full width rather than sitting off to one side beside a blank slab.
 * Under 1280px it covers the column instead of sitting next to it, because 768 + 520 does
 * not fit and squeezing the column is the wrong thing to give up.
 */

export type PanelKind = 'report' | 'questions' | 'evidence' | 'artifacts' | 'audit';

export const PANELS: ReadonlyArray<{ kind: PanelKind; label: string }> = [
  { kind: 'report', label: 'Report' },
  { kind: 'questions', label: 'Questions' },
  { kind: 'evidence', label: 'Evidence' },
  { kind: 'artifacts', label: 'Artifacts' },
  { kind: 'audit', label: 'Audit' },
];

export function SidePanel({
  open,
  onOpen,
  onClose,
  title,
  meta,
  children,
}: {
  open: PanelKind;
  onOpen: (kind: PanelKind) => void;
  onClose: () => void;
  title: string;
  meta?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <aside
      aria-label={title}
      className={cx(
        'flex min-h-0 flex-col border-l border-subtle bg-surface',
        'absolute inset-y-0 right-0 z-10 w-full max-w-panel shadow-none',
        'lg:static lg:z-auto lg:w-panel lg:max-w-none',
      )}
    >
      <header className="flex h-header shrink-0 items-center gap-1 border-b border-subtle px-3">
        {PANELS.map((panel) => (
          <button
            key={panel.kind}
            type="button"
            onClick={() => onOpen(panel.kind)}
            aria-current={open === panel.kind ? 'true' : undefined}
            className={cx(
              'rounded-sm px-2 py-1 text-sm transition-colors',
              open === panel.kind
                ? 'bg-active font-medium text-primary'
                : 'text-muted hover:bg-hover hover:text-primary',
            )}
          >
            {panel.label}
          </button>
        ))}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the panel"
          className="ml-auto rounded-sm px-2 py-1 text-sm text-muted transition-colors hover:bg-hover hover:text-primary"
        >
          ✕
        </button>
      </header>
      {meta ? (
        <div className="shrink-0 border-b border-subtle px-4 py-2 text-xs text-muted">{meta}</div>
      ) : null}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
    </aside>
  );
}
