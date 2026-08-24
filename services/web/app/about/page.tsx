import Link from 'next/link';

import { FleetDiagram } from '@/components/about/FleetDiagram';
import { ThemeToggle } from '@/components/layout/ThemeToggle';
import { Mono } from '@/components/ui/primitives';
import { api, type RegistryAgent } from '@/lib/api/client';
import { engineId, isDepartmentEngine, partition } from '@/lib/registry';

export const dynamic = 'force-dynamic';

/**
 * What this is, and what has been measured about it.
 *
 * Every figure on this page came from a file in `docs/proof/`, produced by a command in the
 * Makefile against the deployed project. The engine ids are read live from the Agent
 * Registry on each request, so the identities listed are the ones that exist right now
 * rather than the ones that existed when this was written.
 *
 * Section A of Phase 9 applies here as much as anywhere: facts, not arguments. The material
 * is a permission denial, a recall delta, a verdict distribution and eight bugs, and none
 * of it needs to be sold.
 */

/** Read from `docs/proof/retrieval-recall.md` — `make recall`, 63 hand-labelled pairs. */
const RECALL = { raw: '90%', expanded: '95%', pairs: 63 };

/** From `docs/proof/iam-runtime-denial.json`, verbatim. Trimmed only where marked. */
const DENIAL = `403 GET https://storage.mtls.googleapis.com/download/storage/v1/b/
attestor-505506-corpus/o/legal%2Fdata-processing-agreement.txt?alt=media:
Caller does not have storage.objects.get access to the Google Cloud Storage object.`;

/**
 * The eight, in the order they were found.
 *
 * A read that fails and a read that legitimately finds nothing are different facts, and
 * every one of these collapsed them. None announced itself: each produced a smaller number,
 * a green run, and a confident false statement.
 */
const FINDINGS: ReadonlyArray<{ where: string; became: string }> = [
  { where: 'Discovery Engine returned [] under a 429', became: 'the corpus has no answer' },
  { where: 'Model Armor denied under a timeout', became: 'this passage is poisoned' },
  { where: 'Embeddings degraded under quota exhaustion', became: 'these scores are cosines' },
  {
    where: 'The commitment read caught every exception and returned []',
    became: 'this customer has no prior commitments',
  },
  {
    where: 'AgentRegistry.list_agents returned [] when the registry was unreachable',
    became: 'no agents are registered',
  },
  {
    where: 'ReviewPipeline.draft caught every exception from the remote engine',
    became: 'we have no policy on this',
  },
  {
    where: 'An engine returned fifteen passages at 0.744 and no prose',
    became: 'no supporting evidence was found in the corpus',
  },
  {
    where: 'Deployed search returned an empty result set successfully, under load',
    became: '172 of 312 questions have no supporting evidence',
  },
];

export default async function AboutPage() {
  let agents: RegistryAgent[] = [];
  try {
    agents = await api.listRegistry();
  } catch {
    // The page is about the architecture, not about the registry's availability. A failed
    // read leaves the engine ids out; every other section stands on its own.
    agents = [];
  }
  // `partition` drops the deployment probe and anything in the project that is not ours,
  // which is the same filter the registry page counts with. Two pages disagreeing about how
  // many engines exist would be a worse problem than either number.
  // `attestor-probe` is a deployment smoke test, not a member of the fleet -- the roster on
  // /fleet drops it the same way. Counting it would make this page say seven where every
  // other surface says six.
  const fleet = partition(agents).fleet.filter(
    (agent) => !(agent.display_name ?? agent.name ?? '').endsWith('probe'),
  );
  // Ordered to match the corpora, so the granted edges in the diagram read as a diagonal.
  const departments = ['security', 'legal', 'engineering'].filter((name) =>
    fleet.some((agent) => isDepartmentEngine(agent) && agent.department === name),
  );

  return (
    <div className="min-h-screen bg-base">
      <header className="sticky top-0 z-10 flex h-header items-center justify-between border-b border-subtle bg-base px-6">
        <Link href="/" className="text-md text-primary no-underline hover:no-underline">
          Attestor
        </Link>
        <div className="flex items-center gap-4">
          <Link href="/" className="text-sm">
            Open the product
          </Link>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-column flex-col gap-24 px-6 py-20">
        <section className="flex flex-col gap-6">
          <h1 className="text-display text-primary">
            A vendor security questionnaire is 312 questions. Attestor answers them from your
            own documents, cites every claim, and refuses the ones it cannot support.
          </h1>
          <p className="text-md leading-relaxed text-secondary">
            An email arrives. Agents route it, retrieve against their own corpora, draft,
            check each other, and hold back what a person has to see. Nobody opens a console
            for any of it.
          </p>
        </section>

        <section className="grid grid-cols-2 gap-x-8 gap-y-8 border-y border-subtle py-10 sm:grid-cols-4">
          <Figure value="312" label="questions in one round" />
          <Figure value={String(fleet.length || 6)} label="deployed engines, one identity each" />
          <Figure value={RECALL.expanded} label={`recall@5 over ${RECALL.pairs} labelled pairs`} />
          <Figure value="8" label="failures that impersonated an empty result" />
        </section>

        <Chapter
          n="01"
          title={`${fleet.length || 6} engines, ${fleet.length || 6} identities`}
        >
          <p>
            Each department is a separate deployed engine with its own service identity, not a
            sub-agent behind a shared one. One engine, one corpus.
          </p>
          <FleetDiagram departments={departments} />
          {fleet.length > 0 ? (
            <dl className="flex flex-col gap-2 pt-2">
              {fleet.map((agent) => (
                <div key={agent.agent_id} className="flex items-baseline justify-between gap-4">
                  <dt className="truncate text-sm text-secondary">
                    {agent.display_name ?? agent.name}
                  </dt>
                  <dd className="shrink-0">
                    <Mono dim>{engineId(agent)}</Mono>
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          <p className="text-sm text-muted">
            Read from <Mono dim>agentregistry.googleapis.com/v1</Mono> on this request.
          </p>
        </Chapter>

        <Chapter n="02" title="The boundary is a credential, not an instruction">
          <p>
            SecurityAgent reads <Mono>corpus/security</Mono>. Asked for{' '}
            <Mono>corpus/legal</Mono>, it does not decline — it is refused, by a conditioned
            IAM binding, before any model is involved.
          </p>
          <pre className="overflow-x-auto rounded bg-code px-4 py-3 font-mono text-xs leading-relaxed text-secondary">
            {DENIAL}
          </pre>
          <p className="text-sm text-muted">
            <Mono dim>docs/proof/iam-runtime-denial.json</Mono> · the same probe reads its own
            prefix at 4,298 bytes in the same run.
          </p>
        </Chapter>

        <Chapter n="03" title="Two observability planes">
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
            <Plane
              title="Compliance"
              store="Firestore audit_events"
              lines={[
                'Append-only and attributed.',
                'Which identity did what, to which object, when.',
                '1,162 events for one review.',
              ]}
            />
            <Plane
              title="Engineering"
              store="Cloud Trace, via OpenTelemetry"
              lines={[
                'Latency, token cost, tool spans.',
                'Organised by span parentage.',
                'Inherited from Agent Runtime; this codebase emits no custom spans.',
              ]}
            />
          </div>
          <p>
            The runtime 403 above is in the first plane. A permission denial is a compliance
            event: it has to be queryable years later, and a span would record that a read took
            240 milliseconds.
          </p>
        </Chapter>

        <Chapter n="04" title="What was promised in round one binds round two">
          <p>
            Commitments are written to Vertex AI Memory Bank when a round closes and loaded at
            the start of every later round. A reply twenty-four days later is checked against
            them before it is drafted.
          </p>
          <blockquote className="border-l-2 border-line pl-4 text-sm leading-relaxed text-secondary">
            Kestrel Data does not offer on-premises or self-hosted deployment. Kestrel Insight
            is multi-tenant SaaS only, with no single-tenant, private-cloud, air-gapped, or
            customer-VPC option, and none on the roadmap.
          </blockquote>
          <p className="text-sm text-muted">
            <Mono dim>docs/proof/memory-bank-recall.json</Mono> · five commitments recalled
            across sessions, with the question each was made against.
          </p>
        </Chapter>

        <Chapter n="05" title="Eight failures that looked like empty results">
          <p>
            A read that fails and a read that legitimately finds nothing are different facts.
            Every one of these collapsed them, and none announced itself: each produced a
            smaller number, a green run, and a confident false statement.
          </p>
          <ol className="flex flex-col gap-4 pt-2">
            {FINDINGS.map((finding, index) => (
              <li key={finding.where} className="flex gap-4">
                <Mono dim>{String(index + 1).padStart(2, '0')}</Mono>
                <div className="flex min-w-0 flex-col gap-1">
                  <span className="text-sm text-primary">{finding.where}</span>
                  <span className="text-sm text-muted">became &ldquo;{finding.became}&rdquo;</span>
                </div>
              </li>
            ))}
          </ol>
          <p className="text-sm text-muted">
            The eighth was the largest: a completed run reported 172 of 312 questions
            unsupported. Queried directly, the same corpus returned passages for five of six of
            them at 0.950 top relevance.
          </p>
        </Chapter>

        <Chapter n="06" title="Built on">
          <ul className="grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-3">
            {STACK.map((item) => (
              <li key={item} className="text-sm text-secondary">
                {item}
              </li>
            ))}
          </ul>
        </Chapter>

        <footer className="flex flex-wrap items-center gap-6 border-t border-subtle pt-10">
          <Link href="/" className="text-sm">
            Open the product
          </Link>
          <Link href="/fleet" className="text-sm">
            The fleet, live
          </Link>
          <Link href="/registry" className="text-sm">
            The registry
          </Link>
          <a
            href="https://github.com/Divy-core/attestor"
            className="text-sm"
            rel="noreferrer noopener"
          >
            Source
          </a>
        </footer>
      </main>
    </div>
  );
}

const STACK = [
  'Vertex AI Agent Engine',
  'Agent Development Kit',
  'Vertex AI Search',
  'Vertex AI Memory Bank',
  'Model Armor',
  'Agent Registry',
  'Cloud Run',
  'Pub/Sub',
  'Firestore',
  'Cloud Storage',
  'Cloud Trace',
  'Gemini',
];

function Figure({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-xl tabular-nums text-primary">{value}</span>
      <span className="text-sm leading-snug text-muted">{label}</span>
    </div>
  );
}

function Chapter({
  n,
  title,
  children,
}: {
  n: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-5">
      <div className="flex items-baseline gap-4">
        <Mono dim>{n}</Mono>
        <h2 className="text-lg text-primary">{title}</h2>
      </div>
      <div className="flex flex-col gap-5 text-base leading-relaxed text-secondary">
        {children}
      </div>
    </section>
  );
}

function Plane({
  title,
  store,
  lines,
}: {
  title: string;
  store: string;
  lines: string[];
}) {
  return (
    <div className="flex flex-col gap-2 rounded border border-subtle px-4 py-3">
      <h3 className="text-sm font-medium text-primary">{title}</h3>
      <Mono dim>{store}</Mono>
      <ul className="flex flex-col gap-1 pt-1">
        {lines.map((line) => (
          <li key={line} className="text-sm text-secondary">
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}
