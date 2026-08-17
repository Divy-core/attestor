'use client';

import { useEffect, useRef, type ReactNode } from 'react';

/**
 * A modal. Deliberately not `<dialog>`: its backdrop is styled through `::backdrop`, which
 * cannot read the theme tokens defined on `:root` in every browser this has to survive, and the
 * demo is recorded once.
 *
 * Escape closes, the backdrop closes, focus moves in on open and returns on close. Depth is a
 * background step and a hairline, as everywhere else — no shadow.
 */
export function Modal({
  title,
  description,
  onClose,
  children,
  footer,
}: {
  title: string;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    // The panel itself, not the first field: the field claims focus with `autoFocus`, and
    // focusing both fights over the caret.
    panel.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-6">
      {/* `color-mix` rather than an opacity utility: the scrim has to darken in both themes,
          and a fixed black at 50% turns the light theme into a different product. */}
      <div
        aria-hidden
        onClick={onClose}
        className="fixed inset-0"
        style={{ background: 'color-mix(in oklab, var(--bg-base) 78%, transparent)' }}
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="relative mt-16 w-full max-w-xl rounded border border-strong bg-surface outline-none"
      >
        <header className="flex flex-col gap-1 border-b border-subtle px-4 py-3">
          <h2 className="text-md font-medium text-primary">{title}</h2>
          {description ? (
            <p className="max-w-prose text-sm text-secondary">{description}</p>
          ) : null}
        </header>
        <div className="flex flex-col gap-4 px-4 py-4">{children}</div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-subtle px-4 py-3">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}
