import { Mono } from '@/components/ui/primitives';
import { engineId, isDepartmentEngine } from '@/lib/registry';
import type { RegistryAgent } from '@/lib/api/client';

/**
 * Least privilege, at a glance.
 *
 * This grid is the strongest architectural sentence in the project rendered as a picture: the
 * department engines each have their own Agent Identity and each read exactly one corpus. The
 * alternative design — one engine with nested `sub_agents` — would have put all three departments
 * behind a single identity, which means one service account holding the union of every
 * department's permissions. That is precisely the least-privilege violation the fleet exists to
 * avoid, and it is why this is separate engines rather than one.
 *
 * A dot means "this identity can read this corpus". A dash means it is refused — and refused by
 * IAM, not by a prompt asking it nicely. `docs/proof/iam-runtime-denial.json` has the platform's
 * own words for one of those dashes.
 *
 * ## The dashes are the content
 *
 * A permission matrix where every cell is filled proves nothing. What makes this worth a page is
 * that most cells are empty, so the shape reads as a diagonal rather than a block. Refusals are
 * rendered as a visible dash rather than as whitespace, because a blank cell is ambiguous between
 * "denied" and "not applicable" and those are different claims.
 *
 * ## The engine column shows the engine
 *
 * Not `resource_name`, which is the registry's own bookkeeping id and merely looks like an engine
 * path. The `reasoningEngine` id comes out of the `agent_id` URN — it is the value the IAM
 * bindings are written against and the one `docs/proof/fleet-deployment.json` records, so it is
 * the only one a reader can check anything with.
 */

const CORPORA = ['security', 'legal', 'engineering'] as const;

export function ScopeMatrix({ agents }: { agents: RegistryAgent[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse text-sm">
        <caption className="px-4 pb-2 text-left text-xs text-secondary">
          A dot is a granted read. A dash is a refusal, enforced by the credential rather than by
          an instruction.
        </caption>
        <thead>
          <tr className="border-b border-subtle text-xs text-muted">
            <th scope="col" className="px-4 py-2 text-left font-normal">
              Agent
            </th>
            {CORPORA.map((corpus) => (
              <th key={corpus} scope="col" className="px-4 py-2 text-left font-normal">
                corpus/{corpus}
              </th>
            ))}
            <th scope="col" className="px-4 py-2 text-left font-normal">
              reasoningEngine
            </th>
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => {
            const department = (agent.department ?? '').toLowerCase();
            const scoped = isDepartmentEngine(agent);
            const engine = engineId(agent);
            return (
              <tr key={agent.agent_id} className="border-b border-subtle last:border-b-0">
                <th scope="row" className="px-4 py-2 text-left font-normal">
                  <span className="text-primary">
                    {agent.display_name ?? agent.name ?? 'unnamed'}
                  </span>
                </th>
                {CORPORA.map((corpus) => (
                  <td key={corpus} className="px-4 py-2">
                    {scoped && department === corpus ? (
                      <span className="inline-flex items-center gap-2">
                        <span aria-hidden className="inline-block h-2 w-2 rounded-sm bg-cited" />
                        <span className="text-xs text-secondary">read</span>
                      </span>
                    ) : scoped ? (
                      <span
                        className="text-xs text-muted"
                        title="Refused by IAM. The credential does not carry this permission."
                      >
                        &mdash; denied
                      </span>
                    ) : (
                      <span
                        className="text-xs text-muted"
                        title="Holds no corpus binding of its own. Cross-department retrieval asks each scoped agent in turn rather than holding the union of their permissions."
                      >
                        no corpus
                      </span>
                    )}
                  </td>
                ))}
                <td className="px-4 py-2">
                  {engine === null ? (
                    <span className="text-xs text-muted">not an engine</span>
                  ) : (
                    <Mono dim title={agent.agent_id}>
                      {engine}
                    </Mono>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
