import Link from 'next/link';

import { FleetRoster } from '@/components/fleet/FleetRoster';
import { ScopeMatrix } from '@/components/registry/ScopeMatrix';
import { Label, Mono, cx } from '@/components/ui/primitives';
import type { InboxStatus, RegistryAgent } from '@/lib/api/client';
import type { AgentActivity, FleetMember } from '@/lib/fleet';

/**
 * The fleet, as live status.
 *
 * ## What this page is for
 *
 * Eight agents exist, each with its own Agent Identity, and one of them cannot read
 * another's corpus. That is the architecture, and for scoring purposes an architecture
 * nobody can see is the same as one that is not there.
 *
 * ## What Phase 8 changed, and why
 *
 * It was eight cards, each listing `reads`, `refused` and `identity`, stacked down the page.
 * Every fact was true and carefully sourced — and the whole thing read as generated
 * documentation, because it *was* documentation. It described the system's configuration on
 * the page whose job is to show the system working.
 *
 * Now: a roster saying what each agent is doing right now and what it did today, with the
 * configuration one click down in a per-agent drawer; and the scope story as **one** compact
 * grid instead of the same three lines repeated eight times. Proof one click deep is
 * checkable. Proof stacked eight times on a landing page is wallpaper.
 *
 * The refusals are still rendered rather than omitted. A permission grid where every cell is
 * filled proves nothing; the dashes are the content.
 */

export function FleetBoard({
  members,
  activity,
  registryAgents,
  inbox,
  inboxError,
  registryError,
}: {
  members: FleetMember[];
  activity: Map<string, AgentActivity>;
  /** The registry rows behind the scope matrix. Read live; empty when the read failed. */
  registryAgents: RegistryAgent[];
  inbox: InboxStatus | null;
  inboxError: string | null;
  registryError: string | null;
}) {
  const working = members.filter((m) => activity.get(m.actor ?? '')?.working).length;
  const today = members.reduce((total, m) => total + (activity.get(m.actor ?? '')?.today ?? 0), 0);
  const answers = members.reduce(
    (total, m) => total + (activity.get(m.actor ?? '')?.answers ?? 0),
    0,
  );

  return (
    <div className="mx-auto flex w-full max-w-page flex-col gap-12 px-6 py-8">
      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline justify-between gap-4">
          <h2 className="text-md text-primary">
            {members.length} agents{working > 0 ? `, ${working} working now` : ', none working now'}
          </h2>
          <p className="text-sm text-muted">
            {today > 0 ? `${today} events today · ` : ''}
            {answers} answers across the reviews read here
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
          <FleetRoster members={members} activity={activity} />
        )}
      </section>

      {/* One compact grid, once: three departments, three corpora, dots and dashes. Only the
          department engines, because they are the ones whose scope is a *claim* -- the
          orchestrator, the verifier and the two in-process agents hold no corpus binding at
          all, and four rows reading "no corpus, no corpus, no corpus" turn a diagonal into a
          block and cost the picture the thing that makes it worth looking at. Their absence
          of scope is in their own drawer, where it belongs. */}
      {registryError === null && registryAgents.length > 0 ? (
        <section className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-4">
            <h2 className="text-md text-primary">
              What each department identity may read
            </h2>
            <Link href="/registry" className="text-sm">
              The registry
            </Link>
          </div>
          <div className="rounded border border-line bg-surface py-2">
            <ScopeMatrix agents={registryAgents} />
          </div>
        </section>
      ) : null}

      <Inbound inbox={inbox} error={inboxError} />
    </div>
  );
}

/**
 * The mailbox, and whether it is actually being watched.
 *
 * On this page rather than only in settings for one reason: a lapsed Gmail watch is
 * invisible from the outside. It expires after seven days, Gmail does not warn, and a
 * mailbox that has stopped notifying looks exactly like a mailbox nobody has emailed. The
 * hours remaining going negative is the only signal there is, so it is on screen.
 *
 * ## What this used to say
 *
 * *"No watch is registered, so no email will start a review. Register one with
 * `tools/gmail_watch.py --apply`."* A shell command, printed in the product, as an
 * instruction to the reader — and the clearest single symptom of an interface that
 * documented the system rather than being it. The unregistered case now links to
 * Connections, where the button that registers it lives.
 */
function Inbound({ inbox, error }: { inbox: InboxStatus | null; error: string | null }) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-baseline gap-4">
        <h2 className="text-md text-primary">Inbound</h2>
        <Link href="/connections" className="text-sm">
          Connections
        </Link>
      </div>

      {error !== null ? (
        <p className="text-sm text-denied">The mailbox status could not be read. {error}</p>
      ) : inbox === null || !inbox.watching ? (
        <p className="max-w-prose text-sm text-muted">
          No mailbox is being watched, so no email starts a review.{' '}
          <Link href="/connections">Connect one</Link>.
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
