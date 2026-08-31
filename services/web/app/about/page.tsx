import Link from 'next/link';

import { FleetDiagram } from '@/components/about/FleetDiagram';
import { ThemeToggle } from '@/components/layout/ThemeToggle';
import { Mono } from '@/components/ui/primitives';
import { api, type RegistryAgent } from '@/lib/api/client';
import { engineId, isDepartmentEngine, partition } from '@/lib/registry';

export const dynamic = 'force-dynamic';

/**
 * The page for someone who has never heard of this.
 *
 * The first version was PROGRESS.md with a stylesheet: engine resource ids, `docs/proof/`
 * paths, raw 403 bodies and recall@5 over 63 labelled pairs, in six numbered chapters of
 * identical width and rhythm. All of it true, none of it legible to a founder or a
 * compliance owner, who are the two people this product is for.
 *
 * The engineering material is not gone -- it is in ONE section, near the end, for the reader
 * who wants it. Everything before that is the problem, the product, the reason to trust it,
 * and the thing it refuses to do. The engine ids in that section are still read live from
 * the Agent Registry on each request, so the page cannot drift from what is deployed.
 *
 * Section A of Phase 9 applies here as much as anywhere: facts, not arguments.
 */

/** From `docs/proof/iam-runtime-denial.json`, verbatim. Line breaks added to fit the column. */
const DENIAL = `403 GET https://storage.mtls.googleapis.com/download/storage/v1/b/
attestor-505506-corpus/o/legal%2Fdata-processing-agreement.txt?alt=media:
Caller does not have storage.objects.get access to the Google Cloud Storage object.`;

/**
 * The nine, in the order they were found.
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
  {
    where: "A session-store quota dropped the engine's tool response mid-stream",
    became: '77 of 150 questions have no supporting evidence',
  },
];

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

export default async function AboutPage() {
  let agents: RegistryAgent[] = [];
  try {
    agents = await api.listRegistry();
  } catch {
    // The page is about the architecture, not about the registry's availability. A failed
    // read leaves the engine ids out; every other section stands on its own.
    agents = [];
  }
  // The same filter /fleet counts with, minus the deployment probe. Two pages disagreeing
  // about how many engines exist would be a worse problem than either number.
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

      <main>
        {/* The statement, and nothing else in the viewport with it. */}
        <section className="mx-auto flex min-h-[78vh] w-full max-w-essay flex-col justify-center px-6 py-24">
          <h1 className="max-w-[16ch] text-hero text-primary">
            A questionnaire is in the way of the deal.
          </h1>
          <p className="mt-8 max-w-prose text-md leading-relaxed text-secondary">
            Every enterprise customer sends one before they will sign. Hundreds of questions
            about how you handle their data, and the answers are already written down &mdash;
            scattered across policies, architecture notes and last year&rsquo;s audit. Someone
            senior spends two weeks finding them again.
          </p>
        </section>

        {/* The argument, step for step. Nothing on this page is worth more than this. */}
        <section className="border-y border-subtle bg-sunken">
          <div className="mx-auto w-full max-w-essay px-6 py-32">
            <h2 className="max-w-[18ch] text-display leading-tight text-primary">
              The same work, twice.
            </h2>
            <div className="mt-16 grid gap-x-16 gap-y-10 md:grid-cols-2">
              <div>
                <p className="text-sm uppercase tracking-wider text-muted">A person, three weeks</p>
                <ol className="mt-6 flex flex-col gap-4 text-base leading-relaxed text-secondary">
                  <li>Opens the spreadsheet. Two hundred rows.</li>
                  <li>Searches the policy drive for each one.</li>
                  <li>Asks engineering about the ones nobody wrote down.</li>
                  <li>Checks what was said to this customer last quarter.</li>
                  <li>Writes an answer. Cannot remember which document it came from.</li>
                  <li>Sends it, and hopes nothing contradicts anything.</li>
                </ol>
              </div>
              <div>
                <p className="text-sm uppercase tracking-wider text-primary">
                  The fleet, thirteen minutes
                </p>
                <ol className="mt-6 flex flex-col gap-4 text-base leading-relaxed text-secondary">
                  <li>Reads the email nobody opened.</li>
                  <li>Routes every question to the team that owns it.</li>
                  <li>Answers from your documents, citing the section.</li>
                  <li>Recalls what this customer was told before.</li>
                  <li>Has a second agent check every claim against its passages.</li>
                  <li>Refuses what it cannot support, and hands you those.</li>
                </ol>
              </div>
            </div>
            <p className="mt-16 max-w-prose text-md leading-relaxed text-secondary">
              Nobody finds the same document twice.
            </p>
          </div>
        </section>

        {/* Both ends of the loop, which is the part that makes it a fleet. */}
        <section className="mx-auto w-full max-w-essay px-6 py-32">
          <h2 className="max-w-[22ch] text-display leading-tight text-primary">
            It closes the loop, not just the middle.
          </h2>
          <ol className="mt-16 flex flex-col gap-10">
            {[
              ['A customer emails a questionnaire.', 'Nobody forwards it. Nobody clicks anything.'],
              ['The fleet answers it.', 'Ten agents, seven identities, each bound to one corpus.'],
              ['A second identity checks the work.', 'It is handed the passages and asked whether they carry the claim.'],
              ['You approve what it held.', 'One action. Every answer still recorded separately, under your name.'],
              ['The reply goes back on their thread.', 'Their workbook, their rows, with the evidence pack attached.'],
            ].map(([title, body], index) => (
              <li key={title} className="flex gap-6">
                <span className="w-8 shrink-0 font-mono text-md text-muted">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <div>
                  <h3 className="text-md text-primary">{title}</h3>
                  <p className="mt-2 max-w-prose text-base leading-relaxed text-secondary">{body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* The boxes are real, and here they are. */}
        <section className="border-y border-subtle bg-sunken">
          <div className="mx-auto w-full max-w-[1400px] px-6 py-32">
            <h2 className="max-w-[24ch] text-display leading-tight text-primary">
              Every box below is running.
            </h2>
            <p className="mt-6 max-w-prose text-md leading-relaxed text-secondary">
              Seven agent identities, three department corpora, two observability planes, and a
              guardrail on both directions.
            </p>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/architecture.svg"
              alt="A customer email becomes a Pub/Sub work envelope; a Cloud Run dispatcher fans it out to seven Agent Runtime engines under separate identities, each department engine bound to its own Vertex AI Search datastore, with Firestore and Memory Bank for state, Model Armor on both directions, and two observability planes."
              className="mt-16 w-full"
            />
          </div>
        </section>

        {/* One line, alone, at a size nothing else reaches. The turn from problem to product. */}
        <section className="border-y border-subtle">
          <div className="mx-auto w-full max-w-essay px-6 py-32">
            <p className="max-w-[22ch] text-display leading-tight text-primary">
              Attestor answers it from your own documents, and shows its working.
            </p>
          </div>
        </section>

        {/* Three things, wide, no boxes. Type and space do the separating. */}
        <section className="mx-auto grid w-full max-w-essay gap-x-12 gap-y-16 px-6 py-32 sm:grid-cols-3">
          <Step
            title="It reads what you already have"
            body="Policies, architecture notes, audit reports, contracts. Nothing is rewritten for it and nothing is filled in by hand."
          />
          <Step
            title="Every claim cites its document"
            body="Each answer names the document and the section it came from, so whoever signs it can check it in seconds rather than trust it."
          />
          <Step
            title="It sends the file back"
            body="Answers land in the customer's own spreadsheet, in their rows, beside their columns. Not a report somebody has to reconcile."
          />
        </section>

        {/* The figures. Two, both meaningful to someone who has never seen this. */}
        <section className="border-y border-subtle bg-raised">
          <div className="mx-auto grid w-full max-w-essay gap-12 px-6 py-24 sm:grid-cols-2">
            <Figure value="Two weeks" label="what answering one of these takes today" />
            <Figure
              value="Thirteen minutes"
              label="a 150-question round, upload to finished draft, measured"
            />
          </div>
        </section>

        {/* Two columns: the trust claim, and the picture of it. */}
        <section className="mx-auto grid w-full max-w-essay items-start gap-x-16 gap-y-12 px-6 py-32 lg:grid-cols-[1fr_1.1fr]">
          <div className="flex flex-col gap-6">
            <h2 className="text-lg text-primary">Why you can check it</h2>
            <p className="text-md leading-relaxed text-secondary">
              Your security policies and your commercial contracts are answered by different
              agents, and each one can only open its own documents. Not by instruction &mdash;
              the credential itself does not carry the permission, so the refusal happens
              before any model is involved.
            </p>
            <p className="text-md leading-relaxed text-secondary">
              Then a separate agent, on separate credentials, reads each drafted answer
              against the passages it cites and says whether they hold it up. It is refused a
              verdict on its own work.
            </p>
          </div>
          <FleetDiagram departments={departments} />
        </section>

        {/* The best property, given the most air on the page. */}
        <section className="border-t border-subtle">
          <div className="mx-auto flex w-full max-w-essay flex-col gap-8 px-6 py-32">
            <h2 className="max-w-[20ch] text-display leading-tight text-primary">
              It will not answer what your documents do not say.
            </h2>
            <p className="max-w-prose text-md leading-relaxed text-secondary">
              When the evidence is not there, the answer is not written. The question comes
              back marked, with the gap named, for a person to decide. The same happens to
              anything it drafted but would not stand behind.
            </p>
            <p className="max-w-prose text-md leading-relaxed text-secondary">
              It never reaches the web to answer a question about you. An agent that looks up
              &ldquo;do you encrypt at rest&rdquo; on the open internet is guessing about its
              own employer, fluently, with a citation that makes the guess look sound.
            </p>
          </div>
        </section>

        {/* Everything an engineer or a judge would want, in one place rather than throughout. */}
        <section className="border-t border-subtle bg-raised">
          <div className="mx-auto flex w-full max-w-essay flex-col gap-16 px-6 py-24">
            <div className="flex flex-col gap-3">
              <Mono dim>For the reader who wants the architecture</Mono>
              <h2 className="text-lg text-primary">How it is actually built</h2>
            </div>

            <div className="grid gap-x-16 gap-y-12 lg:grid-cols-2">
              <Technical title={`${fleet.length || 6} engines, ${fleet.length || 6} identities`}>
                <p>
                  Each department is a separately deployed reasoning engine with its own
                  service identity, not a sub-agent behind a shared one.
                </p>
                {fleet.length > 0 ? (
                  <dl className="flex flex-col gap-2 pt-1">
                    {fleet.map((agent) => (
                      <div
                        key={agent.agent_id}
                        className="flex items-baseline justify-between gap-4"
                      >
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
              </Technical>

              <Technical title="The boundary is a credential">
                <p>
                  SecurityAgent reads <Mono>corpus/security</Mono>. Asked for{' '}
                  <Mono>corpus/legal</Mono>, it is refused by a conditioned IAM binding:
                </p>
                <pre className="overflow-x-auto rounded bg-code px-4 py-3 font-mono text-xs leading-relaxed text-secondary">
                  {DENIAL}
                </pre>
                <p className="text-sm text-muted">
                  <Mono dim>docs/proof/iam-runtime-denial.json</Mono> &middot; the same probe
                  reads its own prefix at 4,298 bytes in the same run.
                </p>
              </Technical>

              <Technical title="Two observability planes">
                <p>
                  Firestore <Mono>audit_events</Mono> is append-only and attributed: which
                  identity did what, to which object, when. Cloud Trace carries latency, token
                  cost and tool spans, inherited from Agent Runtime.
                </p>
                <p>
                  The 403 above is written to the first. A permission denial has to be
                  queryable years later, and a span would record that a read took 240
                  milliseconds.
                </p>
              </Technical>

              <Technical title="Round two knows what round one promised">
                <p>
                  Commitments are written to Vertex AI Memory Bank when a round closes and
                  loaded before any later round is drafted. Twenty-four days later, a reply is
                  checked against them first:
                </p>
                <blockquote className="border-l-2 border-line pl-4 text-sm leading-relaxed text-secondary">
                  Kestrel Data does not offer on-premises or self-hosted deployment. Kestrel
                  Insight is multi-tenant SaaS only, with no single-tenant, private-cloud,
                  air-gapped, or customer-VPC option, and none on the roadmap.
                </blockquote>
              </Technical>
            </div>

            <div className="flex flex-col gap-6 border-t border-subtle pt-12">
              <h3 className="text-md text-primary">
                Nine failures that arrived disguised as an empty result
              </h3>
              <p className="max-w-prose text-base leading-relaxed text-secondary">
                A read that fails and a read that legitimately finds nothing are different
                facts. Every one of these collapsed them, and none announced itself: each
                produced a smaller number, a green run, and a confident false statement.
              </p>
              <ol className="grid gap-x-12 gap-y-4 pt-2 lg:grid-cols-2">
                {FINDINGS.map((finding, index) => (
                  <li key={finding.where} className="flex gap-4">
                    <Mono dim>{String(index + 1).padStart(2, '0')}</Mono>
                    <div className="flex min-w-0 flex-col gap-1">
                      <span className="text-sm text-primary">{finding.where}</span>
                      <span className="text-sm text-muted">
                        became &ldquo;{finding.became}&rdquo;
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            </div>

            <div className="flex flex-col gap-4 border-t border-subtle pt-12">
              <h3 className="text-md text-primary">Built on</h3>
              <ul className="grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-4">
                {STACK.map((item) => (
                  <li key={item} className="text-sm text-secondary">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* One action. */}
        <section className="mx-auto flex w-full max-w-essay flex-col items-start gap-8 px-6 py-32">
          <p className="max-w-[18ch] text-display leading-tight text-primary">
            Send it a questionnaire.
          </p>
          <Link href="/" className="text-md">
            Open Attestor
          </Link>
        </section>

        <footer className="border-t border-subtle">
          <div className="mx-auto flex w-full max-w-essay flex-wrap items-center gap-6 px-6 py-10">
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
          </div>
        </footer>
      </main>
    </div>
  );
}

function Step({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-md text-primary">{title}</h2>
      <p className="text-base leading-relaxed text-secondary">{body}</p>
    </div>
  );
}

function Figure({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex flex-col gap-3">
      <span className="text-display leading-none text-primary">{value}</span>
      <span className="text-md leading-snug text-muted">{label}</span>
    </div>
  );
}

function Technical({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <h3 className="text-md text-primary">{title}</h3>
      <div className="flex flex-col gap-4 text-base leading-relaxed text-secondary">
        {children}
      </div>
    </section>
  );
}
