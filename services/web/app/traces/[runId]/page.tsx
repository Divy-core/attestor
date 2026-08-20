import { AppShell } from '@/components/layout/AppShell';
import { TracePanels } from '@/components/trace/TracePanels';
import { Failure, Mono } from '@/components/ui/primitives';
import { ApiError, api, type AuditEvent } from '@/lib/api/client';

export const dynamic = 'force-dynamic';

/**
 * The audit trail for one run.
 *
 * ## Why the route is keyed on a run but the read is keyed on a review
 *
 * `audit_events` is indexed by review, and that is the right index: the question this plane
 * exists to answer — "why did we answer yes to Q112?" — is asked about a *review*, and the
 * answer may span several runs, because a redelivery, a resume after a human, and a follow-up
 * round are all separate runs against the same claim.
 *
 * So the page reads the whole review's trail and offers the run as a filter rather than as a
 * boundary. Keying the read on the run would hide exactly the cross-run history that makes the
 * trail worth having.
 *
 * The review id is derived from the run's own events rather than parsed out of the run id,
 * because run ids are minted in several shapes (`run-`, `resume-`, `compare-`) and a parser over
 * them would be a silent 404 the first time a new prefix appeared.
 */
export default async function TracePage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;

  let events: AuditEvent[] = [];
  let armorEvents: AuditEvent[] = [];
  let error: string | null = null;
  let reviewId: string | null = null;

  try {
    // There is no by-run read endpoint, and adding one to a deployed control plane for a UI
    // convenience is not a trade worth making. Reviews are few; the run's review is found by
    // scanning their trails, newest first, and the scan stops at the first hit.
    const reviews = await api.listReviews(20);
    for (const review of reviews) {
      const trail = await api.listAudit(review.review_id, 1000);
      if (trail.some((event) => event.run_id === runId)) {
        reviewId = review.review_id;
        events = trail;
        armorEvents = await api.listArmor(review.review_id, 200);
        break;
      }
    }
  } catch (cause) {
    error = cause instanceof ApiError ? cause.human : String(cause);
  }

  if (error !== null || reviewId === null) {
    return (
      <AppShell pathname={`/traces/${runId}`} title="Audit trail">
        <div className="p-6">
          <Failure
            what={error !== null ? 'The audit trail could not be read.' : 'No trail for this run.'}
            detail={
              error ??
              `No audit event carries run_id ${runId}. Either the run has not written its first event yet, or it belongs to a review outside the twenty most recent.`
            }
          />
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      pathname={`/traces/${runId}`}
      title="Audit trail"
      meta={
        <>
          <Mono dim>{reviewId}</Mono> · run <Mono dim>{runId}</Mono>
        </>
      }
    >
      <div className="flex h-full min-h-0 flex-col p-6">
        <TracePanels events={events} armorEvents={armorEvents} />
      </div>
    </AppShell>
  );
}
