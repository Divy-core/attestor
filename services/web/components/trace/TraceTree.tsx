'use client';

import { useMemo, useState } from 'react';

import { Button, Mono, cx } from '@/components/ui/primitives';
import { absolute, duration, humanKind } from '@/lib/format';
import type { AuditEvent } from '@/lib/api/client';

/**
 * The compliance plane, as a tree.
 *
 * 949 events for one review — 312 `question_triaged`, 221 `evidence_retrieved`, 221
 * `answer_drafted`, 180 `human_required`, 10 `armor_blocked`, 3 `work_dead_lettered` — so this
 * has to collapse, filter, and stay fast. A flat 949-row list is not an audit trail anyone can
 * read; it is a log file with a stylesheet.
 *
 * ## The tree is grouped by question, not by time
 *
 * "Why did we answer yes to Q112?" is the question this plane exists to answer, and answering
 * it from a chronological feed means scanning 949 rows for the eight that mention Q112. So the
 * primary grouping is the question, each group holds its own events in order, and the events
 * that belong to no question — run lifecycle, dead letters, round close — group under the run.
 *
 * That is a deliberate departure from how a *trace* viewer normally works. Cloud Trace is
 * organised by span parentage because engineers ask "what was slow"; this plane is organised
 * by subject because auditors ask "what happened to this claim". Different consumers, different
 * shapes — which is the architectural point the two planes are making.
 *
 * ## Why events are not spans
 *
 * These rows have no duration and no parent pointer. They are appended facts, not a call tree,
 * and rendering them with connector lines and elapsed bars would dress them up as something
 * they are not. The engineering plane's real span tree is shown separately and labelled as
 * inherited from the platform — see `components/trace/PlaneNote.tsx`.
 */

/** Kinds that carry a refusal or a block. Called out because they are the interesting minority
 *  and a reader scanning 949 rows should not have to find them by reading. */
const NOTABLE = new Set([
  'armor_blocked',
  'tool_denied',
  'work_dead_lettered',
  'permission_denied',
  'consistency_checked',
  'human_required',
  'human_decision',
]);

type Group = {
  key: string;
  label: string;
  events: AuditEvent[];
  notable: number;
};

export function TraceTree({ events }: { events: AuditEvent[] }) {
  const [filter, setFilter] = useState('');
  const [notableOnly, setNotableOnly] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const event of events) counts.set(event.kind, (counts.get(event.kind) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [events]);

  const groups = useMemo<Group[]>(() => {
    const byKey = new Map<string, AuditEvent[]>();
    for (const event of events) {
      // Events with no question belong to the run, not to an arbitrary question.
      const key = event.question_id ?? `run:${event.run_id ?? 'unknown'}`;
      const bucket = byKey.get(key);
      if (bucket) bucket.push(event);
      else byKey.set(key, [event]);
    }
    return [...byKey.entries()]
      .map(([key, list]) => ({
        key,
        label: key.startsWith('run:') ? `Run ${key.slice(4)}` : key,
        events: [...list].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0)),
        notable: list.filter((e) => NOTABLE.has(e.kind)).length,
      }))
      // Groups containing a refusal or a block first: they are what someone opened this page
      // for, and burying them in alphabetical order by question id would be a filing decision
      // masquerading as a neutral one.
      .sort((a, b) => b.notable - a.notable || a.label.localeCompare(b.label));
  }, [events]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return groups.filter((group) => {
      if (notableOnly && group.notable === 0) return false;
      if (needle.length === 0) return true;
      if (group.label.toLowerCase().includes(needle)) return true;
      return group.events.some(
        (event) =>
          event.kind.includes(needle) ||
          (event.actor ?? '').toLowerCase().includes(needle),
      );
    });
  }, [groups, filter, notableOnly]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-2 border-b border-subtle px-4 py-3">
        <div className="flex items-center gap-2">
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter by question, event kind or agent"
            aria-label="Filter events"
            className="w-full rounded-sm shadow-line bg-sunken px-2 py-1 text-sm text-primary placeholder:text-muted"
          />
          <Mono dim className="shrink-0 tabular-nums">
            {events.length} events
          </Mono>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <button
            onClick={() => setNotableOnly(!notableOnly)}
            aria-pressed={notableOnly}
            className={cx(
              'rounded-sm border px-2 py-1 text-xs transition-colors',
              notableOnly
                ? 'border-strong bg-active text-primary'
                : 'border-subtle text-muted hover:bg-hover',
            )}
          >
            Refusals and blocks only
          </button>
          {kinds.slice(0, 8).map(([kind, count]) => (
            <button
              key={kind}
              onClick={() => setFilter(kind)}
              className="rounded-sm shadow-line px-2 py-1 text-xs text-muted transition-colors hover:bg-hover"
            >
              {humanKind(kind)}{' '}
              <span className="font-mono tabular-nums">{count}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {visible.length === 0 ? (
          <p className="px-4 py-8 text-sm text-secondary">
            No event matches this filter. The review has {events.length} in total.
          </p>
        ) : (
          <ul className="flex flex-col">
            {visible.map((group) => {
              const expanded = open.has(group.key);
              return (
                <li key={group.key} className="border-b border-subtle">
                  <button
                    onClick={() =>
                      setOpen((current) => {
                        const next = new Set(current);
                        if (next.has(group.key)) next.delete(group.key);
                        else next.add(group.key);
                        return next;
                      })
                    }
                    aria-expanded={expanded}
                    className="flex w-full items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-hover"
                  >
                    <span aria-hidden className="w-4 shrink-0 font-mono text-xs text-muted">
                      {expanded ? '−' : '+'}
                    </span>
                    <Mono className="min-w-0 flex-1 truncate">{group.label}</Mono>
                    <span className="shrink-0 text-xs text-muted">
                      {group.events.length} events
                    </span>
                    {group.notable > 0 ? (
                      <span className="shrink-0 rounded-sm shadow-line bg-fill-denied px-2 py-1 text-xs text-denied">
                        {group.notable} notable
                      </span>
                    ) : null}
                  </button>

                  {expanded ? (
                    <ol className="border-l-2 border-line pl-4 ml-6 mb-2">
                      {group.events.map((event, index) => (
                        <EventRow key={`${event.event_id ?? index}`} event={event} />
                      ))}
                    </ol>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-subtle px-4 py-2">
        <Button
          tone="ghost"
          onClick={() => setOpen(new Set(visible.map((group) => group.key)))}
        >
          Expand all
        </Button>
        <Button tone="ghost" onClick={() => setOpen(new Set())}>
          Collapse all
        </Button>
      </div>
    </div>
  );
}

/**
 * One appended fact.
 *
 * `detail` varies by kind and is rendered structurally rather than by assuming fields. A
 * per-kind renderer would look better on the six kinds someone wrote it for and silently drop
 * everything on the seventh — and in an audit trail, a field that is stored but not displayed
 * is a field that is not really in the record.
 */
function EventRow({ event }: { event: AuditEvent }) {
  const notable = NOTABLE.has(event.kind);
  const detail = event.detail ?? {};
  const entries = Object.entries(detail).filter(([, value]) => value !== null && value !== '');

  return (
    <li className="flex flex-col gap-1 py-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className={cx(
            'text-sm',
            notable ? 'font-medium text-primary' : 'text-secondary',
          )}
        >
          {humanKind(event.kind)}
        </span>
        {/* Attribution per event: which agent, which identity, which department. Without this
            the plane records that something happened and not who did it, which is the one
            thing an auditor cannot do without. */}
        {event.actor ? <Mono dim>{event.actor}</Mono> : null}
        {typeof event.seq === 'number' ? <Mono dim>seq {event.seq}</Mono> : null}
        <span className="text-xs text-muted" title={absolute(event.recorded_at)}>
          {event.recorded_at ? absolute(event.recorded_at) : '--'}
        </span>
      </div>

      {entries.length > 0 ? (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          {entries.map(([key, value]) => (
            <div key={key} className="col-span-2 grid grid-cols-subgrid">
              <dt className="text-xs text-muted">{key}</dt>
              <dd className="min-w-0">
                <Mono className="block break-words">{renderValue(value)}</Mono>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </li>
  );
}

function renderValue(value: unknown): string {
  if (typeof value === 'number') {
    // Durations are stored in seconds throughout the audit plane.
    return Number.isInteger(value) ? String(value) : duration(value);
  }
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map((item) => String(item)).join(', ');
  return JSON.stringify(value);
}
