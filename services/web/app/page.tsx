import { ChatEmpty } from '@/components/chat/ChatEmpty';
import { ChatShell } from '@/components/chat/ChatShell';
import { Failure } from '@/components/ui/primitives';
import { ApiError, api, type InboxStatus, type ReviewCard } from '@/lib/api/client';

export const dynamic = 'force-dynamic';

/**
 * The front door.
 *
 * It was the fleet board — eight agents and their permissions, which is the most interesting
 * *architecture* in the project and not the thing anyone opens a product to do. The fleet
 * board is still there, at `/fleet`, and this is a conversation with the system instead.
 *
 * Two reads, both allowed to fail on their own. A rail that could not be listed renders as a
 * failure; the composer still works, because handing a questionnaire in does not depend on
 * knowing what came before it.
 */
export default async function ChatPage() {
  const [reviewResult, inboxResult] = await Promise.allSettled([
    api.reviewBoard(100, false),
    api.inbox(),
  ]);

  const reviews: ReviewCard[] =
    reviewResult.status === 'fulfilled' ? reviewResult.value : [];
  const error =
    reviewResult.status === 'rejected'
      ? reviewResult.reason instanceof ApiError
        ? reviewResult.reason.human
        : String(reviewResult.reason)
      : null;

  const inbox: InboxStatus | null = inboxResult.status === 'fulfilled' ? inboxResult.value : null;
  const watching = inbox?.watching && !inbox.expired ? inbox.address : null;

  return (
    <ChatShell reviews={reviews} activeId={null}>
      {error !== null ? (
        <div className="p-6">
          <Failure what="The control plane could not be reached." detail={error} />
        </div>
      ) : (
        <ChatEmpty watching={watching} />
      )}
    </ChatShell>
  );
}
