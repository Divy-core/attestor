import { ConnectionsBoard } from '@/components/connections/ConnectionsBoard';
import { AppShell } from '@/components/layout/AppShell';
import { ApiError, api, type Connections } from '@/lib/api/client';

export const dynamic = 'force-dynamic';

/**
 * Gmail, Drive, Slack. Connected or not, which account, which scopes, since when.
 *
 * This page exists because of one sentence that used to be live on the fleet page: *"No
 * watch is registered, so no email will start a review. Register one with
 * `tools/gmail_watch.py --apply`."* Every capability the product has must be reachable from
 * the product, and until Phase 8 the single capability the whole inbound story rested on
 * was reachable only from a terminal.
 */
export default async function ConnectionsPage() {
  let connections: Connections | null = null;
  let loadError: string | null = null;
  try {
    // `probe: false` -- Firestore only. The full picture costs an IAM policy read, a
    // subscription list and a Secret Manager read on another service, and holding the first
    // frame for four round trips to render a settings page is the wrong trade. The board
    // asks for the rest itself, on mount.
    connections = await api.connections(false);
  } catch (cause) {
    loadError = cause instanceof ApiError ? cause.human : String(cause);
  }

  const gmail = connections?.gmail;
  const meta =
    gmail === undefined
      ? undefined
      : gmail.connected
        ? `Gmail connected · ${gmail.address}`
        : 'Gmail not connected';

  return (
    <AppShell pathname="/connections" title="Connections" meta={meta}>
      <div className="mx-auto flex w-full max-w-page flex-col gap-6 px-6 py-8">
        <ConnectionsBoard initial={connections} loadError={loadError} />
      </div>
    </AppShell>
  );
}
