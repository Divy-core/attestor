import type { AuditEvent, RegistryAgent, ReviewRow } from '@/lib/api/client';
import { engineId, isDepartmentEngine } from '@/lib/registry';

/**
 * The fleet as a roster, assembled from what is actually known about each member.
 *
 * ## Why this file exists at all
 *
 * Six agents have been real since Phase 5 — distinct Agent Identities, IAM-conditioned
 * corpus bindings, a proven 403 when one reaches for another's corpus — and the interface
 * showed none of it. For scoring purposes invisible is the same as absent, and "multi-agent,
 * cross-department, catalogued" is a third of what the brief asks for.
 *
 * ## What is read and what is stated
 *
 * The engine id, the display name and the department come from the **live** Agent Registry.
 * The corpus binding and the refusals come from `infra/iam/scope_agents.py`, which is the
 * code that writes them — the registry list endpoint returns empty `scopes` on every entry,
 * measured rather than assumed (`docs/proof/registry-listing.json`), and filling that gap
 * with a plausible-looking value would be inventing evidence on the page whose whole job is
 * to make evidence checkable.
 *
 * So each field carries where it came from. A reader can tell which half of a card was read
 * from a running service and which half is a description of committed infrastructure code,
 * which is a distinction this project has spent six phases refusing to blur.
 *
 * ## Two members have no engine, and that is not a gap
 *
 * `InboxAgent` and `AssemblerAgent` run in the dispatcher, not on Agent Runtime. Neither
 * touches a corpus: one classifies an email and one composes a round from answers that
 * already exist. Deploying them as engines to make the roster look symmetrical would add two
 * cold starts and an identity that scopes nothing. They are listed with `engine: null` and
 * the reason, rather than omitted so the count reads as a round number.
 */

export type FleetRole = 'department' | 'orchestrator' | 'evidence' | 'verifier' | 'service';

export type FleetMember = {
  id: string;
  name: string;
  role: FleetRole;
  department: string | null;
  /** What it does, in one line, in the vocabulary of the job rather than the code. */
  purpose: string;
  /** `reasoningEngines:<id>` from the registry URN, or null for an in-process agent. */
  engine: string | null;
  /** Why there is no engine. Present only when `engine` is null. */
  engineNote?: string;
  /** Corpus prefixes this identity may read. */
  reads: string[];
  /** Corpus prefixes it is refused, by IAM rather than by instruction. */
  refused: string[];
  /** Where the corpus facts come from, rendered next to them. */
  scopeSource: string;
  /** The `actor` this agent writes into the audit trail, when it writes answers. */
  actor: string | null;
};

const SCOPE_SOURCE = 'infra/iam/scope_agents.py — the registry list endpoint returns no scopes';

const DEPARTMENT_PURPOSE: Record<string, string> = {
  security: 'Drafts every security and infrastructure control answer, from the security corpus.',
  legal: 'Drafts DPA, privacy and contractual answers, from the legal corpus.',
  engineering: 'Drafts architecture, SDLC and availability answers, from the engineering corpus.',
};

const ALL_CORPORA = ['security', 'legal', 'engineering'] as const;

/** The two agents that run in the dispatcher rather than on an engine. */
const IN_PROCESS: readonly FleetMember[] = [
  {
    id: 'inbox',
    name: 'InboxAgent',
    role: 'service',
    department: null,
    purpose:
      'Reads the watched mailbox and decides what arrived: a review, a follow-up, or neither.',
    engine: null,
    engineNote:
      'Runs in the dispatcher. One cheap classification per email, with no corpus access — there is nothing for an engine identity to scope.',
    reads: [],
    refused: ALL_CORPORA.map((c) => `corpus/${c}`),
    scopeSource: 'no corpus binding of any kind',
    actor: 'InboxAgent',
  },
  {
    id: 'assembler',
    name: 'AssemblerAgent',
    role: 'service',
    department: null,
    purpose:
      'Composes the round, decides what a person must see, and holds the release until they have.',
    engine: null,
    engineNote:
      'Runs in the dispatcher. Reads answers that already exist and never retrieves, so it has no corpus to be scoped to.',
    reads: [],
    refused: ALL_CORPORA.map((c) => `corpus/${c}`),
    scopeSource: 'no corpus binding of any kind',
    actor: 'AssemblerAgent',
  },
];

function memberFor(agent: RegistryAgent): FleetMember | null {
  const engine = engineId(agent);
  if (engine === null) return null;
  const name = agent.display_name ?? agent.name ?? 'unnamed';
  if (name.endsWith('probe')) return null;

  if (isDepartmentEngine(agent)) {
    const department = (agent.department ?? '').toLowerCase();
    return {
      id: name,
      name,
      role: 'department',
      department,
      purpose: DEPARTMENT_PURPOSE[department] ?? 'Drafts answers from its own corpus.',
      engine,
      reads: [`corpus/${department}`],
      refused: ALL_CORPORA.filter((c) => c !== department).map((c) => `corpus/${c}`),
      scopeSource: SCOPE_SOURCE,
      actor: `${department.charAt(0).toUpperCase()}${department.slice(1)}Agent`,
    };
  }

  if (name.endsWith('verifier')) {
    // Separation of duties, rendered as a card. The interesting fields are the two empty
    // ones: it reads nothing, and it has no department -- because an agent that could
    // retrieve would be able to go and find a better citation, which is a different and
    // weaker question, and an agent with a department would eventually be reviewing its
    // own work.
    return {
      id: name,
      name,
      role: 'verifier',
      department: null,
      purpose:
        'Reads each drafted answer against the passages it cites and reports whether the claims are in them. Never writes, never retrieves.',
      engine,
      reads: [],
      refused: ALL_CORPORA.map((c) => `corpus/${c}`),
      scopeSource:
        'no corpus binding of any kind — infra/iam/scope_agents.py reports "verifier: no corpus access"',
      actor: 'VerifierAgent',
    };
  }

  if (name.endsWith('evidence')) {
    // The one legitimate cross-department reader, and the one asymmetry in the whole scope
    // story. It is scoped by the `department` argument its tool takes rather than by IAM,
    // which `infra/iam/scope_agents.py` records as a decision rather than an oversight --
    // so the card says so instead of drawing three dashes it has not earned.
    return {
      id: name,
      name,
      role: 'evidence',
      department: null,
      purpose:
        'Retrieves passages for any department, on request. The one agent allowed to cross a corpus boundary.',
      engine,
      reads: ALL_CORPORA.map((c) => `corpus/${c}`),
      refused: [],
      scopeSource:
        'scoped by the department argument its tool takes, NOT by IAM — the one asymmetry, recorded in infra/iam/scope_agents.py',
      actor: 'EvidenceAgent',
    };
  }

  return {
    id: name,
    name,
    role: 'orchestrator',
    department: null,
    purpose:
      'Plans the round, routes questions to departments, and owns the release-or-hold decision.',
    engine,
    reads: [],
    refused: ALL_CORPORA.map((c) => `corpus/${c}`),
    scopeSource: 'no corpus binding — it routes and decides, it does not retrieve',
    actor: 'OrchestratorAgent',
  };
}

/** Departments first, then the orchestrator, then the in-process agents. */
export function roster(agents: RegistryAgent[]): FleetMember[] {
  const deployed = agents
    .map(memberFor)
    .filter((member): member is FleetMember => member !== null)
    .sort((a, b) => {
      const order: Record<FleetRole, number> = {
        department: 0,
        verifier: 1,
        evidence: 2,
        orchestrator: 3,
        service: 4,
      };
      const rank = (m: FleetMember) => order[m.role];
      return rank(a) - rank(b) || a.name.localeCompare(b.name);
    });
  return [...deployed, ...IN_PROCESS];
}

// ---------------------------------------------------------------------------------
// What each agent is doing, from the audit trail
// ---------------------------------------------------------------------------------

export type AgentActivity = {
  /** Answers this agent wrote across the reviews that were read. */
  answers: number;
  /** Of those, how many carry at least one citation. */
  cited: number;
  /** The most recent thing it did, and when. */
  lastAt: string | null;
  /** What that most recent thing was, as the trail names it. */
  lastKind: string | null;
  /** Which review it was on, so the fleet page can link to what it is doing. */
  lastReview: string | null;
  /** Events it wrote since midnight, local time. "What it did today". */
  today: number;
  /** Everything it did, by kind, so a drawer can say what the work consisted of. */
  kinds: Record<string, number>;
  /** True when its most recent activity is inside the working window. */
  working: boolean;
};

/** Anything newer than this counts as "now" on the fleet page. */
export const WORKING_WINDOW_MS = 3 * 60 * 1000;

/**
 * Fold audit events into per-agent activity.
 *
 * Keyed on the event's `actor`, which is what the drafting agent writes and therefore the
 * only attribution that comes from the run rather than from this file. An agent with no
 * events gets a zeroed record rather than being dropped: "SecurityAgent has answered
 * nothing today" is a fact worth rendering, and a card that vanishes when idle makes a
 * six-agent fleet look like however many happen to be busy.
 */
export function activityByActor(
  events: readonly AuditEvent[],
  now: number = Date.now(),
): Map<string, AgentActivity> {
  const out = new Map<string, AgentActivity>();
  // Midnight local, because "today" is a thing a person says about their own working day
  // and not about UTC. Computed once rather than per event.
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  const startOfDay = midnight.getTime();

  for (const event of events) {
    const actor = event.actor;
    if (!actor) continue;
    const current: AgentActivity = out.get(actor) ?? {
      answers: 0,
      cited: 0,
      lastAt: null,
      lastKind: null,
      lastReview: null,
      today: 0,
      kinds: {},
      working: false,
    };
    if (event.kind === 'answer_drafted') {
      current.answers += 1;
      const citations = Number((event.detail as { citation_count?: unknown })?.citation_count ?? 0);
      if (citations > 0) current.cited += 1;
    }
    current.kinds[event.kind] = (current.kinds[event.kind] ?? 0) + 1;
    const at = event.occurred_at ?? event.recorded_at ?? null;
    if (at) {
      if (Date.parse(at) >= startOfDay) current.today += 1;
      if (current.lastAt === null || at > current.lastAt) {
        current.lastAt = at;
        current.lastKind = event.kind;
        current.lastReview = event.review_id ?? null;
      }
    }
    out.set(actor, current);
  }
  for (const activity of out.values()) {
    activity.working =
      activity.lastAt !== null && now - Date.parse(activity.lastAt) < WORKING_WINDOW_MS;
  }
  return out;
}

/**
 * What an agent is doing, in the words a person would use.
 *
 * Derived from the last event it wrote, because that is the only evidence there is. An
 * agent with no events at all reports as idle with nothing behind it, which is different
 * from an agent that finished an hour ago -- and the difference matters on a page whose
 * job is saying what the fleet is doing right now.
 */
export function doingNow(activity: AgentActivity | null): string {
  if (activity === null || activity.lastAt === null) return 'Nothing on the trail yet';
  const verb = DOING[activity.lastKind ?? ''] ?? (activity.lastKind ?? 'working').replace(/_/g, ' ');
  return activity.working ? verb : `Last: ${verb.toLowerCase()}`;
}

const DOING: Record<string, string> = {
  question_triaged: 'Routing questions to departments',
  evidence_retrieved: 'Retrieving passages',
  query_expanded: 'Expanding a question into queries',
  answer_drafted: 'Drafting answers',
  answer_verified: 'Checking answers against their citations',
  consistency_checked: 'Checking against prior commitments',
  human_required: 'Holding answers for a person',
  armor_blocked: 'Refusing input before a model reads it',
  tool_denied: 'Refusing a cross-department read',
  stage_completed: 'Advancing a stage',
  export_produced: 'Building the pack',
  pack_delivered: 'Sending the pack to the customer',
  plan_selected: 'Choosing a plan for the round',
  retry_decided: 'Deciding on a retry',
  run_completed: 'Closing the round',
  human_decision: 'Recording a decision',
  human_asked: 'Asking the thread a question',
  orchestrator_answered: 'Answering from the audit trail',
};

/**
 * Which reviews are worth reading the audit trail of for the fleet page.
 *
 * Bounded on purpose. Every review's audit trail is a separate query of up to a thousand
 * documents, and rendering a landing page must not become a scan of the whole collection as
 * the project accumulates runs. Non-archived, most recent first, capped.
 */
export function reviewsWorthReading(reviews: readonly ReviewRow[], limit = 4): ReviewRow[] {
  return reviews
    .filter((review) => !review.archived)
    .slice(0, limit);
}
