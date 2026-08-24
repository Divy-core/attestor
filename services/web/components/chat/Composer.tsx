'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { Button, Label, Mono, cx } from '@/components/ui/primitives';
import { ACCEPT_ATTRIBUTE } from '@/lib/api/start';
import { useOperator } from '@/lib/operator';
import type { MessageReply } from '@/lib/types/thread';

/**
 * Where a person types, and the only place in the product that both answers and acts.
 *
 * ## One endpoint, and the client does not classify
 *
 * The line goes to `POST /reviews/{id}/message` and the server decides whether it is a
 * question or an instruction. Putting that decision here would mean shipping the command
 * grammar to the browser and keeping two copies of it in step; the grammar is literal
 * rather than fuzzy and lives in `attestor_platform.thread.commands`.
 *
 * ## Confirmation is a round trip, not a dialog
 *
 * An irreversible command comes back as `kind: 'confirm'` with **nothing written and
 * nothing published**. The prompt renders inline, above the box, and going through with it
 * re-posts the same text with `confirm` set to the action by name. A confirmation that the
 * client could skip would not be a gate.
 *
 * ## The attachment
 *
 * `+` opens a file picker; a drop anywhere on the column does the same thing. Where the
 * file goes depends on whether a conversation is open, and the caller decides that — see
 * `onAttach`.
 */

type Props = {
  /** Null on the empty state, where there is no conversation to talk to yet. */
  reviewId: string | null;
  /** A questionnaire was handed in. Starts a review, or opens the next round. */
  onAttach: (file: File) => void;
  /** Something was recorded or dispatched; the thread should re-read. */
  onSettled: () => void;
  /** A command asked for a panel rather than for work. */
  onPanel?: (panel: string) => void;
  placeholder?: string;
  /** Rendered to the right of the send control: stream health, usually. */
  status?: React.ReactNode;
  autoFocus?: boolean;
};

export function Composer({
  reviewId,
  onAttach,
  onSettled,
  onPanel,
  placeholder = 'Ask about this review, or tell it what to do',
  status,
  autoFocus = false,
}: Props) {
  const [operator, setOperator] = useOperator();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<MessageReply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  // Grows with the content and stops. `field-sizing: content` is not available across the
  // browsers this has to run in, so the height is measured: reset to `auto` first, or the
  // scroll height only ever ratchets upward and the box never shrinks again after a delete.
  useEffect(() => {
    const el = box.current;
    if (el === null) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);

  const post = useCallback(
    async (confirm?: string) => {
      const line = text.trim();
      if (!line || !operator.trim() || reviewId === null) return;
      setBusy(true);
      setError(null);
      try {
        const response = await fetch(
          `/api/attestor/reviews/${encodeURIComponent(reviewId)}/message`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: line, actor: operator.trim(), confirm: confirm ?? '' }),
          },
        );
        const payload: unknown = await response.json().catch(() => null);
        if (!response.ok) {
          const detail =
            payload && typeof payload === 'object' && 'detail' in payload
              ? String((payload as { detail: unknown }).detail)
              : `The control plane returned ${response.status}.`;
          setError(detail);
          return;
        }
        const result = payload as MessageReply;
        // An answer is not echoed here. Both halves of the exchange are already appended to
        // the audit trail, so the thread above shows them in place a moment later -- and a
        // reply rendered twice, once transiently and once for good, reads as two replies.
        setReply(result.kind === 'answered' ? null : result);
        if (result.kind === 'confirm') return;

        setText('');
        if (result.kind === 'dispatched' && result.action === 'export') onPanel?.('report');
        onSettled();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy(false);
      }
    },
    [text, operator, reviewId, onSettled, onPanel],
  );

  const named = operator.trim().length > 0;
  const ready = text.trim().length > 0 && named && reviewId !== null;

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        const file = event.dataTransfer.files[0];
        if (file) onAttach(file);
      }}
      className="flex flex-col gap-3"
    >
      {reply?.kind === 'confirm' ? (
        <div className="flex flex-col gap-2 rounded border border-flagged/40 bg-surface px-4 py-3">
          <p className="text-sm text-primary">{reply.prompt}</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button tone="primary" disabled={busy} onClick={() => void post(reply.action)}>
              {busy ? 'Sending' : `Go ahead, as ${operator.trim()}`}
            </Button>
            <Button tone="ghost" disabled={busy} onClick={() => setReply(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {reply?.kind === 'dispatched' ? (
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-l-2 border-cited pl-3">
          <span className="text-sm text-primary">{DISPATCHED[reply.action] ?? 'Done.'}</span>
          {reply.work !== 'none' ? (
            <>
              <span className="text-xs text-muted">published to Pub/Sub</span>
              <Mono dim>{reply.dedup_key}</Mono>
            </>
          ) : null}
        </div>
      ) : null}

      {error !== null ? (
        <div className="flex flex-col gap-1 border-l-2 border-denied pl-3">
          <p className="text-sm text-primary">That did not run.</p>
          <Mono className="text-xs">{error}</Mono>
        </div>
      ) : null}

      <div
        className={cx(
          'flex items-end gap-2 rounded border bg-surface px-3 py-2 transition-colors',
          dragging ? 'border-accent' : 'border-line',
        )}
      >
        <input
          ref={picker}
          type="file"
          accept={ACCEPT_ATTRIBUTE}
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onAttach(file);
            event.target.value = '';
          }}
        />
        <button
          type="button"
          onClick={() => picker.current?.click()}
          title="Attach a questionnaire"
          aria-label="Attach a questionnaire"
          className="flex h-row w-row shrink-0 items-center justify-center rounded-sm text-md text-muted transition-colors hover:bg-hover hover:text-primary"
        >
          +
        </button>

        <textarea
          ref={box}
          rows={1}
          value={text}
          autoFocus={autoFocus}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || event.shiftKey) return;
            event.preventDefault();
            if (ready && !busy) void post();
          }}
          placeholder={reviewId === null ? 'Drop a questionnaire to begin' : placeholder}
          aria-label="Message"
          disabled={reviewId === null}
          className={cx(
            'min-h-row flex-1 resize-none bg-transparent py-2 text-base text-primary',
            'outline-none placeholder:text-muted disabled:cursor-not-allowed',
          )}
        />

        {status ? <div className="shrink-0 pb-2">{status}</div> : null}
        <Button
          tone="primary"
          small
          disabled={!ready || busy}
          onClick={() => void post()}
          title={named ? undefined : 'Enter your name first'}
        >
          {busy ? 'Sending' : 'Send'}
        </Button>
      </div>

      {!named ? (
        <label className="flex items-center gap-2">
          <Label>your name, recorded on the audit trail</Label>
          <input
            value={operator}
            onChange={(event) => setOperator(event.target.value)}
            placeholder="who is here"
            className="h-row-dense w-full max-w-list rounded-sm bg-sunken px-2 text-xs text-primary outline-none placeholder:text-muted"
          />
        </label>
      ) : null}
    </div>
  );
}

/** What each dispatched command did, past tense. */
const DISPATCHED: Record<string, string> = {
  send_pack: 'Sending. The dispatcher replies on the customer thread.',
  redraft: 'Redrafting.',
  export: 'Building the workbook and the evidence pack.',
};
