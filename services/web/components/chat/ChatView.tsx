'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useState } from 'react';

import { Composer } from '@/components/chat/Composer';
import { Report } from '@/components/chat/Report';
import { SidePanel, type PanelKind } from '@/components/chat/SidePanel';
import { ArtifactsPanel } from '@/components/review/ArtifactsPanel';
import { AuditPanel } from '@/components/review/AuditPanel';
import { EvidencePanel } from '@/components/review/EvidencePanel';
import { ExportPanel } from '@/components/review/ExportPanel';
import { NewReviewDialog } from '@/components/review/NewReview';
import { ReviewWorkspace } from '@/components/review/ReviewWorkspace';
import { ReviewThread } from '@/components/thread/ReviewThread';
import { Button, cx } from '@/components/ui/primitives';
import type { AnswerRow, QuestionRow, ReviewDetailRow } from '@/lib/api/client';
import type { ThreadPayload } from '@/lib/types/thread';

/**
 * One conversation: the thread, the composer under it, and the panel when it is open.
 *
 * ## What is in the column and what is in the panel
 *
 * The column is the reasoning trail, at a readable measure. The panel is the product of
 * the session — the report, the grid, the evidence, the pack, the raw trail. The split is
 * by *width*: anything that needs more than 768px to be worth reading is a panel, and
 * everything else stays where the reading happens.
 *
 * ## The panel is in the URL
 *
 * `?panel=report` is a link a person can send, and closing it removes the parameter rather
 * than leaving a dead one behind. Written with `history.replaceState`, because the page is
 * `force-dynamic` and a router navigation per panel press is a server round trip for a
 * state change that already happened locally.
 */

export function ChatView({
  reviewId,
  roundId,
  runId,
  review,
  initialThread,
  initialQuestions,
  initialAnswers,
  initialPanel,
  threadError,
  loadError,
  arrivedByEmail,
}: {
  reviewId: string;
  roundId: string;
  runId: string | null;
  review: ReviewDetailRow | null;
  initialThread: ThreadPayload | null;
  initialQuestions: QuestionRow[];
  initialAnswers: AnswerRow[];
  initialPanel: PanelKind | null;
  threadError: string | null;
  loadError: string | null;
  arrivedByEmail: boolean;
}) {
  const router = useRouter();
  const [panel, setPanel] = useState<PanelKind | null>(initialPanel);
  const [focus, setFocus] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [pending, setPending] = useState<File | null>(null);

  const open = useCallback((kind: PanelKind | null, questionId?: string) => {
    if (questionId) setFocus(questionId);
    setPanel(kind);
    const params = new URLSearchParams(window.location.search);
    if (kind === null) params.delete('panel');
    else params.set('panel', kind);
    const query = params.toString();
    window.history.replaceState(
      null,
      '',
      query ? `${window.location.pathname}?${query}` : window.location.pathname,
    );
  }, []);

  const pendingCount = initialAnswers.filter((a) => a.status === 'needs_human').length;

  return (
    <>
      <ReviewThread
        reviewId={reviewId}
        roundId={roundId}
        runId={runId}
        initialThread={initialThread}
        initialQuestions={initialQuestions}
        initialAnswers={initialAnswers}
        loadError={threadError}
        refreshToken={refreshToken}
        onOpenQuestions={(questionId) => open('questions', questionId)}
        onOpenArtifacts={() => open('artifacts')}
        footer={
          <>
            <Composer
              reviewId={reviewId}
              onAttach={setPending}
              onSettled={() => setRefreshToken((n) => n + 1)}
              onPanel={(kind) => open(kind as PanelKind)}
            />
            <nav aria-label="Panels" className="flex flex-wrap items-center gap-1">
              {(['report', 'questions', 'evidence', 'artifacts', 'audit'] as const).map((kind) => (
                <Button
                  key={kind}
                  tone="ghost"
                  small
                  onClick={() => open(panel === kind ? null : kind)}
                  className={cx('capitalize', panel === kind && 'bg-active text-primary')}
                >
                  {kind}
                  {kind === 'questions' && pendingCount > 0 ? (
                    <span className="font-mono tabular-nums text-muted">{pendingCount}</span>
                  ) : null}
                </Button>
              ))}
            </nav>
          </>
        }
      />

      {panel !== null ? (
        <SidePanel
          open={panel}
          onOpen={(kind) => open(kind)}
          onClose={() => open(null)}
          title={panel}
        >
          {panel === 'report' ? (
            <Report review={review} questions={initialQuestions} answers={initialAnswers} />
          ) : panel === 'questions' ? (
            <ReviewWorkspace
              roundId={roundId}
              runId={runId}
              initialQuestions={initialQuestions}
              initialAnswers={initialAnswers}
              loadError={loadError}
              focusQuestion={focus}
            />
          ) : panel === 'evidence' ? (
            <EvidencePanel
              questions={initialQuestions}
              answers={initialAnswers}
              onOpenQuestion={(questionId) => open('questions', questionId)}
            />
          ) : panel === 'artifacts' ? (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <ArtifactsPanel reviewId={reviewId} canDeliver={arrivedByEmail} />
              <div className="border-t border-subtle">
                <ExportPanel reviewId={reviewId} roundId={roundId} />
              </div>
            </div>
          ) : (
            <AuditPanel reviewId={reviewId} questions={initialQuestions} focus={focus} />
          )}
        </SidePanel>
      ) : null}

      {pending !== null ? (
        <NewReviewDialog
          file={pending}
          onClose={() => setPending(null)}
          onStarted={(next) => {
            setPending(null);
            router.push(`/reviews/${next}`);
          }}
        />
      ) : null}
    </>
  );
}
