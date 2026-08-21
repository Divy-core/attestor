import { AppShell } from '@/components/layout/AppShell';
import { ScopeMatrix } from '@/components/registry/ScopeMatrix';
import { Empty, Failure, Label, Mono, Panel, PanelHeader } from '@/components/ui/primitives';
import { ApiError, api, type RegistryAgent } from '@/lib/api/client';
import { describe, engineId, identityFor, isDepartmentEngine, partition } from '@/lib/registry';

export const dynamic = 'force-dynamic';

/**
 * The fleet, read from the live Agent Registry rather than from our own records.
 *
 * `agentregistry.googleapis.com/v1` — a different service from the Agent Runtime listing, which
 * is the point: the engines are catalogued by the platform with no manual registration step, so
 * this page is discovery working rather than a table we maintain.
 *
 * Every honesty constraint this page has to satisfy is enforced in `lib/registry.ts`, where the
 * reasoning is written down: the engine id comes out of the URN rather than from the registry's
 * own record id, the identity is never synthesised, and agents that are not ours are counted
 * separately instead of being folded into a nicer number.
 */
export default async function RegistryPage() {
  let agents: RegistryAgent[] = [];
  let error: string | null = null;
  try {
    agents = await api.listRegistry();
  } catch (cause) {
    error = cause instanceof ApiError ? cause.human : String(cause);
  }

  const split = partition(agents);
  const anyIdentityFromRegistry = agents.some((agent) => Boolean(agent.effective_identity));

  return (
    <AppShell
      pathname="/registry"
      title="Registry"
      meta={error === null && agents.length > 0 ? describe(split) : undefined}
    >
      <div className="mx-auto flex w-full max-w-page flex-col gap-12 px-6 py-8">
        {error !== null ? (
          <Failure
            what="The Agent Registry could not be read."
            detail={error}
            action={
              <span className="text-xs text-secondary">
                This is a 503, not an empty fleet. The page will not render &ldquo;no agents are
                registered&rdquo; because a call failed — that would be a lie told in a demo.
              </span>
            }
          />
        ) : agents.length === 0 ? (
          <Empty
            title="No agents catalogued"
            hint="Agents appear here automatically when deployed to Agent Runtime — there is no manual registration step. Run services/runtime/deploy_fleet.py to deploy the fleet."
          />
        ) : (
          <>
            <Panel flush>
              <PanelHeader
                title="Scope"
                meta={`${split.fleet.filter(isDepartmentEngine).length} department engines, one corpus each`}
              />
              <ScopeMatrix agents={split.fleet} />
            </Panel>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
              {split.fleet.map((agent) => (
                <AgentCard key={agent.agent_id} agent={agent} />
              ))}
            </div>

            <section className="flex flex-col gap-4">
                <h2 className="text-md text-primary">
                  What this listing does and does not prove
                </h2>
                <p className="max-w-prose text-sm text-secondary">
                  Every entry above came back from{' '}
                  <Mono dim>agentregistry.googleapis.com/v1</Mono> with no manual registration
                  step. That part is the registry&rsquo;s, and it is the claim worth making:
                  discovery is the platform&rsquo;s job and we did not build a catalogue.
                </p>
                {!anyIdentityFromRegistry ? (
                  <p className="max-w-prose text-sm leading-relaxed text-secondary">
                    What it does <em>not</em> carry is identity. The list endpoint returns{' '}
                    <Mono dim>effective_identity</Mono> and <Mono dim>identity_type</Mono> as{' '}
                    <Mono dim>null</Mono> on every entry, so those rows are left blank rather than
                    filled in with a plausible-looking value. Identity distinctness is proven
                    elsewhere and independently — each <Mono dim>agent_id</Mono> names a distinct{' '}
                    <Mono dim>reasoningEngine</Mono>, each engine resource carries its own{' '}
                    <Mono dim>spec.effectiveIdentity</Mono>, and the conditioned bucket bindings
                    refuse a cross-department read. &ldquo;Distinct identities, per the
                    registry&rdquo; would not be a true sentence about this page.
                  </p>
                ) : null}
                {split.other.length > 0 ? (
                  <p className="max-w-prose text-sm leading-relaxed text-secondary">
                    The registry also catalogues{' '}
                    <span className="text-primary">{split.other.length}</span> agent
                    {split.other.length === 1 ? '' : 's'} that {split.other.length === 1 ? 'is' : 'are'}{' '}
                    not part of this fleet —{' '}
                    {split.other.map((agent) => agent.display_name ?? 'unnamed').join(', ')} — listed
                    below rather than filtered out, because a count that quietly excludes what
                    spoils it is not a count.
                  </p>
                ) : null}
            </section>

            {split.other.length > 0 ? (
              <Panel flush>
                <PanelHeader title="Other agents in this project" meta="Not deployed by Attestor" />
                <ul className="flex flex-col">
                  {split.other.map((agent) => (
                    <li
                      key={agent.agent_id}
                      className="flex items-baseline gap-4 border-b border-subtle px-6 py-4 last:border-b-0"
                    >
                      <span className="min-w-0 flex-1 truncate text-sm text-primary">
                        {agent.display_name ?? 'unnamed'}
                      </span>
                      <Mono dim className="min-w-0 flex-1 truncate">
                        {agent.agent_id}
                      </Mono>
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}
          </>
        )}
      </div>
    </AppShell>
  );
}

function AgentCard({ agent }: { agent: RegistryAgent }) {
  const engine = engineId(agent);
  const identity = identityFor(agent);
  return (
    <Panel className="flex flex-col gap-6">
      <header className="flex min-w-0 flex-col gap-2">
        <h3 className="truncate text-base text-primary">
          {agent.display_name ?? agent.name ?? 'unnamed'}
        </h3>
        <Label>{isDepartmentEngine(agent) ? agent.department : 'no corpus binding'}</Label>
      </header>
      <dl className="flex flex-col gap-4">
        <Field
          label="reasoningEngine"
          value={engine}
          title={agent.agent_id}
          source="Agent Registry, from the agent_id URN"
        />
        <Field
          label="Registry record"
          value={agent.resource_name ?? null}
          title={agent.resource_name ?? undefined}
          source="Agent Registry — bookkeeping id, not the engine"
        />
        <Field
          label="Framework"
          value={agent.agent_framework ?? null}
          source="Agent Registry"
        />
        <Field label="Identity type" value={agent.identity_type ?? null} source={identity.source} />
        <Field label="Effective identity" value={identity.value} source={identity.source} />
        {agent.tools && agent.tools.length > 0 ? (
          <Field label="Tools" value={agent.tools.join(', ')} source="Agent Registry" />
        ) : null}
      </dl>
    </Panel>
  );
}

/**
 * A field with its provenance.
 *
 * A `null` value renders as "not returned" rather than as a fallback. Every fallback on this page
 * would be a guess displayed in the same typeface as a fact, on the one page whose subject is
 * being able to check where a fact came from.
 */
function Field({
  label,
  value,
  title,
  source,
}: {
  label: string;
  value: string | null;
  title?: string;
  source: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="min-w-0">
        {value === null ? (
          <span className="block text-sm text-muted">not returned</span>
        ) : (
          <Mono title={title} className="block truncate text-sm">
            {value}
          </Mono>
        )}
        <span className="block text-xs text-muted">{source}</span>
      </dd>
    </div>
  );
}
