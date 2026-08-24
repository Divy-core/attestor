'use client';

import { useState } from 'react';

import { Button, Label, Mono, cx } from '@/components/ui/primitives';
import type { AnswerRow, QuestionRow } from '@/lib/api/client';
import { useOperator } from '@/lib/operator';

/**
 * The human gate.
 *
 * ## What "Approve" actually does, and why the receipt is shown
 *
 * The control plane does not apply the decision. It publishes `resume_after_human` to Pub/Sub
 * and the dispatcher applies it — which is what makes a redelivered approval idempotent rather
 * than usually-fine. That is a real architectural property and it is invisible unless the UI
 * says so, so the response's `dedup_key` is rendered on success. It is the value that makes
 * the second delivery a no-op, and showing it turns a claim into something on screen.
 *
 * ## Copy
 *
 * A button that says "Approve" produces "Approved." Sentence case, active voice, past tense on
 * completion. No "Successfully approved!", no exclamation, no toast that says "Great!".
 */

type Outcome = { dedupKey: string; runId: string } | { error: string } | null;

export function ApprovalQueue({
  pending,
  onResolved,
  focus,
}: {
  pending: Array<{ question: QuestionRow; answer: AnswerRow }>;
  onResolved: () => void;
  /**
   * The question the grid has selected. When it is in this queue it is ordered first, so
   * pressing `a` on a row lands on that row rather than at the top of a list of seventy.
   */
  focus?: string | null;
}) {
  const [operator, setOperator] = useOperator();

  const ordered =
    focus == null
      ? pending
      : [
          ...pending.filter((row) => row.question.question_id === focus),
          ...pending.filter((row) => row.question.question_id !== focus),
        ];
  if (pending.length === 0) {
    return (
      <div className="flex flex-col items-start gap-2 px-4 py-8">
        <div className="w-full border-t border-dashed border-line" />
        <h3 className="pt-3 text-sm font-medium text-primary">Nothing waiting on a human</h3>
        <p className="max-w-prose text-sm text-secondary">
          Answers arrive here on a missing citation, a low computed confidence, or a
          contradiction with an earlier round.
        </p>
      </div>
    );
  }

  return (
    <>
      <label className="flex flex-col gap-2 border-b border-subtle px-4 py-3">
        <Label>your name, recorded against every decision you make here</Label>
        <input
          value={operator}
          onChange={(event) => setOperator(event.target.value)}
          placeholder="who is reviewing"
          className="h-row w-full max-w-list rounded-sm bg-sunken px-2 text-sm text-primary outline-none placeholder:text-muted"
        />
        {operator.trim() ? null : (
          <span className="text-xs text-muted">
            Approve and Reject stay disabled until this is filled in.
          </span>
        )}
      </label>
      <ul className="flex flex-col">
        {ordered.map(({ question, answer }) => (
          <ApprovalRow
            key={question.question_id}
            question={question}
            answer={answer}
            operator={operator}
            onResolved={onResolved}
          />
        ))}
      </ul>
    </>
  );
}


function ApprovalRow({
  question,
  answer,
  operator,
  onResolved,
}: {
  question: QuestionRow;
  answer: AnswerRow;
  operator: string;
  onResolved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(answer.text);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);

  async function submit(approved: boolean) {
    setBusy(true);
    setOutcome(null);
    try {
      const response = await fetch(
        `/api/attestor/rounds/${encodeURIComponent(answer.round_id)}/answers/${encodeURIComponent(
          question.question_id,
        )}/approval`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            question_id: question.question_id,
            approved,
            edited_text: editing && text !== answer.text ? text : null,
            // The name the reviewer typed. The control plane rejects whitespace-only
            // values, so a blank one fails at the edge rather than landing in the trail
            // looking populated.
            resolved_by: operator.trim(),
          }),
        },
      );
      const payload: unknown = await response.json();
      if (!response.ok) {
        const detail =
          payload && typeof payload === 'object' && 'detail' in payload
            ? String((payload as { detail: unknown }).detail)
            : `${response.status}`;
        setOutcome({ error: detail });
        return;
      }
      const body = payload as { dedup_key?: string; run_id?: string };
      setOutcome({ dedupKey: body.dedup_key ?? '', runId: body.run_id ?? '' });
      onResolved();
    } catch (cause) {
      setOutcome({ error: cause instanceof Error ? cause.message : String(cause) });
    } finally {
      setBusy(false);
    }
  }

  const resolved = outcome !== null && 'dedupKey' in outcome;

  return (
    <li className="flex flex-col gap-2 border-b border-subtle px-4 py-3">
      <p className="text-sm text-primary">{question.text}</p>
      <Mono dim>{question.question_id}</Mono>

      {editing ? (
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={5}
          aria-label="Edited answer"
          className={cx(
            'w-full rounded-sm bg-sunken px-2 py-2 text-sm text-primary',
          )}
        />
      ) : (
        <p className="whitespace-pre-wrap text-sm text-secondary">{answer.text}</p>
      )}

      {resolved ? (
        // Past tense, and the mechanism named. Not a toast that disappears -- the receipt is
        // the evidence that this went through Pub/Sub rather than being written directly.
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-l-2 border-cited pl-3">
          <span className="text-sm text-primary">
            {editing ? 'Edited and approved.' : 'Approved.'}
          </span>
          <span className="text-xs text-secondary">
            Published to Pub/Sub. The dispatcher applies it.
          </span>
          <Mono dim title="Idempotency key">
            {(outcome as { dedupKey: string }).dedupKey}
          </Mono>
        </div>
      ) : outcome !== null && 'error' in outcome ? (
        <div className="flex flex-col gap-1 border-l-2 border-denied pl-3">
          <span className="text-sm text-primary">The decision was not published.</span>
          <Mono className="text-xs">{outcome.error}</Mono>
          <span className="text-xs text-secondary">
            Nothing was applied. Try again, or check the control plane.
          </span>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <Button
            tone="primary"
            onClick={() => submit(true)}
            disabled={busy || !operator.trim()}
            title={operator.trim() ? undefined : "Enter your name above first"}
          >
            {busy ? 'Publishing' : editing ? 'Save and approve' : 'Approve'}
          </Button>
          <Button onClick={() => setEditing(!editing)} disabled={busy}>
            {editing ? 'Cancel edit' : 'Edit'}
          </Button>
          <Button
            tone="ghost"
            onClick={() => submit(false)}
            disabled={busy || !operator.trim()}
            title={operator.trim() ? undefined : "Enter your name above first"}
          >
            Reject
          </Button>
        </div>
      )}
    </li>
  );
}
