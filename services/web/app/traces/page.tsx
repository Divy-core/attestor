import Link from 'next/link';

import { AppShell } from '@/components/layout/AppShell';
import { Panel, Empty, Failure, Mono } from '@/components/ui/primitives';
import { ApiError, api, type ReviewRow } from '@/lib/api/client';
import { ago } from '@/lib/format';

export const dynamic = 'force-dynamic';

/**
 * An index, because `/traces` with no run id has to go somewhere and a bare 404 on a nav link is
 * worse than a list. Reviews rather than runs: a run id is not a thing anyone remembers, and the
 * trail is per-review anyway.
 */
export default async function TracesIndexPage() {
  let reviews: ReviewRow[] = [];
  let error: string | null = null;
  try {
    reviews = await api.listReviews(30);
  } catch (cause) {
    error = cause instanceof ApiError ? cause.human : String(cause);
  }

  return (
    <AppShell pathname="/traces" title="Audit trails">
      <div className="p-6">
        <Panel className="overflow-hidden">
          {error !== null ? (
            <Failure what="Reviews could not be listed." detail={error} />
          ) : reviews.length === 0 ? (
            <Empty
              title="No trails yet"
              hint="Every agent decision, retrieval, refusal and guardrail block appends to audit_events as it happens. A trail appears here with its review."
            />
          ) : (
            <ul className="flex flex-col">
              {reviews.map((review) => (
                <li key={review.review_id} className="border-b border-subtle last:border-b-0">
                  <Link
                    href={`/reviews/${review.review_id}`}
                    className="flex items-center gap-4 px-4 py-2 no-underline transition-colors hover:bg-hover"
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm text-primary">{review.customer}</span>
                      <Mono dim>{review.review_id}</Mono>
                    </span>
                    <span className="w-16 shrink-0 text-sm text-secondary">{review.state}</span>
                    <span className="w-16 shrink-0 text-right text-sm text-muted">
                      {ago(review.created_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <p className="px-1 pt-3 text-sm text-secondary">
          Open a review and follow <span className="text-primary">Audit trail</span> to see its
          compliance plane. A trail is addressed by run, and a review may have several — a
          redelivery, a resume after a human, and a follow-up round are each their own run against
          the same claims.
        </p>
      </div>
    </AppShell>
  );
}
