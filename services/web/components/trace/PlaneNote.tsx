import { Panel, PanelHeader, Mono } from '@/components/ui/primitives';

/**
 * The two observability planes, labelled, with the asymmetry stated rather than smoothed over.
 *
 * Most entrants conflate these. This build does not, and the distinction is itself the
 * architectural claim: they have different consumers, different retention, different schemas,
 * and different questions they can answer.
 *
 * The honest part is the asymmetry. The compliance plane is complete and is what this system
 * leans on — 949 events for one review, attributed to named agents, immutable and queryable.
 * The engineering plane is real but **inherited**: it comes from Agent Runtime's
 * `enable_tracing=True` and Cloud Run's own request span. `attestor_platform.telemetry`
 * contains the audit writer and nothing else — this codebase emits no custom OTel spans.
 *
 * All of that reasoning lives here, in a comment, and none of it is rendered. What the panels
 * carry is the two labels, the event count, the span tree as captured, and one factual line
 * about whose spans those are. Phase 9 took the argument off the screen.
 */

/** The span tree as captured from Cloud Trace. Verbatim from `docs/proof/observability-planes.json`. */
const SPAN_TREE = `/pubsub/push
invoke_workflow security_agent
  invoke_agent security_agent
    execute_tool search_security_corpus
    call_llm -> generate_content gemini-3.7-flash`;

export function PlaneNote({ eventCount }: { eventCount: number }) {
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <Panel>
        <PanelHeader
          title="Compliance plane"
          meta={<>Firestore <Mono dim>audit_events</Mono></>}
        />
        <div className="flex flex-col gap-2 px-4 py-3">
          <p className="text-sm text-secondary">
            Append-only and attributed. Which identity did what, to which object, and when.
          </p>
          <p className="text-sm text-primary">
            <span className="font-mono tabular-nums">{eventCount}</span> events for this review.
          </p>
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Engineering plane" meta="Cloud Trace, via OpenTelemetry" />
        <div className="flex flex-col gap-2 px-4 py-3">
          <p className="text-sm text-secondary">
            Latency, token cost and tool spans, organised by span parentage.
          </p>
          <pre className="overflow-x-auto rounded bg-code px-3 py-2 font-mono text-xs text-secondary">
            {SPAN_TREE}
          </pre>
          <p className="border-t border-subtle pt-2 text-sm text-secondary">
            <span className="text-primary">This codebase emits no custom OTel spans.</span> The
            tree above comes from Agent Runtime&rsquo;s <Mono dim>enable_tracing=True</Mono> and
            Cloud Run&rsquo;s request span.
          </p>
        </div>
      </Panel>
    </div>
  );
}
