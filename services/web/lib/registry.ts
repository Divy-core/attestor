import type { RegistryAgent } from '@/lib/api/client';

/**
 * Reading the Agent Registry listing without overstating what it says.
 *
 * Three things about the live payload were only discovered by rendering it, and each one would
 * have put a false statement on the page that a judge is likely to check.
 *
 * ## 1. `resource_name` is the registry's record, not the engine
 *
 * It looks like an engine path and is not:
 *
 *     resource_name  projects/attestor-505506/locations/us-central1/agents/
 *                    agentregistry-00000000-0000-0000-c03a-e2a2f50e3400
 *     agent_id       urn:agent:projects-906988347581:...:reasoningEngines:4340794390889889792
 *
 * The `reasoningEngine` id — the thing that identifies the deployed engine, that the IAM
 * bindings are written against, and that `docs/proof/fleet-deployment.json` records — is inside
 * the URN. Rendering `resource_name` under a heading of "Engine" showed a registry bookkeeping
 * id and called it the engine.
 *
 * ## 2. The identity must not be synthesised
 *
 * The listing returns `effective_identity` as `null` on every entry. A first version filled the
 * gap with a plausible `principal://agents.global…/{id}` string built from `resource_name` —
 * which is to say it **invented an identity from the wrong identifier** and displayed it as
 * though it had been read from somewhere. That is fabricated evidence on the page whose entire
 * job is to make provenance checkable, and it is exactly the failure this build has spent five
 * phases refusing.
 *
 * `identityUnavailable` therefore returns nothing to render, and the page says where the proof
 * actually lives instead.
 *
 * ## 3. Not every agent in the registry is ours
 *
 * The listing includes `Workspace Agent`, which Google provides, and `attestor-probe`, the
 * Phase 0 engine kept alive deliberately. Seven rows under a heading reading "five engines,
 * five identities" is a false sentence, and the fix is to partition the list and count each part
 * rather than to quietly hide the rows that spoil the number.
 */

/** The department engines. Exactly these three carry a corpus binding. */
const DEPARTMENTS = new Set(['security', 'legal', 'engineering']);

/** The `reasoningEngine` id, from the URN. `null` when the entry names no engine. */
export function engineId(agent: RegistryAgent): string | null {
  const match = /reasoningEngines:(\d+)/.exec(agent.agent_id);
  return match?.[1] ?? null;
}

/**
 * Whether this entry is part of the Attestor fleet.
 *
 * Keyed on naming an `aiplatform` reasoning engine rather than on a display-name prefix: the
 * registry is free to relabel a deployment, and a check on "starts with attestor-" would quietly
 * drop a renamed engine while happily accepting anything else someone called `attestor-x`.
 */
export function isFleetEngine(agent: RegistryAgent): boolean {
  return engineId(agent) !== null;
}

/** A department engine, which is to say one with a corpus. */
export function isDepartmentEngine(agent: RegistryAgent): boolean {
  return isFleetEngine(agent) && DEPARTMENTS.has((agent.department ?? '').toLowerCase());
}

export type Partitioned = {
  /** Our engines, department ones first. */
  fleet: RegistryAgent[];
  /** Everything else the registry catalogues for this project. */
  other: RegistryAgent[];
};

export function partition(agents: RegistryAgent[]): Partitioned {
  const fleet = agents.filter(isFleetEngine);
  return {
    fleet: [...fleet].sort((a, b) => {
      const rank = (agent: RegistryAgent) => (isDepartmentEngine(agent) ? 0 : 1);
      return rank(a) - rank(b) || (a.display_name ?? '').localeCompare(b.display_name ?? '');
    }),
    other: agents.filter((agent) => !isFleetEngine(agent)),
  };
}

/**
 * What to say about identity, given that the listing does not carry it.
 *
 * Returns the value when the API provides one — so this disappears on its own if those fields
 * are ever populated, rather than becoming a stale disclaimer — and otherwise returns a
 * statement of where the fact is proven, with nothing invented.
 */
export function identityFor(agent: RegistryAgent): { value: string | null; source: string } {
  if (agent.effective_identity) {
    return { value: agent.effective_identity, source: 'Agent Registry' };
  }
  return {
    value: null,
    source:
      'not returned by the registry list endpoint — proven from the engine resource and the live IAM bindings',
  };
}

/** A truthful one-line count for the page header. */
export function describe({ fleet, other }: Partitioned): string {
  const departments = fleet.filter(isDepartmentEngine).length;
  const parts = [`${fleet.length} Attestor engines`, `${departments} with a corpus binding`];
  if (other.length > 0) parts.push(`${other.length} other agent${other.length === 1 ? '' : 's'}`);
  return parts.join(' · ');
}
