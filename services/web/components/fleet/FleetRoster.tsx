'use client';

import Link from 'next/link';
import { useState } from 'react';

import { Label, Mono, cx } from '@/components/ui/primitives';
import { ago } from '@/lib/format';
import { doingNow, type AgentActivity, type FleetMember } from '@/lib/fleet';

/**
 * The fleet, as live status rather than as a specification sheet.
 *
 * ## What this replaced, and why it was wrong
 *
 * Eight cards, each listing `reads`, `refused` and `identity`, stacked down the landing
 * page. Every fact on them was true and carefully sourced, and the whole thing read as
 * generated documentation — because it was documentation. It described a system's
 * *configuration* on the page whose job is to show the system *working*.
 *
 * A row now says what an agent is doing right now, what it did today, and whether it is
 * idle. The configuration has not been deleted or weakened: it is one click down, in a
 * drawer, which is where proof belongs. Proof one click deep is checkable; proof stacked
 * eight times on the landing surface is wallpaper.
 *
 * ## Idle is a state, not an absence
 *
 * An agent with no events still gets a row. A roster that shows only the busy agents makes
 * an eight-agent fleet look like however many happen to be working, and "SecurityAgent has
 * done nothing today" is a fact worth being able to read.
 *
 * ## Every claim carries where it came from
 *
 * The drawer keeps the distinction the previous cards were careful about: the engine id and
 * the department come from the **live** Agent Registry, and the corpus bindings are a
 * description of `infra/iam/scope_agents.py`, because the registry's list endpoint returns
 * empty scopes on every entry — measured, not assumed. Filling that gap with a plausible
 * value would be inventing evidence on the surface whose entire job is making evidence
 * checkable.
 */

export function FleetRoster({
  members,
  activity,
}: {
  members: FleetMember[];
  activity: Map<string, AgentActivity>;
}) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <ul className="rounded border border-line bg-surface">
      {members.map((member) => {
        const live = activity.get(member.actor ?? '') ?? null;
        const expanded = open === member.id;
        return (
          <li key={member.id} className="border-b border-subtle last:border-0">
            <button
              type="button"
              onClick={() => setOpen(expanded ? null : member.id)}
              aria-expanded={expanded}
              className="flex w-full items-center gap-4 px-6 py-3 text-left transition-colors hover:bg-hover"
            >
              <span
                aria-hidden
                title={live?.working ? 'Active in the last three minutes' : 'Idle'}
                className={cx(
                  'inline-block h-2 w-2 shrink-0 rounded-sm',
                  live?.working ? 'bg-cited pulse-working' : 'bg-track',
                )}
              />
              {/* The name this agent writes into the audit trail, because that is the name
                  the thread, the answers and the export all use -- and a fleet page calling
                  the same agent something else makes a reader do a translation nobody asked
                  them for. The deployed engine's own display name sits beside it. */}
              <span className="w-list max-w-list shrink-0 truncate text-sm text-primary">
                {member.actor ?? member.name}
              </span>
              {member.name !== member.actor ? (
                <span className="hidden shrink-0 lg:block">
                  <Mono dim>{member.name}</Mono>
                </span>
              ) : null}
              <span className="hidden shrink-0 text-xs text-muted xl:block">
                {member.role === 'department' ? member.department : member.role}
              </span>
              <span
                className={cx(
                  'min-w-0 flex-1 truncate text-sm',
                  live?.working ? 'text-primary' : 'text-secondary',
                )}
              >
                {doingNow(live)}
              </span>
              {live && live.today > 0 ? (
                <span className="hidden shrink-0 text-xs text-muted xl:block">
                  {live.today} today
                </span>
              ) : null}
              <span className="shrink-0 text-right">
                <Mono dim>{live?.lastAt ? ago(live.lastAt) : '—'}</Mono>
              </span>
              <span
                aria-hidden
                className={cx(
                  'shrink-0 font-mono text-xs text-muted transition-transform',
                  expanded ? 'rotate-90' : '',
                )}
              >
                ▸
              </span>
            </button>

            {expanded ? <Drawer member={member} live={live} /> : null}
          </li>
        );
      })}
    </ul>
  );
}

/** The proof, one click deep: what it may read, what it is refused, and who it is. */
function Drawer({ member, live }: { member: FleetMember; live: AgentActivity | null }) {
  const kinds = Object.entries(live?.kinds ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <div className="flex flex-col gap-6 border-t border-subtle px-6 py-4">
      <p className="max-w-prose text-sm text-secondary">{member.purpose}</p>

      <div className="flex flex-wrap gap-x-12 gap-y-4">
        <Block label="reads" source={member.scopeSource}>
          {member.reads.length === 0 ? (
            <span className="text-xs text-muted">no corpus</span>
          ) : (
            <Mono>{member.reads.join(', ')}</Mono>
          )}
        </Block>
        <Block label="refused" source={member.scopeSource}>
          {member.refused.length === 0 ? (
            <span className="text-xs text-muted">nothing — scoped by tool argument</span>
          ) : (
            <Mono dim>{member.refused.join(', ')}</Mono>
          )}
        </Block>
        <Block
          label="identity"
          source={
            member.engine === null
              ? (member.engineNote ?? '')
              : 'reasoningEngines id, from the Agent Registry URN'
          }
        >
          {member.engine === null ? (
            <span className="text-xs text-muted">in the dispatcher</span>
          ) : (
            <Mono>{member.engine}</Mono>
          )}
        </Block>
      </div>

      {kinds.length > 0 ? (
        <div className="flex flex-col gap-2">
          <Label>what it has written to the audit trail</Label>
          <ul className="flex flex-wrap gap-x-8 gap-y-1">
            {kinds.map(([kind, count]) => (
              <li key={kind} className="flex items-baseline gap-2">
                <span className="text-xs text-secondary">{kind.replace(/_/g, ' ')}</span>
                <Mono dim>{count}</Mono>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="max-w-prose text-xs text-muted">
          Nothing on the trail from this agent in the reviews read here.
        </p>
      )}

      {live?.lastReview ? (
        <Link href={`/reviews/${live.lastReview}`} className="text-sm">
          Open the review it last worked on
        </Link>
      ) : null}
    </div>
  );
}

function Block({
  label,
  source,
  children,
}: {
  label: string;
  source: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label>{label}</Label>
      <div className="min-w-0">{children}</div>
      {/* Where the fact came from, next to the fact. Half of every drawer is read from a
          running service and half is a description of committed infrastructure code, and
          this project has spent six phases refusing to blur that. */}
      <span className="max-w-prose text-xs text-muted">{source}</span>
    </div>
  );
}
