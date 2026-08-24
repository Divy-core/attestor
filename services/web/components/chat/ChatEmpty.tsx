'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { Composer } from '@/components/chat/Composer';
import { NewReviewDialog } from '@/components/review/NewReview';

/**
 * The front door with no conversation open.
 *
 * A heading and the composer, centred, and two things to do. Nothing else: no tour, no
 * feature grid, no explanation of what the product is for. Whoever opens this either drops
 * a file or connects a mailbox, and both are one action away.
 */

export function ChatEmpty({ watching }: { watching: string | null }) {
  const router = useRouter();
  const [pending, setPending] = useState<File | null>(null);

  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-6">
      <div className="flex w-full max-w-column flex-col gap-6 pb-16">
        <h1 className="text-lg text-primary">Drop a questionnaire, or connect a mailbox.</h1>

        <Composer
          reviewId={null}
          onAttach={setPending}
          onSettled={() => {}}
          placeholder="Drop a questionnaire to begin"
        />

        <p className="text-sm text-muted">
          {watching ? (
            <>
              Watching <span className="text-secondary">{watching}</span>.{' '}
              <Link href="/connections">Connections</Link>
            </>
          ) : (
            <>
              No mailbox connected. <Link href="/connections">Connect</Link>
            </>
          )}
        </p>
      </div>

      {pending !== null ? (
        <NewReviewDialog
          file={pending}
          onClose={() => setPending(null)}
          onStarted={(reviewId) => {
            setPending(null);
            router.push(`/reviews/${reviewId}`);
          }}
        />
      ) : null}
    </div>
  );
}
