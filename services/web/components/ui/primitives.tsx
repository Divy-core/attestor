import type { CSSProperties, ReactNode } from 'react';

import { STATES, type StateDescriptor, type StateForm } from '@/lib/states';

/**
 * The whole vocabulary. Everything the interface is made of is here or composed from here.
 *
 * ## Four rules, and they are what the rebuild is
 *
 * 1. **Hairlines do all the separation.** No card fills, no zebra striping, no boxes inside
 *    boxes. A panel is a panel because a 1px rule says where it stops, and that rule is a
 *    `box-shadow: 0 0 0 1px` rather than a `border` — a shadow does not participate in
 *    layout, so a row that gains a ring on selection does not change height and the list
 *    does not shift under the cursor.
 * 2. **Generous padding inside a cell, tight rhythm between rows.** Density comes from
 *    removing the space *between* things, not from cramping what is inside them.
 * 3. **Hierarchy from type, never from colour.** Size and weight carry the structure. The
 *    accent is for links, primary actions and focus; the six state hues are for status and
 *    nothing else. Delete the accent and this should still read correctly.
 * 4. **Every element earns its pixel.** There is no decorative component in this file.
 *
 * ## Server components by default
 *
 * Nothing here declares `'use client'`. That is load-bearing and was learned the hard way in
 * Phase 6.5: adding the directive to this module made every server component that imported
 * `cx` fail at runtime with "Attempted to call cx() from the server", and `tsc --noEmit` was
 * clean throughout. Anything needing state lives in its own client module.
 */

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

// ---------------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------------

/**
 * A value the system produced, not prose about it.
 *
 * Monospace is semantic here rather than stylistic: an engine resource name, a dedup key, a
 * relevance score, a revision. If it could have been typed by a person it is not `Mono`.
 */
export function Mono({
  children,
  dim,
  className,
  title,
}: {
  children: ReactNode;
  dim?: boolean;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        'font-mono text-xs tabular-nums',
        dim ? 'text-muted' : 'text-secondary',
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A column heading or a metadata label. Never a sentence. */
export function Label({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        'text-xs font-medium uppercase tracking-wide text-muted',
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * One live figure, big enough to read across a room.
 *
 * The only place 32px type appears. A counter moving during a run is the one thing on
 * screen a viewer should not have to look for.
 */
export function Figure({
  value,
  of,
  label,
  tone = 'default',
}: {
  value: number | string;
  of?: number | string;
  label: string;
  tone?: 'default' | 'muted';
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline gap-1">
        <span
          className={cx(
            'text-xl font-medium tabular-nums',
            tone === 'muted' ? 'text-muted' : 'text-primary',
          )}
        >
          {value}
        </span>
        {of !== undefined ? (
          <span className="text-sm tabular-nums text-muted">/ {of}</span>
        ) : null}
      </div>
      <Label>{label}</Label>
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Surfaces
// ---------------------------------------------------------------------------------

/**
 * A bounded region. One hairline, no fill of its own beyond the surface step.
 *
 * `flush` exists for a panel that owns a list: the list rows supply their own horizontal
 * padding and a panel that also padded them would double it.
 */
export function Panel({
  children,
  className,
  flush,
}: {
  children: ReactNode;
  className?: string;
  flush?: boolean;
}) {
  return (
    <section
      className={cx(
        'rounded bg-surface shadow-line',
        flush ? '' : 'p-4',
        className,
      )}
    >
      {children}
    </section>
  );
}

/** A panel's heading row. Separated by a rule, not by a filled bar. */
export function PanelHeader({
  title,
  meta,
  actions,
}: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-subtle px-4 py-3">
      <div className="flex min-w-0 items-baseline gap-3">
        <h2 className="truncate text-md font-semibold text-primary">{title}</h2>
        {meta ? <span className="truncate text-sm text-muted">{meta}</span> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

/**
 * What a region says when it is genuinely empty.
 *
 * `hint` is required rather than optional on purpose. An empty state that only says "no
 * results" tells a person nothing they did not already know; the useful half is what would
 * appear here and how to make it appear.
 */
export function Empty({
  title,
  hint,
  action,
}: {
  title: string;
  hint: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-2 px-4 py-8">
      <p className="text-base font-medium text-secondary">{title}</p>
      <p className="max-w-prose text-sm text-muted">{hint}</p>
      {action ? <div className="pt-2">{action}</div> : null}
    </div>
  );
}

/**
 * A read that failed, rendered as a failure.
 *
 * Distinct from `Empty` and that distinction is the point. "This review has no answers" and
 * "the read failed" are different facts, and the second one rendered as the first is the
 * mistake this codebase has made repeatedly in Python and intends never to make in the UI.
 */
export function Failure({
  what,
  detail,
  action,
}: {
  what: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-2 rounded bg-fill-denied px-4 py-3 shadow-line">
      <p className="text-base font-medium text-denied">{what}</p>
      <p className="max-w-prose font-mono text-xs text-secondary">{detail}</p>
      {action ? <div className="pt-1">{action}</div> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------------

type ButtonTone = 'primary' | 'secondary' | 'ghost' | 'danger';

const BUTTON_TONES: Record<ButtonTone, string> = {
  // The accent's only appearances: this, links, focus, and the active nav item.
  primary: 'bg-accent text-accent-ink hover:bg-accent-hover',
  secondary: 'bg-surface text-primary shadow-line-strong hover:bg-hover',
  ghost: 'bg-transparent text-secondary hover:bg-hover hover:text-primary',
  danger: 'bg-transparent text-denied shadow-line-strong hover:bg-fill-denied',
};

export function Button({
  children,
  tone = 'secondary',
  type = 'button',
  onClick,
  disabled,
  title,
  className,
  small,
}: {
  children: ReactNode;
  tone?: ButtonTone;
  type?: 'button' | 'submit';
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
  className?: string;
  small?: boolean;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cx(
        'inline-flex shrink-0 items-center justify-center gap-2 rounded-sm font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-40',
        small ? 'h-row-dense px-2 text-xs' : 'h-row px-3 text-sm',
        BUTTON_TONES[tone],
        className,
      )}
    >
      {children}
    </button>
  );
}

/** A keyboard shortcut, shown where the action is. */
export function Key({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-sm bg-sunken px-1 font-mono text-xs text-muted shadow-line">
      {children}
    </kbd>
  );
}

// ---------------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------------

/**
 * The state marker. Hue *and* form, so it survives greyscale and video compression.
 *
 * Three of the six carry their identity in shape rather than colour — a hollow ring for the
 * honest blank, a hatched fill for containment, a half-filled dot for partial capability —
 * and those are exactly the three whose luminances sit within a point of each other. If the
 * only difference between them were hue the design would fail its own exit criterion.
 */
export function StateDot({ form, className }: { form: StateForm; className?: string }) {
  const base = 'inline-block h-2 w-2 shrink-0 rounded-sm';
  if (form === 'ring') {
    return <span className={cx(base, 'bg-transparent shadow-line-strong', className)} />;
  }
  if (form === 'half') {
    return (
      <span
        className={cx(base, 'bg-current', className)}
        style={{ clipPath: 'polygon(0 0, 50% 0, 50% 100%, 0 100%)' }}
      />
    );
  }
  if (form === 'hatched') {
    return (
      <span
        className={cx(base, className)}
        style={{
          backgroundImage:
            'repeating-linear-gradient(45deg, currentColor 0 2px, transparent 2px 4px)',
        }}
      />
    );
  }
  return <span className={cx(base, 'bg-current', className)} />;
}

export function StateBadge({
  state,
  children,
  className,
}: {
  state: StateDescriptor;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <span
      title={state.meaning}
      className={cx(
        'inline-flex items-center gap-2 rounded-sm px-2 py-1 text-xs font-medium',
        state.fill,
        state.ink,
        className,
      )}
    >
      <StateDot form={state.form} />
      {children ?? state.label}
    </span>
  );
}

/** Every state, once, with what each means. */
export function StateLegend({ keys }: { keys: readonly (keyof typeof STATES)[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-2">
      {keys.map((key) => (
        <li key={key}>
          <StateBadge state={STATES[key]} />
        </li>
      ))}
    </ul>
  );
}

/**
 * A review's lifecycle position. Deliberately monochrome.
 *
 * `drafting` is not good or bad, it is a position in a sequence, so it gets weight and a
 * rule rather than a hue. Six state colours already carry meaning; adding three more for
 * lifecycle positions would dilute all of them.
 */
export function LifecycleBadge({ state, tone }: { state: string; tone: string }) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-sm px-2 py-1 text-xs font-medium shadow-line',
        tone === 'terminal' ? 'text-muted' : 'text-primary',
      )}
    >
      {state.replace(/_/g, ' ')}
    </span>
  );
}

// ---------------------------------------------------------------------------------
// Magnitudes
// ---------------------------------------------------------------------------------

/**
 * A proportion, drawn once.
 *
 * One hue at varying length rather than a spectrum: a score is a magnitude, and giving
 * magnitudes different hues invents categories that are not there.
 */
export function Meter({
  value,
  max = 1,
  label,
  className,
}: {
  value: number;
  max?: number;
  label: string;
  className?: string;
}) {
  const fraction = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  const style: CSSProperties = { width: `${(fraction * 100).toFixed(1)}%` };
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cx('block h-1 w-full overflow-hidden rounded-sm bg-track', className)}
    >
      <span className="block h-full bg-scale" style={style} />
    </span>
  );
}

// ---------------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------------

/**
 * One line of a dense list.
 *
 * Selection is a ring plus a background step, never a colour: the row is not a *status*, it
 * is the thing the cursor is on. The ring is a shadow so the row does not resize.
 */
export function Row({
  children,
  selected,
  onClick,
  className,
  id,
}: {
  children: ReactNode;
  selected?: boolean;
  onClick?: () => void;
  className?: string;
  id?: string;
}) {
  return (
    <div
      id={id}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? -1 : undefined}
      onClick={onClick}
      className={cx(
        'flex w-full items-center gap-3 border-b border-subtle px-4 py-2 text-left transition-colors',
        onClick ? 'cursor-pointer' : '',
        selected ? 'bg-active' : 'hover:bg-hover',
        className,
      )}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Form controls
//
// Three inputs and a label, because the New review form needs exactly three inputs and a
// component library is a liability at this size. Each one is a hairline and a background
// step -- the same vocabulary as everything else, so a form does not look like a different
// application from the grid next to it.
// ---------------------------------------------------------------------------------

const CONTROL =
  'h-row w-full rounded-sm bg-sunken px-2 text-sm text-primary shadow-line outline-none placeholder:text-muted';

export function Field({
  label,
  hint,
  id,
  children,
}: {
  label: string;
  hint?: ReactNode;
  id?: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={id} className="flex flex-col gap-2">
      <Label>{label}</Label>
      {children}
      {hint ? <span className="text-xs text-muted">{hint}</span> : null}
    </label>
  );
}

export function TextInput({
  value,
  onChange,
  placeholder,
  disabled,
  autoFocus,
  id,
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  id?: string;
}) {
  return (
    <input
      id={id}
      value={value}
      disabled={disabled}
      autoFocus={autoFocus}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className={cx(CONTROL, 'disabled:opacity-50')}
    />
  );
}

export function Select<T extends string>({
  value,
  onChange,
  options,
  disabled,
  id,
}: {
  value: T;
  onChange: (next: T) => void;
  options: ReadonlyArray<{ value: T; label: string }>;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as T)}
      className={cx(CONTROL, 'disabled:opacity-50')}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/**
 * Determinate progress, for the one thing in this interface that has a known total: an
 * upload. Everything else is a count of work whose size is not known until it is done, and
 * a bar that guesses at that would be a fiction rendered as a measurement.
 */
export function Progress({ fraction, label }: { fraction: number; label: string }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-muted">{label}</span>
        <Mono>{Math.round(fraction * 100)}%</Mono>
      </div>
      <Meter value={fraction} label={label} />
    </div>
  );
}

/**
 * A tab strip. Used by the trace viewer, where the three panes genuinely are alternatives.
 *
 * The review workspace deliberately does NOT use this: its panes are simultaneous, because
 * losing the question list to read an answer is the thing the three-pane layout exists to
 * prevent.
 */
export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: ReadonlyArray<{ id: T; label: string; count?: number }>;
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="tablist" className="flex shrink-0 items-center gap-1 border-b border-subtle px-4 py-2">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={tab.id === active}
          onClick={() => onChange(tab.id)}
          className={cx(
            'inline-flex items-center gap-2 rounded-sm px-2 py-1 text-sm transition-colors',
            tab.id === active
              ? 'bg-active font-medium text-primary'
              : 'text-secondary hover:bg-hover',
          )}
        >
          {tab.label}
          {typeof tab.count === 'number' ? (
            <span className="font-mono text-xs tabular-nums text-muted">{tab.count}</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}
