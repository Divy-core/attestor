import Link from 'next/link';
import type { ReactNode } from 'react';

import { Label, Mono, cx } from '@/components/ui/primitives';
import type { InboxStatus, ReviewRow } from '@/lib/api/client';
import type { AgentActivity, FleetMember } from '@/lib/fleet';

/**
 * The fleet, as the landing page.
 *
 * The page used to be a list of reviews, which is the least interesting true thing about
 * this system. Seven agents exist, each with its own Agent Identity and its own corpus, and
 * one of them cannot read another's — that is the architecture, and until Phase 7 the
 * interface never said so. A judge who does not see it has no reason to believe it.
 *
 * ## Why this reads quietly
 *
 * The first version of this board put a ring around every card, a rule under every heading,
 * shouted every label in uppercase, and set three metadata rows inside a bordered list on
 * each card. Seven of those on one page is a wall of boxes. What is on screen now is the
 * same information with the boxes taken away: one hairline per card, generous padding inside
 * it, and the metadata as a plain aligned list. Density comes from removing the space
 * *between* things, not from cramping what is inside them.
 *
 * ## The facts, and where each came from
 *
 * The engine id and the department are read from the **live** Agent Registry. The corpus
 * bindings are a description of `infra/iam/scope_agents.py`, because the registry's list
 * endpoint returns empty `scopes` on every entry — measured in Phase 6, not assumed. Filling
 * that gap with a plausible value would be inventing evidence on the page whose entire job is
 * to make evidence checkable, and this build did exactly that once and caught it.
 *
 * The refusals are rendered rather than omitted. A permission list where everything is
 * granted proves nothing; the dashes are the content.
 */

function Dot({ working }: { working: boolean }) {
  return (
    <span
      title={working ? 'Active in the last three minutes' : 'Idle'}
      className={cx(
        'inline-block h-2 w-2 shrink-0 rounded-sm',
        working ? 'bg-cited pulse-working' : 'bg-track',
      )}
    />
  );
}

/** One aligned metadata line. No rule, no chrome — the alignment is the structure. */
function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="shrink-0">
        <Label>{label}</Label>
      </dt>
      <dd className="min-w-0 truncate text-right">{children}</dd>
    </div>
  );
}

function AgentCard({ member, activity }: { member: FleetMember; activity: AgentActivity | null }) {
  const working = activity?.working ?? false;
  const answers = activity?.answers ?? 0;
  return (
    <article className="flex flex-col gap-6 rounded border border-line bg-surface p-6">
      <header className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex items-center gap-2">
            <Dot working={working} />
            <h3 className="truncate text-base text-primary">{member.name}</h3>
          </div>
          <Label>
            {member.role === 'department' ? `${member.department} department` : member.role}
          </Label>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <span className="text-lg tabular-nums text-primary">{answers}</span>
          <Label>{answers === 1 ? 'answer' : 'answers'}</Label>
        </div>
      </header>

      <p className="text-sm leading-relaxed text-secondary">{member.purpose}</p>

      <dl className="flex flex-col gap-3">
        <Fact label="reads">
          {member.reads.length === 0 ? (
            <span className="text-xs text-muted">no corpus</span>
          ) : (
            <Mono title={member.scopeSource}>{member.reads.join(', ')}</Mono>
          )}
        </Fact>
        <Fact label="refused">
          {member.refused.length === 0 ? (
            <span className="text-xs text-muted" title={member.scopeSource}>
              nothing — scoped by tool argument
            </span>
          ) : (
            <Mono dim title={member.scopeSource}>
              {member.refused.join(', ')}
            </Mono>
          )}
        </Fact>
        <Fact label="identity">
          {member.engine === null ? (
            <span className="text-xs text-muted" title={member.engineNote}>
              in the dispatcher
            </span>
          ) : (
            <Mono title="reasoningEngines id, from the Agent Registry URN">{member.engine}</Mono>
          )}
        </Fact>
        {answers > 0 ? (
          <Fact label="cited">
            <Mono>
              {activity?.cited ?? 0} of {answers}
            </Mono>
          </Fact>
        ) : null}
      </dl>
    </article>
  );
}

/**
 * The mailbox, and whether it is actually being watched.
 *
 * On the landing page rather than buried in settings for one reason: a lapsed Gmail watch is
 * invisible from the outside. It expires after seven days, Gmail does not warn, and a mailbox
 * that has stopped notifying looks exactly like a mailbox nobody has emailed. The hours
 * remaining going negative is the only signal there is, so it is on screen.
 */
function Inbound({ inbox, error }: { inbox: InboxStatus | null; error: string | null }) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-md text-primary">Inbound</h2>
      <p className="max-w-prose text-sm leading-relaxed text-secondary">
        A customer emails the watched address. Gmail publishes a change notification to
        Pub/Sub, the dispatcher turns it into a unit of work, and the fleet answers it. A reply
        on a thread Attestor already owns wakes that review and opens the next round instead.
        Nobody opens this page for any of that to happen.
      </p>

      {error !== null ? (
        <p className="text-sm text-denied">The mailbox status could not be read. {error}</p>
      ) : inbox === null || !inbox.watching ? (
        <p className="text-sm text-muted">
          No watch is registered, so no email will start a review. Register one with{' '}
          <span className="font-mono text-xs">tools/gmail_watch.py --apply</span>.
        </p>
      ) : (
        <dl className="flex flex-wrap items-baseline gap-10">
          <div className="flex flex-col gap-2">
            <Label>watching</Label>
            <Mono>{inbox.address || 'unknown'}</Mono>
          </div>
          <div className="flex flex-col gap-2">
            <Label>topic</Label>
            <Mono>{inbox.topic.split('/').pop()}</Mono>
          </div>
          <div className="flex flex-col gap-2">
            <Label>watch expires</Label>
            <span
              className={cx(
                'font-mono text-xs tabular-nums',
                inbox.expired ? 'text-denied' : 'text-secondary',
              )}
            >
              {inbox.expired ? 'EXPIRED — no email is arriving' : `${inbox.expires_in_hours ?? 0}h`}
            </span>
          </div>
        </dl>
      )}
    </section>
  );
}

export function FleetBoard({
  members,
  activity,
  reviews,
  archivedCount,
  inbox,
  inboxError,
  registryError,
}: {
  members: FleetMember[];
  activity: Map<string, AgentActivity>;
  reviews: ReviewRow[];
  archivedCount: number;
  inbox: InboxStatus | null;
  inboxError: string | null;
  registryError: string | null;
}) {
  const working = members.filter((m) => activity.get(m.actor ?? '')?.working).length;
  const answers = members.reduce(
    (total, m) => total + (activity.get(m.actor ?? '')?.answers ?? 0),
    0,
  );

  return (
    <div className="mx-auto flex w-full max-w-page flex-col gap-12 px-6 py-8">
      <Inbound inbox={inbox} error={inboxError} />

      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline justify-between gap-4">
          <h2 className="text-md text-primary">
            {members.length} agents{working > 0 ? `, ${working} working now` : ''}
          </h2>
          <p className="text-sm text-muted">
            {answers} answers across the reviews below · department engines read one corpus each
          </p>
        </div>

        {registryError !== null ? (
          <div className="flex flex-col gap-3 rounded border border-line p-6">
            <p className="text-sm text-denied">
              The Agent Registry is unreachable, so the deployed engines cannot be listed.
            </p>
            <p className="font-mono text-xs text-secondary">{registryError}</p>
            <p className="text-sm text-muted">
              An empty fleet would be a claim. This is a failed read, and it says so.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {members.map((member) => (
              <AgentCard
                key={member.id}
                member={member}
                activity={activity.get(member.actor ?? '') ?? null}
              />
            ))}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-md text-primary">Reviews</h2>
          <Link href="/reviews" className="text-sm">
            All{archivedCount > 0 ? ` · ${archivedCount} archived` : ''}
          </Link>
        </div>
        <ul className="rounded border border-line bg-surface">
          {reviews.map((review) => (
            <li key={review.review_id}>
              <Link
                href={`/reviews/${review.review_id}`}
                className="flex items-center gap-4 border-b border-subtle px-6 py-4 no-underline last:border-0 hover:bg-hover hover:no-underline"
              >
                <span className="min-w-0 flex-1 truncate text-sm text-primary">
                  {review.customer}
                </span>
                <span className="shrink-0 text-sm text-muted">
                  {review.state.replace(/_/g, ' ')}
                </span>
                <Mono dim className="shrink-0">
                  round {review.current_round}
                </Mono>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
