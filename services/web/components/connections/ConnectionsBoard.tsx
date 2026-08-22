'use client';

import { useCallback, useEffect, useState } from 'react';

import { Button, Failure, Label, Mono, cx } from '@/components/ui/primitives';
import type { Connections, ScopeGrant } from '@/lib/api/client';
import { absolute, ago } from '@/lib/format';

/**
 * The page that replaced a CLI command printed inside the product.
 *
 * Until Phase 8 the fleet page carried this sentence: *"No watch is registered, so no email
 * will start a review. Register one with `tools/gmail_watch.py --apply`."* A shell command,
 * rendered in an interface, as an instruction to the reader. Everything else wrong with
 * that interface followed from the same root — it documented the system instead of being
 * it — and this page is where that stops for the inbound path. **Connect registers the
 * watch.**
 *
 * ## Three things every connection has to say
 *
 * Connected or not, **which account**, and **what it may do**. The scopes are listed
 * individually with what each permits, because the narrowness is a claim worth being able
 * to read off the screen: `drive.file` sees only files this application created, and a page
 * that summarised all four as "Google access" would be throwing away the most interesting
 * true thing about the integration.
 *
 * ## The expiry is on screen because Gmail will not tell you
 *
 * `users.watch` lapses after seven days. Gmail does not renew it, does not warn, and does
 * not fail loudly — the notifications simply stop, and a mailbox that has stopped notifying
 * is indistinguishable from a mailbox nobody has emailed. So the hours remaining are shown,
 * and an expired watch says so in place of the reassuring green line.
 *
 * ## Refusing to connect is a feature, and its reason is the payload
 *
 * Gmail will register a watch against a topic nobody is subscribed to. It returns a history
 * id, records a healthy-looking registration, and drops every notification into a void for
 * a week — the worst available outcome, because it looks exactly like it worked. The
 * dispatcher checks first and refuses, and that refusal is rendered here in full.
 */

export function ConnectionsBoard({
  initial,
  loadError,
}: {
  initial: Connections | null;
  loadError: string | null;
}) {
  const [state, setState] = useState<Connections | null>(initial);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [probing, setProbing] = useState(true);

  const refresh = useCallback(async () => {
    const response = await fetch('/api/attestor/connections?probe=true', { cache: 'no-store' });
    if (!response.ok) {
      setError(`The control plane returned ${response.status}.`);
      return;
    }
    setState((await response.json()) as Connections);
  }, []);

  // The server rendered the Firestore-only view so the page paints immediately. This is the
  // rest of it: whether a mailbox has consented, whether Gmail can actually publish to the
  // topic, and what each scope permits -- four round trips across two services, which is
  // not something to hold a first frame for.
  useEffect(() => {
    void refresh().finally(() => setProbing(false));
  }, [refresh]);

  const act = useCallback(
    async (method: 'POST' | 'DELETE') => {
      setBusy(true);
      setRefusal(null);
      setError(null);
      try {
        const response = await fetch('/api/attestor/connections/gmail', { method });
        const payload: unknown = await response.json().catch(() => null);
        if (!response.ok) {
          const detail =
            payload && typeof payload === 'object' && 'detail' in payload
              ? String((payload as { detail: unknown }).detail)
              : `The control plane returned ${response.status}.`;
          // A 409 is a refusal with a reason; anything else is a failure. Rendered
          // differently, because "this would not have worked" and "this did not run" call
          // for different actions.
          if (response.status === 409) setRefusal(detail);
          else setError(detail);
          return;
        }
        await refresh();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  if (loadError !== null || state === null) {
    return (
      <Failure
        what="The connections could not be read."
        detail={loadError ?? 'No payload was returned.'}
      />
    );
  }

  const { gmail, drive, slack } = state;

  return (
    <div className="flex flex-col gap-6">
      {probing ? (
        <p className="text-xs text-muted">Checking what each connection can currently do.</p>
      ) : null}

      {state.unavailable ? (
        <Failure
          what="These connections cannot be changed from here right now."
          detail={state.unavailable}
        />
      ) : null}

      <Card
        title="Gmail"
        account={gmail.address}
        connected={gmail.connected}
        status={
          gmail.expired
            ? 'The watch has expired. No email is starting a review.'
            : gmail.connected
              ? `Watching. ${gmail.expires_in_hours ?? 0}h before it has to be renewed.`
              : 'No watch is registered. Email arriving at this mailbox starts nothing.'
        }
        action={
          <div className="flex items-center gap-2">
            {gmail.connected ? (
              <>
                <Button
                  onClick={() => void act('POST')}
                  disabled={busy || !state.manageable}
                  title={state.manageable ? undefined : 'The connection service is unreachable.'}
                >
                  {busy ? 'Renewing' : 'Renew'}
                </Button>
                <Button
                  tone="ghost"
                  onClick={() => void act('DELETE')}
                  disabled={busy || !state.manageable}
                >
                  Disconnect
                </Button>
              </>
            ) : (
              <Button
                tone="primary"
                onClick={() => void act('POST')}
                disabled={busy || !state.manageable}
                title={state.manageable ? undefined : 'The connection service is unreachable.'}
              >
                {busy ? 'Connecting' : 'Connect'}
              </Button>
            )}
          </div>
        }
      >
        {refusal !== null ? (
          <div className="flex flex-col gap-1 border-l-2 border-flagged pl-3">
            <p className="text-sm text-primary">The watch was not registered.</p>
            <p className="max-w-prose text-sm text-secondary">{refusal}</p>
            <p className="max-w-prose text-xs text-muted">
              Nothing was changed, and nothing is being watched.
            </p>
          </div>
        ) : null}

        {error !== null ? (
          <Failure what="That did not run." detail={error} />
        ) : null}

        {gmail.consented === false ? (
          <p className="max-w-prose text-sm text-secondary">
            No mailbox has granted consent to this deployment, so there is nothing to watch.
            Consent is a person signing in to a Google account and approving the scopes
            below; it cannot be granted by this or any other service on their behalf.
          </p>
        ) : null}

        <Facts>
          <Fact label="Mailbox" value={gmail.address || 'none'} mono />
          {gmail.registered_at ? (
            <Fact
              label="Connected"
              value={ago(gmail.registered_at)}
              title={absolute(gmail.registered_at)}
            />
          ) : null}
          {gmail.expires_at ? (
            <Fact
              label={gmail.expired ? 'Expired' : 'Renew before'}
              value={absolute(gmail.expires_at)}
              tone={gmail.expired ? 'warn' : undefined}
            />
          ) : null}
          {gmail.history_id ? <Fact label="History cursor" value={gmail.history_id} mono /> : null}
          {gmail.topic ? <Fact label="Notifications to" value={gmail.topic} mono /> : null}
        </Facts>

        {gmail.delivery ? (
          <div className="flex flex-col gap-2">
            <Label>whether a notification would actually arrive</Label>
            <p className="max-w-prose text-sm text-secondary">{gmail.delivery.note}</p>
            {gmail.delivery.subscriptions.length > 0 ? (
              <ul className="flex flex-col gap-1">
                {gmail.delivery.subscriptions.map((name) => (
                  <li key={name}>
                    <Mono dim>{name}</Mono>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <Scopes scopes={gmail.scopes} />
      </Card>

      <Card
        title="Drive"
        account={drive.connected ? gmail.address : ''}
        connected={drive.connected}
        status={
          drive.connected
            ? 'Completed packs are filed to a folder per customer.'
            : 'Nothing is filed anywhere. A completed pack stays in this system.'
        }
      >
        <p className="max-w-prose text-sm text-secondary">
          Drive is granted in the same consent as the mailbox and has nothing separate to
          register, so it is connected exactly when that consent exists.
        </p>
        <Scopes scopes={drive.scopes} />
      </Card>

      <Card
        title="Slack"
        account=""
        connected={slack.connected}
        status="Not built. Escalations reach a person by email."
      >
        <p className="max-w-prose text-sm text-secondary">
          Listed rather than omitted. An integration that does not exist and one nobody has
          connected look identical when only the working ones are shown, and the difference
          is the one a reader is trying to establish.
        </p>
      </Card>
    </div>
  );
}

function Card({
  title,
  account,
  connected,
  status,
  action,
  children,
}: {
  title: string;
  account: string;
  connected: boolean;
  status: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <section className="rounded border border-line bg-surface">
      <header className="flex items-start justify-between gap-4 border-b border-subtle px-6 py-4">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex items-baseline gap-3">
            <h2 className="text-md font-medium text-primary">{title}</h2>
            <span
              className={cx('text-xs', connected ? 'text-cited' : 'text-muted')}
              title={connected ? 'Connected' : 'Not connected'}
            >
              {connected ? 'connected' : 'not connected'}
            </span>
            {account ? <Mono dim>{account}</Mono> : null}
          </div>
          <p className="max-w-prose text-sm text-secondary">{status}</p>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </header>
      <div className="flex flex-col gap-6 px-6 py-4">{children}</div>
    </section>
  );
}

function Facts({ children }: { children: React.ReactNode }) {
  return <dl className="flex flex-wrap gap-x-12 gap-y-3">{children}</dl>;
}

function Fact({
  label,
  value,
  mono,
  title,
  tone,
}: {
  label: string;
  value: string;
  mono?: boolean;
  title?: string;
  tone?: 'warn';
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      <dd
        title={title}
        className={cx(
          'text-sm',
          mono ? 'font-mono text-xs text-secondary' : '',
          tone === 'warn' ? 'text-flagged' : 'text-primary',
        )}
      >
        {value}
      </dd>
    </div>
  );
}

/**
 * The scopes, one line each, with what each one permits.
 *
 * Not summarised. `drive.file` grants access to files this application created and nothing
 * else in the Drive, which is the least-privilege story this integration has, and it only
 * lands if a reader can see the scope name next to the sentence.
 */
function Scopes({ scopes }: { scopes: ScopeGrant[] }) {
  if (scopes.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <Label>what it may do</Label>
      <ul className="flex flex-col gap-2">
        {scopes.map((grant) => (
          <li key={grant.scope} className="flex flex-col gap-1">
            <Mono>{grant.scope.replace('https://www.googleapis.com/auth/', '')}</Mono>
            <span className="max-w-prose text-sm text-secondary">{grant.grants}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
