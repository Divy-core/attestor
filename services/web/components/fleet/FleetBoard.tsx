import Link from 'next/link';

import { Label, Mono, Panel, StateDot, cx } from '@/components/ui/primitives';
import type { InboxStatus, ReviewRow } from '@/lib/api/client';
import type { AgentActivity, FleetMember } from '@/lib/fleet';

/**
 * The fleet, as the landing page.
 *
 * The page used to be a list of reviews, which is the least interesting true thing about
 * this system. Six agents exist, each with its own Agent Identity and its own corpus, and one
 * of them cannot read another's — that is the architecture, and until Phase 7 the interface
 * never said so. A judge who does not see it has no reason to believe it.
 *
 * Each card carries four things and keeps them visibly separate:
 *
 *   1. **What it is** — name, role, and the one-line job.
 *   2. **What it can reach** — the corpus it reads and the ones it is refused, with the
 *      refusals shown rather than omitted. A permission list where everything is granted
 *      proves nothing; the dashes are the content.
 *   3. **Its identity** — the `reasoningEngines` id, which is what the IAM bindings are
 *      written against and therefore the only value a reader can check anything with.
 *   4. **What it is doing** — answers written, how many of them cited, and whether it moved
 *      in the last three minutes.
 *
 * The source of each fact is on the card. The engine id and the department are read from the
 * live Agent Registry; the corpus bindings are a description of `infra/iam/scope_agents.py`,
 * because the registry's list endpoint returns empty scopes on every entry. Filling that gap
 * with a plausible value would be inventing evidence on the page whose entire job is to make
 * evidence checkable — a mistake this build made once, in Phase 6, and caught.
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

function AgentCard({ member, activity }: { member: FleetMember; activity: AgentActivity | null }) {
  const working = activity?.working ?? false;
  return (
    <article className="flex flex-col gap-3 rounded bg-surface p-4 shadow-line">
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex items-center gap-2">
            <Dot working={working} />
            <h3 className="truncate text-base font-semibold text-primary">{member.name}</h3>
          </div>
          <Label>
            {member.role === 'department' ? `${member.department} department` : member.role}
          </Label>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="text-md font-medium tabular-nums text-primary">
            {activity?.answers ?? 0}
          </span>
          <Label>answers</Label>
        </div>
      </header>

      <p className="text-sm text-secondary">{member.purpose}</p>

      <dl className="flex flex-col gap-2 border-t border-subtle pt-3">
        <div className="flex items-baseline justify-between gap-3">
          <dt className="shrink-0">
            <Label>reads</Label>
          </dt>
          <dd className="min-w-0 truncate text-right">
            {member.reads.length === 0 ? (
              <span className="text-xs text-muted">no corpus</span>
            ) : (
              <Mono title={member.scopeSource}>{member.reads.join(', ')}</Mono>
            )}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="shrink-0">
            <Label>refused</Label>
          </dt>
          <dd className="min-w-0 truncate text-right" title={member.scopeSource}>
            {member.refused.length === 0 ? (
              <span className="text-xs text-muted">nothing — see the note</span>
            ) : (
              <Mono dim>{member.refused.join(', ')}</Mono>
            )}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="shrink-0">
            <Label>identity</Label>
          </dt>
          <dd className="min-w-0 truncate text-right">
            {member.engine === null ? (
              <span className="text-xs text-muted" title={member.engineNote}>
                in the dispatcher
              </span>
            ) : (
              <Mono title="reasoningEngines id, from the Agent Registry URN">
                {member.engine}
              </Mono>
            )}
          </dd>
        </div>
      </dl>

      {activity && activity.answers > 0 ? (
        <p className="text-xs text-muted">
          {activity.cited} of {activity.answers} carry a citation
        </p>
      ) : null}
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
    <Panel className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-md font-semibold text-primary">Inbound</h2>
        <Label>how work arrives</Label>
      </div>
      <p className="max-w-prose text-sm text-secondary">
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
        <dl className="flex flex-wrap items-baseline gap-6">
          <div className="flex flex-col gap-1">
            <Label>watching</Label>
            <Mono>{inbox.address || 'unknown'}</Mono>
          </div>
          <div className="flex flex-col gap-1">
            <Label>topic</Label>
            <Mono>{inbox.topic.split('/').pop()}</Mono>
          </div>
          <div className="flex flex-col gap-1">
            <Label>watch expires</Label>
            <span
              className={cx(
                'font-mono text-xs tabular-nums',
                inbox.expired ? 'text-denied' : 'text-secondary',
              )}
            >
              {inbox.expired
                ? 'EXPIRED — no email is arriving'
                : `${inbox.expires_in_hours ?? 0}h`}
            </span>
          </div>
        </dl>
      )}
    </Panel>
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
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      <Inbound inbox={inbox} error={inboxError} />

      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-md font-semibold text-primary">
            {members.length} agents{working > 0 ? `, ${working} working now` : ''}
          </h2>
          <p className="text-sm text-muted">
            {answers} answers across the reviews below · department engines read one corpus each
          </p>
        </div>

        {registryError !== null ? (
          <Panel>
            <p className="text-sm text-denied">
              The Agent Registry is unreachable, so the deployed engines cannot be listed.
            </p>
            <p className="pt-2 font-mono text-xs text-secondary">{registryError}</p>
            <p className="pt-2 text-sm text-muted">
              An empty fleet would be a claim. This is a failed read, and it says so.
            </p>
          </Panel>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
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

      <Panel flush>
        <header className="flex items-baseline justify-between gap-4 border-b border-subtle px-4 py-3">
          <h2 className="text-md font-semibold text-primary">Reviews</h2>
          <Link href="/reviews" className="text-sm">
            All {archivedCount > 0 ? `· ${archivedCount} archived` : ''}
          </Link>
        </header>
        <ul>
          {reviews.map((review) => (
            <li key={review.review_id}>
              <Link
                href={`/reviews/${review.review_id}`}
                className="flex items-center gap-3 border-b border-subtle px-4 py-3 no-underline last:border-0 hover:bg-hover hover:no-underline"
              >
                <StateDot form={review.state === 'awaiting_human' ? 'half' : 'solid'} />
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-primary">
                  {review.customer}
                </span>
                <span className="shrink-0 text-sm text-secondary">
                  {review.state.replace(/_/g, ' ')}
                </span>
                <Mono dim className="shrink-0">
                  round {review.current_round}
                </Mono>
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
