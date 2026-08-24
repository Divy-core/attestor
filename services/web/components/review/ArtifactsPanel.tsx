'use client';

import { useCallback, useEffect, useState } from 'react';

import { Button, Empty, Failure, Label, Mono, cx } from '@/components/ui/primitives';
import type { ArtifactRow } from '@/lib/api/client';

/**
 * What this review produced, where it went, and the one control that sends it.
 *
 * ## The panel
 *
 * A vendor security review does not end in a database. It ends with the pack in the
 * customer's hands and a copy where the compliance owner can find it in eighteen months.
 * This lists both halves: the files written to Drive, and — once it has happened — the fact
 * that they were emailed, by whom.
 *
 * ## The send control
 *
 * The only irreversible action in this interface, and it is built to feel like one.
 *
 * It asks for a name and will not proceed without one. That is not a form validation: the
 * protocol's `DeliverPackPayload.approved_by` cannot be blank, so an unattributed send is
 * unconstructable — this field is where the name comes from, and typing it is the moment a
 * person takes responsibility. Whitespace is refused for the same reason the payload refuses
 * it: `"   "` in an audit trail looks populated and identifies nobody.
 *
 * It is deliberately **not** on the command palette and **not** on a keyboard shortcut. A
 * palette that can fire a destructive action off a fuzzy match will eventually fire the
 * wrong one, and the whole point of this control is that reaching it is a decision.
 */

const KIND_LABELS: Record<string, string> = {
  workbook: 'Completed questionnaire',
  evidence_pack: 'Evidence pack',
};

function bytes(size: number): string {
  if (size <= 0) return '—';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} kB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function ArtifactsPanel({
  reviewId,
  canDeliver,
}: {
  reviewId: string;
  /**
   * Whether this review arrived by email. A review started from the browser has no thread to
   * reply on, and the control says so rather than offering a button that 409s.
   */
  canDeliver: boolean;
}) {
  const [rows, setRows] = useState<ArtifactRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<{ to: string; dedupKey: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch(
        `/api/attestor/reviews/${encodeURIComponent(reviewId)}/artifacts`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error(`The control plane returned ${response.status}.`);
      setRows((await response.json()) as ArtifactRow[]);
      setError(null);
    } catch (cause) {
      // Never an empty list: "this review produced nothing" and "the read failed" are
      // different facts and the second one rendered as the first is the mistake this
      // codebase has made repeatedly in Python.
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [reviewId]);

  useEffect(() => {
    void load();
  }, [load]);

  const send = useCallback(async () => {
    if (!name.trim()) return;
    setSending(true);
    try {
      const response = await fetch(
        `/api/attestor/reviews/${encodeURIComponent(reviewId)}/deliver`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approved_by: name.trim(), note }),
        },
      );
      const payload = (await response.json()) as {
        to?: string;
        dedup_key?: string;
        detail?: string;
      };
      if (!response.ok) throw new Error(payload.detail ?? `The control plane returned ${response.status}.`);
      setSent({ to: payload.to ?? 'the customer', dedupKey: payload.dedup_key ?? '' });
      setConfirming(false);
      setTimeout(() => void load(), 2_000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSending(false);
    }
  }, [name, note, reviewId, load]);

  return (
    <div className="flex flex-col gap-4 p-4">
      <section className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between gap-4">
          <h3 className="text-md font-medium text-primary">Artifacts</h3>
          <Label>what this review produced</Label>
        </div>

        {error !== null ? (
          <Failure
            what="The artifacts could not be read."
            detail={error}
            action={
              <Button small onClick={() => void load()}>
                Try again
              </Button>
            }
          />
        ) : rows === null ? (
          <p className="text-sm text-muted">Reading…</p>
        ) : rows.length === 0 ? (
          <Empty
            title="Nothing has been produced yet"
            hint="The completed workbook and the evidence pack are written to Drive when the pack is sent. Until then they can be downloaded from Export, which builds them on demand."
          />
        ) : (
          <ul className="flex flex-col">
            {rows.map((row) => (
              <li
                key={row.file_id}
                className="flex items-center gap-3 border-b border-subtle py-3 last:border-0"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-primary">
                    {KIND_LABELS[row.kind] ?? row.kind}
                  </span>
                  <Mono dim>{row.name}</Mono>
                </span>
                <span className="shrink-0 text-xs tabular-nums text-muted">
                  {bytes(row.size_bytes)}
                </span>
                <a
                  href={row.link}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 text-sm"
                  title="Opens in Drive. The file is shared with nobody, so this asks you to sign in."
                >
                  Open
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-3 border-t border-subtle pt-4">
        <div className="flex items-baseline justify-between gap-4">
          <h3 className="text-md font-medium text-primary">Send to the customer</h3>
          <Label>irreversible</Label>
        </div>

        {sent !== null ? (
          <div className="flex flex-col gap-2 rounded-sm bg-fill-cited px-3 py-2">
            <p className="text-sm font-medium text-cited">Sent to {sent.to}.</p>
            <p className="text-xs text-secondary">
              The dispatcher is delivering it. A redelivery of this authorisation is a no-op —
              its key is <Mono>{sent.dedupKey}</Mono>.
            </p>
          </div>
        ) : !canDeliver ? (
          <p className="max-w-prose text-sm text-muted">
            This review did not arrive by email. There is no thread to reply on; download the
            pack from Export.
          </p>
        ) : (
          <>
            <p className="max-w-prose text-sm text-secondary">
              Replies in the thread the questionnaire arrived on, with the completed workbook
              and the evidence pack attached, and writes a copy to Drive. Answers held for a
              human, unsupported by evidence, or blocked by the guardrail are marked as such in
              the file.
            </p>
            <label className="flex flex-col gap-2">
              <Label>your name, recorded on the audit trail</Label>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="who is authorising this"
                className="h-row w-full max-w-list rounded-sm bg-sunken px-2 text-sm text-primary outline-none placeholder:text-muted"
              />
            </label>
            <label className="flex flex-col gap-2">
              <Label>a line for the covering note, if you want one</Label>
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="optional"
                className="h-row w-full max-w-list rounded-sm bg-sunken px-2 text-sm text-primary outline-none placeholder:text-muted"
              />
            </label>

            {confirming ? (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm text-primary">
                  This sends an email. It cannot be recalled.
                </span>
                <Button tone="primary" disabled={sending} onClick={() => void send()}>
                  {sending ? 'Sending…' : `Send as ${name.trim()}`}
                </Button>
                <Button tone="ghost" disabled={sending} onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <Button
                  disabled={!name.trim()}
                  title={
                    name.trim()
                      ? 'You will be asked to confirm'
                      : 'Enter the name that goes on the audit trail first'
                  }
                  onClick={() => setConfirming(true)}
                  className={cx(!name.trim() && 'cursor-not-allowed')}
                >
                  Send the pack
                </Button>

              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
