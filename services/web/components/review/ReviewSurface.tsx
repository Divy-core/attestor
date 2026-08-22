'use client';

import { useCallback, useEffect, useState } from 'react';

import { ArtifactsPanel } from '@/components/review/ArtifactsPanel';
import { AuditPanel } from '@/components/review/AuditPanel';
import { EvidencePanel } from '@/components/review/EvidencePanel';
import { ExportPanel } from '@/components/review/ExportPanel';
import { ReviewWorkspace } from '@/components/review/ReviewWorkspace';
import { ReviewThread } from '@/components/thread/ReviewThread';
import { cx } from '@/components/ui/primitives';
import type { AnswerRow, QuestionRow } from '@/lib/api/client';
import { TABS, type Tab } from '@/lib/tabs';
import type { ThreadPayload } from '@/lib/types/thread';

/**
 * A review's five views, and the thread is the one it opens on.
 *
 * ## Why the thread is the landing surface and the grid is not
 *
 * The grid was, and it was the wrong choice for a reason worth writing down: 312 rows tells
 * a reader what the system *produced* and nothing about what it *did*. Nobody opening a
 * vendor review at nine in the morning wants to start by reading question one; they want to
 * know what happened overnight, what is waiting on them, and why. That is the thread, and
 * the grid is where you go once you know which row you care about.
 *
 * ## The tab lives in the URL
 *
 * `?tab=evidence&sel=<question id>` is a link a person can send, and "here is what I am
 * looking at" is the most common thing anyone wants to do with a console. Written with
 * `history.replaceState` rather than the router, because the page is `force-dynamic` and a
 * router navigation per tab press is a server round trip for a state change that already
 * happened locally — the defect that made the grid's `j`/`k` keys unusable in Phase 6.5.
 */

export function ReviewSurface({
  reviewId,
  roundId,
  runId,
  initialTab,
  initialThread,
  initialQuestions,
  initialAnswers,
  threadError,
  loadError,
  arrivedByEmail,
  pendingCount,
}: {
  reviewId: string;
  roundId: string;
  runId: string | null;
  initialTab: Tab;
  initialThread: ThreadPayload | null;
  initialQuestions: QuestionRow[];
  initialAnswers: AnswerRow[];
  threadError: string | null;
  loadError: string | null;
  arrivedByEmail: boolean;
  pendingCount: number;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [focus, setFocus] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (tab === 'thread') params.delete('tab');
    else params.set('tab', tab);
    if (focus) params.set('sel', focus);
    const query = params.toString();
    const url = query ? `${window.location.pathname}?${query}` : window.location.pathname;
    if (url !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', url);
    }
  }, [tab, focus]);

  /** From anywhere: open the grid, on this row if one was named. */
  const openQuestions = useCallback((questionId?: string) => {
    if (questionId) setFocus(questionId);
    setTab('questions');
  }, []);

  const openArtifacts = useCallback(() => setTab('artifacts'), []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <nav
        aria-label="This review"
        className="flex shrink-0 items-center gap-1 border-b border-subtle px-4 py-2"
      >
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setTab(name)}
            aria-current={tab === name ? 'page' : undefined}
            className={cx(
              'inline-flex items-center gap-2 rounded-sm px-2 py-1 text-sm capitalize transition-colors',
              tab === name ? 'bg-active font-medium text-primary' : 'text-secondary hover:bg-hover',
            )}
          >
            {name}
            {name === 'questions' && pendingCount > 0 ? (
              <span
                className="font-mono tabular-nums text-muted"
                title={`${pendingCount} answers are held for a person`}
              >
                {pendingCount}
              </span>
            ) : null}
          </button>
        ))}
      </nav>

      <div className="flex min-h-0 flex-1 flex-col">
        {tab === 'thread' ? (
          <ReviewThread
            reviewId={reviewId}
            roundId={roundId}
            runId={runId}
            initialThread={initialThread}
            initialQuestions={initialQuestions}
            initialAnswers={initialAnswers}
            loadError={threadError}
            onOpenQuestions={openQuestions}
            onOpenArtifacts={openArtifacts}
          />
        ) : tab === 'questions' ? (
          <ReviewWorkspace
            roundId={roundId}
            runId={runId}
            initialQuestions={initialQuestions}
            initialAnswers={initialAnswers}
            loadError={loadError}
            focusQuestion={focus}
          />
        ) : tab === 'evidence' ? (
          <EvidencePanel
            questions={initialQuestions}
            answers={initialAnswers}
            onOpenQuestion={openQuestions}
          />
        ) : tab === 'artifacts' ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <ArtifactsPanel reviewId={reviewId} canDeliver={arrivedByEmail} />
            <div className="border-t border-subtle">
              <ExportPanel reviewId={reviewId} roundId={roundId} />
            </div>
          </div>
        ) : (
          <AuditPanel
            reviewId={reviewId}
            questions={initialQuestions}
            focus={focus}
          />
        )}
      </div>
    </div>
  );
}
