'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef } from 'react';

/**
 * Open the workspace on a review that arrived while nobody was looking.
 *
 * The product's whole claim is that work starts without a person starting it. Until this,
 * the front door proved the opposite: an email would arrive, a fleet of agents would answer
 * forty questions, and the screen would go on showing an empty composer until somebody
 * thought to reload. The one moment worth watching was the one moment the interface hid.
 *
 * So the front door watches for work. It records the newest review it can see when it
 * mounts, polls the same board the rail is built from, and the first time something newer
 * than that appears it navigates to it. Only ever forward, and only ever to a review that
 * did not exist when the page opened — reloading the page with forty old reviews in the rail
 * does not fling you into the most recent one.
 *
 * Polling rather than the run stream, because the run stream is scoped to a run and this is
 * waiting for a review that has no run yet — there is nothing to subscribe to until the
 * thing being waited for exists.
 */

const POLL_MS = 4000;

type Seen = { review_id: string; opened_at: string | null };

export function AutoOpen({ seen }: { seen: Seen[] }): null {
  const router = useRouter();
  // A ref, not state: this is a high-water mark the effect reads, and putting it in state
  // would re-run the effect every tick and restart the interval.
  const known = useRef<Set<string>>(new Set(seen.map((review) => review.review_id)));
  const navigated = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function look(): Promise<void> {
      if (cancelled || navigated.current) return;
      try {
        const response = await fetch('/api/attestor/reviews/board?limit=20&include_archived=false');
        if (!response.ok) return;
        const board = (await response.json()) as Seen[];
        const fresh = board.find((review) => !known.current.has(review.review_id));
        if (fresh === undefined) return;
        // Remember it either way, so a navigation that gets cancelled does not leave the
        // page trying to open the same review every four seconds.
        known.current.add(fresh.review_id);
        navigated.current = true;
        router.push(`/reviews/${encodeURIComponent(fresh.review_id)}`);
      } catch {
        // A failed poll is not worth telling anyone about. The rail is still correct, the
        // composer still works, and the next tick is four seconds away.
      }
    }

    const timer = setInterval(() => void look(), POLL_MS);
    void look();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [router]);

  return null;
}
