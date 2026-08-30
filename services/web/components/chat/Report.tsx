'use client';

import { CitationList } from '@/components/review/CitationList';
import { Bar } from '@/components/thread/blocks';
import { Label, Mono, cx } from '@/components/ui/primitives';
import type { AnswerRow, QuestionRow, ReviewDetailRow } from '@/lib/api/client';

/**
 * The round as a document.
 *
 * The grid is for an operator working 312 rows: fixed height, virtualised, one line each.
 * This is for a person who wants to read what the system concluded — a summary with the
 * counts, then the answers by department, each with its citations and its verification
 * verdict beside it. The same content the evidence pack PDF carries.
 *
 * Everything here is counted from the answers. Nothing is asked of a model, and no figure
 * on this page exists that the grid would disagree with.
 */

const DEPARTMENTS = ['security', 'legal', 'engineering', 'unassigned'] as const;

/**
 * The verifier writes its engine resource name as the identity that checked the answer.
 * That is right for the trail, where the question is which credential did the work, and
 * unreadable as a byline. The role goes on the line; the resource name stays in the
 * evidence pack and on the thread's separation-of-duties block.
 */
function verifierName(raw: string): string {
  return raw.includes('/reasoningEngines/') ? 'a separate agent identity' : raw;
}

const SUPPORT_LABEL: Record<string, string> = {
  supported: 'supported',
  partially_supported: 'partially supported',
  unsupported: 'unsupported',
  unknown: 'not verified',
};

export function Report({
  review,
  questions,
  answers,
}: {
  review: ReviewDetailRow | null;
  questions: QuestionRow[];
  answers: AnswerRow[];
}) {
  const byQuestion = new Map(answers.map((a) => [a.question_id, a]));
  const labels = new Map(questions.map((q, i) => [q.question_id, `Q${i + 1}`]));

  const answered = answers.filter((a) => a.citations.length > 0).length;
  const held = answers.filter((a) => a.status === 'needs_human').length;
  const approved = answers.filter((a) => a.status === 'approved').length;
  const verified = answers.filter((a) => a.verified_by).length;
  const unsupported = answers.filter(
    (a) => a.support === 'unsupported' || a.support === 'partially_supported',
  ).length;

  const flagged = questions
    .map((question) => ({ question, answer: byQuestion.get(question.question_id) ?? null }))
    .filter(({ answer }) => answer !== null && answer.citations.length === 0);

  const supported = answers.filter((a) => a.support === 'supported').length;
  const partially = answers.filter((a) => a.support === 'partially_supported').length;
  const refuted = answers.filter((a) => a.support === 'unsupported').length;

  const grouped = DEPARTMENTS.map((department) => ({
    department,
    rows: questions
      .filter((q) => q.department === department)
      .map((q) => ({ question: q, answer: byQuestion.get(q.question_id) ?? null }))
      .filter((row) => row.answer !== null),
  })).filter((group) => group.rows.length > 0);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="flex flex-col gap-8 px-6 py-6">
        <header className="flex flex-col gap-3">
          <h2 className="text-lg text-primary">{review?.customer ?? 'This review'}</h2>
          <p className="text-sm text-secondary">
            {review ? `${review.framework.toUpperCase()} · ${review.residency.toUpperCase()} · ` : ''}
            round {review?.current_round ?? 1} · {questions.length} questions
          </p>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 pt-2 sm:grid-cols-3">
            <Figure label="answered with a citation" value={`${answered} of ${questions.length}`} />
            <Figure label="held for a person" value={String(held)} />
            <Figure label="approved by a person" value={String(approved)} />
            <Figure
              label="checked by the verifier"
              value={verified > 0 ? `${verified} of ${answers.length}` : 'none'}
            />
            <Figure
              label="claims the passages did not carry"
              value={verified > 0 ? String(unsupported) : '—'}
            />
          </dl>
          <Bar
            total={questions.length}
            segments={[
              { label: 'answered with a citation', count: answered, className: 'bg-fill-cited' },
              {
                label: 'refused, no supporting evidence',
                count: questions.length - answered,
                className: 'bg-fill-flagged',
              },
            ]}
          />
          {verified > 0 ? (
            <Bar
              total={supported + partially + refuted}
              segments={[
                { label: 'supported', count: supported, className: 'bg-fill-cited' },
                { label: 'partially', count: partially, className: 'bg-fill-degraded' },
                { label: 'unsupported', count: refuted, className: 'bg-fill-denied' },
              ]}
            />
          ) : null}
        </header>

        {flagged.length > 0 ? (
          <section className="flex flex-col gap-4">
            <div className="flex items-baseline justify-between gap-4 border-b border-subtle pb-2">
              <h3 className="text-md text-primary">Not answered</h3>
              <Mono dim>{flagged.length}</Mono>
            </div>
            <p className="max-w-prose text-sm text-secondary">
              The corpus carries no passage supporting these. They are returned unanswered.
            </p>
            <ul className="flex flex-col gap-2">
              {flagged.map(({ question }) => (
                <li key={question.question_id} className="flex items-baseline gap-3">
                  <Mono dim>{labels.get(question.question_id)}</Mono>
                  <span className="min-w-0 flex-1 text-sm text-secondary">{question.text}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {grouped.length === 0 ? (
          <p className="text-sm text-muted">No answer has been drafted in this round.</p>
        ) : null}

        {grouped.map((group) => (
          <section key={group.department} className="flex flex-col gap-6">
            <div className="flex items-baseline justify-between gap-4 border-b border-subtle pb-2">
              <h3 className="text-md capitalize text-primary">{group.department}</h3>
              <Mono dim>
                {group.rows.filter((row) => (row.answer?.citations.length ?? 0) > 0).length} of{' '}
                {group.rows.length} cited
              </Mono>
            </div>
            {group.rows.map(({ question, answer }) => (
              <article key={question.question_id} className="flex flex-col gap-2">
                <div className="flex items-baseline gap-3">
                  <Mono dim>{labels.get(question.question_id)}</Mono>
                  <h4 className="min-w-0 flex-1 text-sm font-medium text-primary">
                    {question.text}
                  </h4>
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-secondary">
                  {answer?.text || 'No answer was drafted.'}
                </p>
                <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                  <span className="text-xs text-muted">
                    {answer?.confidence} confidence · {answer?.status.replace(/_/g, ' ')}
                  </span>
                  <span
                    className={cx(
                      'text-xs',
                      answer?.support === 'supported'
                        ? 'text-cited'
                        : answer?.support === 'unsupported'
                          ? 'text-denied'
                          : 'text-muted',
                    )}
                  >
                    {SUPPORT_LABEL[answer?.support ?? 'unknown']}
                    {answer?.verified_by ? ` by ${verifierName(answer.verified_by)}` : ''}
                  </span>
                </div>
                {answer ? <CitationList citations={answer.citations} dense /> : null}
              </article>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <dd className="text-md tabular-nums text-primary">{value}</dd>
      <dt>
        <Label>{label}</Label>
      </dt>
    </div>
  );
}
