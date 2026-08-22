import { FleetBoard } from '@/components/fleet/FleetBoard';
import { AppShell } from '@/components/layout/AppShell';
import { Failure } from '@/components/ui/primitives';
import {
  ApiError,
  api,
  type AuditEvent,
  type InboxStatus,
  type RegistryAgent,
  type ReviewRow,
} from '@/lib/api/client';
import { activityByActor, reviewsWorthReading, roster } from '@/lib/fleet';

export const dynamic = 'force-dynamic';

/**
 * The landing page is the fleet, not a list of reviews.
 *
 * A list of reviews is the least interesting true thing about this system. What is
 * interesting is that six agents exist with distinct identities, that a department engine is
 * refused another department's corpus by IAM rather than by instruction, and that a review
 * can start because an email arrived. None of that was visible before Phase 7, and for
 * scoring purposes invisible is the same as absent.
 *
 * ## Every read here degrades on its own
 *
 * Four independent reads, each caught separately. A failed registry read renders as a failed
 * read — never as an empty fleet, which would be a claim rather than an error. The reviews
 * list, the mailbox status and the audit trails are the same: any one of them can be
 * unavailable without taking the page down or, worse, taking it down to something that looks
 * like a working system with nothing in it.
 */
export default async function FleetPage() {
  const [registryResult, reviewsResult, inboxResult] = await Promise.allSettled([
    api.listRegistry(),
    api.listReviews(50),
    api.inbox(),
  ]);

  const registry: RegistryAgent[] =
    registryResult.status === 'fulfilled' ? registryResult.value : [];
  const registryError =
    registryResult.status === 'rejected' ? describe(registryResult.reason) : null;

  const allReviews: ReviewRow[] = reviewsResult.status === 'fulfilled' ? reviewsResult.value : [];
  const reviewsError = reviewsResult.status === 'rejected' ? describe(reviewsResult.reason) : null;

  const inbox: InboxStatus | null = inboxResult.status === 'fulfilled' ? inboxResult.value : null;
  const inboxError = inboxResult.status === 'rejected' ? describe(inboxResult.reason) : null;

  const visible = allReviews.filter((review) => !review.archived);
  const archivedCount = allReviews.length - visible.length;

  // Bounded. Every review's audit trail is a separate query of up to a thousand documents,
  // and a landing page must not become a scan of the whole collection as runs accumulate.
  const trails = await Promise.allSettled(
    reviewsWorthReading(visible).map((review) => api.listAudit(review.review_id, 1000)),
  );
  const events: AuditEvent[] = trails.flatMap((trail) =>
    trail.status === 'fulfilled' ? trail.value : [],
  );

  const members = roster(registry);
  const activity = activityByActor(events);

  return (
    <AppShell
      pathname="/"
      title="Fleet"
      meta={
        registryError === null
          ? `${members.length} agents · ${visible.length} live reviews`
          : undefined
      }
      reviews={visible}
    >
      {reviewsError !== null ? (
        <div className="p-4">
          <Failure what="The control plane could not be reached." detail={reviewsError} />
        </div>
      ) : (
        <FleetBoard
          members={members}
          activity={activity}
          reviews={visible}
          archivedCount={archivedCount}
          inbox={inbox}
          inboxError={inboxError}
          registryError={registryError}
        />
      )}
    </AppShell>
  );
}

function describe(cause: unknown): string {
  return cause instanceof ApiError ? cause.human : String(cause);
}
