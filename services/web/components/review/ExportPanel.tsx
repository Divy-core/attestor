'use client';

import { useCallback, useEffect, useState } from 'react';

import { Button, Mono } from '@/components/ui/primitives';

/**
 * The deliverable, and the one control on this page that hands something back.
 *
 * A vendor security review ends when the completed spreadsheet goes to the customer. Every
 * other page in this console shows what the fleet did; this is where the work leaves the
 * system. It matters more than it looks: until Phase 6.5 the answers lived in Firestore and
 * stopped there, which meant Attestor automated the hard part of a workflow and then declined
 * to finish it.
 *
 * ## Two formats, because reviewers ask for two things
 *
 * The workbook is the customer's own file with the answers written into the rows the questions
 * came from — directly returnable. The evidence pack is the PDF a security reviewer reads when
 * they want to check a claim rather than trust it: every answer with its passages, sections and
 * relevance scores.
 *
 * ## Why the manifest exists
 *
 * The counts are fetched before the click. A button that promises a file the system cannot
 * produce is worse than a disabled one that says why — and the workbook genuinely cannot be
 * produced for a round whose source questionnaire was never recorded, while the evidence pack
 * can. Rendering both as equally available and letting one 409 would be the kind of thing that
 * happens on camera.
 *
 * ## Why these are plain links
 *
 * A normal `<a href>`, not a fetch-and-blob. The browser streams the response straight to disk
 * without holding a 300-page PDF in a tab, and `Content-Disposition` from the service names the
 * file. They point at this app's own `/api/attestor/export/...` route, which streams from the
 * control plane rather than buffering — see that handler for why the origin stays server-side
 * now that it accepts writes.
 */

type Manifest = {
  round_id: string;
  questions: number;
  answered: number;
  cited: number;
  sendable: number;
  human_approved: number;
  by_release_state: Record<string, number>;
  workbook_available: boolean;
  source: string;
  release_rule: string;
};

type Props = {
  reviewId: string;
  roundId: string;
};

export function ExportPanel({ reviewId, roundId }: Props) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch(
        `/api/attestor/reviews/${encodeURIComponent(reviewId)}/export/manifest` +
          `?round_id=${encodeURIComponent(roundId)}`,
        { cache: 'no-store' },
      );
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null);
        const detail =
          payload && typeof payload === 'object' && 'detail' in payload
            ? String((payload as { detail: unknown }).detail)
            : `${response.status} ${response.statusText}`;
        setError(detail);
        return;
      }
      setError(null);
      setManifest((await response.json()) as Manifest);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [reviewId, roundId]);

  useEffect(() => {
    void load();
  }, [load]);

  const href = (format: 'xlsx' | 'pdf') =>
    `/api/attestor/export/${encodeURIComponent(reviewId)}` +
    `?format=${format}&round_id=${encodeURIComponent(roundId)}`;

  if (error !== null) {
    return (
      <div className="flex flex-col gap-1 px-4 py-3">
        <h3 className="text-sm font-medium text-primary">This round cannot be exported yet.</h3>
        <p className="max-w-prose font-mono text-xs text-secondary">{error}</p>
      </div>
    );
  }

  if (manifest === null) {
    return (
      <div className="px-4 py-3">
        <p className="text-sm text-muted">Reading what this round contains…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <Figure label="Questions" value={manifest.questions} />
        <Figure label="Answered" value={manifest.answered} />
        <Figure label="Cited" value={manifest.cited} />
        <Figure label="Sendable" value={manifest.sendable} />
        <Figure label="Human-approved" value={manifest.human_approved} />
      </div>

      <p className="max-w-prose text-sm text-secondary">{manifest.release_rule}</p>

      <ul className="flex flex-col gap-1">
        {Object.entries(manifest.by_release_state)
          .sort((a, b) => b[1] - a[1])
          .map(([state, count]) => (
            <li key={state} className="flex items-baseline gap-3 text-sm">
              <span className="w-12 shrink-0 text-right font-mono tabular-nums text-primary">
                {count}
              </span>
              <span className="text-secondary">{state}</span>
            </li>
          ))}
      </ul>

      <div className="flex flex-wrap items-center gap-2 border-t border-subtle pt-3">
        {manifest.workbook_available ? (
          <a
            href={href('xlsx')}
            className="inline-flex items-center rounded-sm border border-primary bg-primary px-3 py-1 text-sm text-inverse no-underline transition-colors hover:opacity-90"
          >
            Download the completed workbook
          </a>
        ) : (
          <span className="max-w-prose text-sm text-secondary">
            The workbook needs the questionnaire this round was started from, and no source is
            recorded for it. The evidence pack does not.
          </span>
        )}
        <a
          href={href('pdf')}
          className="inline-flex items-center rounded-sm shadow-line-strong bg-surface px-3 py-1 text-sm text-primary no-underline transition-colors hover:bg-hover"
        >
          Download the evidence pack
        </a>
        <Button tone="ghost" onClick={() => void load()}>
          Refresh counts
        </Button>
      </div>

      <Mono dim>
        source of the questionnaire: {manifest.source} · round {manifest.round_id}
      </Mono>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
      <span className="font-mono text-lg tabular-nums text-primary">{value}</span>
    </div>
  );
}
