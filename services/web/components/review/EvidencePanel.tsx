'use client';

import { useMemo, useState } from 'react';

import { Empty, Label, Meter, Mono, cx } from '@/components/ui/primitives';
import type { AnswerRow, QuestionRow } from '@/lib/api/client';
import { documentName, score } from '@/lib/format';

/**
 * The corpus, as this round actually used it.
 *
 * ## Why this is a tab and not a paragraph somewhere
 *
 * "Grounded in your own documents" is the claim the whole system rests on, and until now it
 * was only checkable one answer at a time — open a row, read its citations, repeat 312
 * times. This inverts it: every document the round stood on, how many answers leaned on it,
 * how strongly, and which answers those were.
 *
 * That view answers two questions nothing else here could. **Which parts of the corpus are
 * carrying the review** — if four documents supply two hundred answers, the coverage is
 * narrower than the answer count suggests. And **what was never used** is visible by its
 * absence, which is the honest reading of a policy library that half the questions could
 * not be answered from.
 *
 * ## Everything here is counted from the citations on the answers
 *
 * No separate read, no second source. A document appears because an answer cites it, the
 * scores are the retrieval scores stored on that citation, and the answer count is a length.
 * If this panel and the grid ever disagreed, one of them would be inventing something.
 */

type Source = {
  uri: string;
  title: string;
  sections: Set<string>;
  answers: string[];
  scores: number[];
  snippet: string;
};

export function EvidencePanel({
  questions,
  answers,
  onOpenQuestion,
}: {
  questions: QuestionRow[];
  answers: AnswerRow[];
  onOpenQuestion?: (questionId: string) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);

  const { sources, cited, uncited } = useMemo(() => {
    const index = new Map<string, Source>();
    let withCitation = 0;
    for (const answer of answers) {
      if (answer.citations.length > 0) withCitation += 1;
      for (const citation of answer.citations) {
        const existing = index.get(citation.document_uri);
        const source: Source = existing ?? {
          uri: citation.document_uri,
          title: citation.document_title || documentName(citation.document_uri),
          sections: new Set<string>(),
          answers: [],
          scores: [],
          snippet: citation.snippet,
        };
        if (citation.section) source.sections.add(citation.section);
        if (!source.answers.includes(answer.question_id)) source.answers.push(answer.question_id);
        source.scores.push(citation.retrieval_score);
        index.set(citation.document_uri, source);
      }
    }
    const ordered = [...index.values()].sort((a, b) => b.answers.length - a.answers.length);
    return {
      sources: ordered,
      cited: withCitation,
      uncited: answers.length - withCitation,
    };
  }, [answers]);

  const questionText = useMemo(
    () => new Map(questions.map((question) => [question.question_id, question.text])),
    [questions],
  );

  if (answers.length === 0) {
    return (
      <Empty
        title="No answer in this round has been drafted yet."
        hint="Documents appear here as answers cite them. Nothing has been retrieved, and nothing has failed."
      />
    );
  }

  if (sources.length === 0) {
    return (
      <Empty
        title="No answer in this round cites a document."
        hint={`All ${answers.length} answers are flagged rather than answered — the corpus carried nothing that supported them. That is the system declining to answer, not an error.`}
      />
    );
  }

  const open = sources.find((source) => source.uri === selected) ?? null;

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,5fr)_minmax(0,5fr)]">
      <div className="flex min-h-0 flex-col border-r border-subtle">
        <div className="flex shrink-0 flex-wrap items-baseline gap-x-6 gap-y-2 border-b border-subtle px-4 py-3">
          <span className="text-sm text-secondary">
            <Mono>{sources.length}</Mono> documents carried{' '}
            <Mono>{cited}</Mono> of <Mono>{answers.length}</Mono> answers
          </span>
          {uncited > 0 ? (
            <span className="text-xs text-muted">
              {uncited} answers cite nothing and are flagged rather than answered.
            </span>
          ) : null}
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {sources.map((source) => {
            const mean =
              source.scores.reduce((total, value) => total + value, 0) / source.scores.length;
            return (
              <li key={source.uri}>
                <button
                  type="button"
                  onClick={() => setSelected(source.uri)}
                  className={cx(
                    'flex w-full flex-col gap-1 border-b border-subtle px-4 py-3 text-left',
                    source.uri === selected ? 'bg-active' : 'hover:bg-hover',
                  )}
                >
                  <span className="flex items-baseline gap-3">
                    <span className="min-w-0 flex-1 truncate text-sm text-primary">
                      {source.title}
                    </span>
                    <Mono>{source.answers.length}</Mono>
                    <span className="text-xs text-muted">answers</span>
                  </span>
                  <span className="flex items-center gap-3">
                    <Meter
                      value={mean}
                      label={`mean relevance ${score(mean)}`}
                      className="w-16"
                    />
                    <Mono dim>{score(mean)} mean</Mono>
                    {source.sections.size > 0 ? (
                      <span className="truncate text-xs text-muted">
                        {source.sections.size} section{source.sections.size === 1 ? '' : 's'}
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="min-h-0 overflow-y-auto">
        {open === null ? (
          <Empty
            title="No document selected"
            hint="Pick one to see the sections it was read from, a passage as retrieved, and every answer that leaned on it."
          />
        ) : (
          <div className="flex flex-col gap-6 p-4">
            <div className="flex flex-col gap-2">
              <h3 className="text-md text-primary">{open.title}</h3>
              <Mono dim>{open.uri}</Mono>
            </div>

            {open.sections.size > 0 ? (
              <div className="flex flex-col gap-2">
                <Label>sections read</Label>
                <ul className="flex flex-wrap gap-2">
                  {[...open.sections].map((section) => (
                    <li key={section} className="text-xs text-secondary">
                      {section}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="flex flex-col gap-2">
              <Label>a passage, as it was retrieved</Label>
              <p className="max-w-prose whitespace-pre-wrap text-sm text-secondary">
                {open.snippet}
              </p>
            </div>

            <div className="flex flex-col gap-2">
              <Label>answers that stood on it</Label>
              <ul className="flex flex-col">
                {open.answers.slice(0, 40).map((questionId) => (
                  <li key={questionId}>
                    <button
                      type="button"
                      onClick={() => onOpenQuestion?.(questionId)}
                      className="w-full border-b border-subtle px-2 py-2 text-left text-sm text-secondary hover:bg-hover hover:text-primary"
                    >
                      {questionText.get(questionId) ?? questionId}
                    </button>
                  </li>
                ))}
              </ul>
              {open.answers.length > 40 ? (
                <p className="text-xs text-muted">
                  {open.answers.length - 40} further answers not listed here.
                </p>
              ) : null}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
