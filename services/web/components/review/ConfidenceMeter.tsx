import { Mono, cx } from '@/components/ui/primitives';
import { percent, score } from '@/lib/format';
import type { AnswerRow } from '@/lib/api/client';

/**
 * Confidence, shown as the signals it was computed from rather than as a verdict.
 *
 * "Confidence is computed from observable signals, never asked of a model" is one of the
 * strongest claims in this build, and until now it existed only in an ADR. A model that is
 * asked how confident it is will tell you, fluently, and be wrong — which is why the number
 * here comes from counting citations and measuring cosine similarity instead.
 *
 * So the component's job is not to render `high` in green. It is to show the four inputs, so
 * that a reader can disagree with the verdict on the evidence in front of them. A confidence
 * score a user cannot audit is exactly the unsourced assertion this system exists to refuse.
 *
 * The bars use one hue at varying length. A score is a magnitude; giving magnitudes different
 * hues invents categories that are not in the data.
 */

function Signal({
  label,
  value,
  detail,
  fraction,
}: {
  label: string;
  value: string;
  detail: string;
  /** 0..1, or null when the signal is a count rather than a proportion. */
  fraction: number | null;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-muted">{label}</span>
        <Mono className="text-xs">{value}</Mono>
      </div>
      <div className="h-1 w-full rounded-full bg-track" role="presentation">
        {fraction === null ? null : (
          <div
            className="h-1 rounded-full bg-scale transition-all duration-state"
            style={{ width: `${Math.max(2, Math.min(100, fraction * 100))}%` }}
          />
        )}
      </div>
      <span className="text-xs text-muted">{detail}</span>
    </div>
  );
}

export function ConfidenceMeter({ answer }: { answer: AnswerRow }) {
  const scores = answer.citations.map((c) => c.retrieval_score);
  const max = scores.length > 0 ? Math.max(...scores) : 0;
  const mean = scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : 0;

  // Cosine over `text-embedding-005` does not use the full 0..1 range: unrelated text in the
  // same domain still scores ~0.6. Rendering a raw 0.69 as a bar 69% full would say "roughly
  // two thirds relevant", which is not what it means. The bars are stretched across the range
  // the measured distribution actually occupies, and the raw figure is printed beside them so
  // the transformation is never the only thing on screen.
  const FLOOR = 0.55;
  const CEILING = 0.85;
  const stretch = (value: number) => (value - FLOOR) / (CEILING - FLOOR);

  return (
    <div className="flex flex-col gap-3 rounded border border-subtle bg-sunken p-3">
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wide text-muted">Confidence</span>
        <span className="text-sm font-medium text-primary">{answer.confidence}</span>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Signal
          label="Citations"
          value={String(answer.citations.length)}
          detail={answer.citations.length === 0 ? 'None. Nothing to stand on.' : 'passages behind this answer'}
          // Five is what the retriever returns at full strength, so it is the natural full bar.
          fraction={Math.min(1, answer.citations.length / 5)}
        />
        <Signal
          label="Top relevance"
          value={score(max)}
          detail="best passage, cosine"
          fraction={scores.length > 0 ? stretch(max) : null}
        />
        <Signal
          label="Mean relevance"
          value={score(mean)}
          detail="across all cited passages"
          fraction={scores.length > 0 ? stretch(mean) : null}
        />
        <Signal
          label="Distinct documents"
          value={String(new Set(answer.citations.map((c) => c.document_uri)).size)}
          detail="corroboration across sources"
          fraction={Math.min(1, new Set(answer.citations.map((c) => c.document_uri)).size / 3)}
        />
      </div>

      <p className="border-t border-subtle pt-2 text-xs text-secondary">
        Computed from these signals. The model is never asked how confident it is.
      </p>
    </div>
  );
}

/** The compact form, for a grid row where four bars will not fit. */
export function ConfidenceInline({ answer }: { answer: AnswerRow }) {
  const scores = answer.citations.map((c) => c.retrieval_score);
  const max = scores.length > 0 ? Math.max(...scores) : 0;
  return (
    <span className="inline-flex items-center gap-2">
      <Mono className={cx('tabular-nums', scores.length === 0 && 'text-muted')}>
        {scores.length > 0 ? score(max) : '--'}
      </Mono>
      <span className="text-xs text-muted">{percent(Math.min(1, answer.citations.length / 5))}</span>
    </span>
  );
}
