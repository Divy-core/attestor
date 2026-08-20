import { CitationList } from '@/components/review/CitationList';
import { ConfidenceMeter } from '@/components/review/ConfidenceMeter';
import { Mono, StateBadge, cx } from '@/components/ui/primitives';
import { absolute, ago } from '@/lib/format';
import { stateFor } from '@/lib/states';
import type { AnswerRow, QuestionRow } from '@/lib/api/client';

/**
 * One question, its answer, and the trail behind it.
 *
 * The order on screen is the order of the argument: what was asked, who answered it, what they
 * said, what backs it, and how confident the system is on the evidence. Confidence comes last
 * deliberately — reading the verdict before the evidence is how a reader stops checking.
 */
export function AnswerCard({
  question,
  answer,
}: {
  question: QuestionRow;
  answer: AnswerRow | null;
}) {
  const state = stateFor(answer?.status ?? null, answer?.citations.length ?? 0);

  return (
    <article className="flex flex-col gap-4 rounded shadow-line bg-surface">
      <header className="flex flex-col gap-2 border-b border-subtle px-4 py-3">
        <div className="flex items-start justify-between gap-4">
          <p className="min-w-0 text-md text-primary">{question.text}</p>
          <StateBadge state={state} />
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <Mono dim title="Content-derived question id. Stable across rounds, which is what makes a follow-up recognisable as the same question.">
            {question.question_id}
          </Mono>
          <span className="text-xs text-muted">{question.department}</span>
          {question.framework_hint ? (
            <span className="text-xs text-muted">{question.framework_hint}</span>
          ) : null}
          {question.source_ref?.sheet ? (
            <Mono dim title="Where this came from in the customer's spreadsheet">
              {question.source_ref.sheet}
              {question.source_ref.row ? `:${question.source_ref.row}` : ''}
            </Mono>
          ) : null}
        </div>
      </header>

      {answer === null ? (
        <div className="px-4 pb-4">
          <p className="border-l-2 border-dashed border-line pl-3 text-sm text-secondary">
            Not yet drafted. This question has been triaged to{' '}
            <span className="text-primary">{question.department}</span> and is waiting for that
            department&rsquo;s engine.
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-col gap-2 px-4">
            <div className="flex items-baseline gap-3">
              <span className="text-xs uppercase tracking-wide text-muted">Answer</span>
              <Mono dim title="The agent that authored this">
                {answer.authored_by}
              </Mono>
              <span className="text-xs text-muted" title={absolute(answer.created_at)}>
                {ago(answer.created_at)}
              </span>
            </div>
            <p
              className={cx(
                'whitespace-pre-wrap text-md leading-relaxed',
                // A quarantined or refused answer is not the customer-facing text and should
                // not be set as though it were.
                state.key === 'quarantined' || state.key === 'no-evidence'
                  ? 'text-secondary'
                  : 'text-primary',
              )}
            >
              {answer.text}
            </p>
            <p className="text-xs text-secondary">{state.meaning}</p>
            <Verification answer={answer} />
          </div>

          <div className="flex flex-col gap-2 px-4">
            <span className="text-xs uppercase tracking-wide text-muted">
              Evidence · {answer.citations.length}
            </span>
            <CitationList citations={answer.citations} />
          </div>

          <div className="px-4 pb-4">
            <ConfidenceMeter answer={answer} />
          </div>
        </>
      )}
    </article>
  );
}

/**
 * Who checked the answer, and what they found.
 *
 * Two agents named on every answer, or an explicit statement that only one was involved.
 * That second case is the one worth rendering: an answer nobody verified and an answer that
 * passed verification look identical if only the passes are shown, and "grounded, and
 * someone who did not write it confirmed that" is a materially stronger claim than
 * "grounded, as far as anyone knows".
 *
 * The unsupported claims are quoted because "partially supported" without the overreaching
 * sentence gives a reviewer nothing to act on.
 */
function Verification({ answer }: { answer: AnswerRow }) {
  const verdict = answer.support ?? 'unknown';
  if (verdict === 'unknown') {
    return (
      <p className="text-xs text-muted">
        Not verified — no separate agent checked this answer against its citations.
      </p>
    );
  }
  const label = verdict.replace(/_/g, ' ');
  return (
    <div className="flex flex-col gap-2 rounded-sm bg-sunken px-3 py-2 shadow-line">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-xs uppercase tracking-wide text-muted">Verified</span>
        <span
          className={cx(
            'text-sm font-medium',
            verdict === 'supported' ? 'text-cited' : 'text-flagged',
          )}
        >
          {label}
        </span>
        <Mono dim title="The agent that checked this. Never the agent that wrote it.">
          {answer.verified_by || 'a separate agent'}
        </Mono>
      </div>
      {verdict !== 'supported' ? (
        <p className="text-xs text-secondary">
          The cited passages do not carry every claim in this answer.
        </p>
      ) : null}
    </div>
  );
}
