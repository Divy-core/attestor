import { Mono, cx } from '@/components/ui/primitives';
import { absolute } from '@/lib/format';
import type { AuditEvent } from '@/lib/api/client';

/**
 * The blocked payload, rendered legibly, with where in the document it sat.
 *
 * This is four seconds of the video and it is the beat that lands hardest, so it has to look
 * good at 1080p and it has to be readable at rest — no hover, no expand-to-see. A viewer who has
 * to be told what they are looking at is a viewer who has already moved on.
 *
 * ## Why `chunk_index` is the interesting field
 *
 * Model Armor's prompt-injection filter caps at 512 tokens. A vendor questionnaire is far longer
 * than that, so an attacker's natural move is to bury the instruction deep in the document where
 * a naive single-call screen never looks. `screen_long_text()` chunks at ~450 tokens with ~50
 * overlap and fans out, aggregating to the strictest verdict — and `chunk_index` is the receipt:
 * it says which window caught it, which is to say roughly how far into the document the payload
 * was hiding.
 *
 * Showing the index turns "Model Armor blocked it" into "Model Armor blocked it at chunk 3 of a
 * document that would have defeated a single 512-token call". That second sentence is the one
 * worth marks, and it is the difference between using a managed service and knowing its limits.
 *
 * ## Not a diff of two texts
 *
 * The name in the plan of record is `InjectionDiff`, and the thing worth showing is not
 * before-and-after prose — it is the *contrast* between what the document claimed to be and the
 * instruction hidden inside it. So the payload is set apart from the surrounding cell text by a
 * rule and a fill, and the matched filters are named. A character-level diff of a questionnaire
 * cell would be noise.
 */
export function InjectionDiff({ event }: { event: AuditEvent }) {
  const detail = event.detail ?? {};
  const excerpt = typeof detail['excerpt'] === 'string' ? detail['excerpt'] : '';
  const surface = typeof detail['surface'] === 'string' ? detail['surface'] : 'unknown';
  const decision = typeof detail['decision'] === 'string' ? detail['decision'] : 'deny';
  const chunkIndex = typeof detail['chunk_index'] === 'number' ? detail['chunk_index'] : null;
  const filters = Array.isArray(detail['matched_filters'])
    ? (detail['matched_filters'] as unknown[]).map(String)
    : [];
  const documentTitle =
    typeof detail['document_title'] === 'string' ? detail['document_title'] : null;
  const documentUri = typeof detail['document_uri'] === 'string' ? detail['document_uri'] : null;

  return (
    <article className="flex flex-col gap-3 rounded shadow-line bg-surface">
      <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-subtle px-4 py-3">
        <div className="flex items-baseline gap-3">
          <span className="text-sm font-medium text-primary">
            {surface === 'tool_output' ? 'Tool poisoning' : 'Prompt injection'}
          </span>
          <span className="inline-flex items-center gap-2 rounded-sm shadow-line bg-fill-quarantined px-2 py-1 text-xs text-quarantined">
            <span aria-hidden className="fill-hatched inline-block h-2 w-2 rounded-sm border border-current" />
            {decision}
          </span>
        </div>
        <span className="text-xs text-muted" title={absolute(event.recorded_at)}>
          {absolute(event.recorded_at)}
        </span>
      </header>

      <div className="flex flex-wrap gap-x-6 gap-y-2 px-4">
        <Meta label="Surface" value={surface} />
        <Meta
          label="Chunk"
          value={chunkIndex === null ? 'single call' : `#${chunkIndex}`}
          hint={
            chunkIndex === null
              ? 'Short enough for one 512-token screen.'
              : 'Which ~450-token window caught it. The filter caps at 512 tokens; this document was longer.'
          }
        />
        {event.question_id ? <Meta label="Question" value={event.question_id} /> : null}
        {documentTitle ? <Meta label="Document" value={documentTitle} hint={documentUri ?? undefined} /> : null}
      </div>

      {filters.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 px-4">
          <span className="text-xs uppercase tracking-wide text-muted">Matched</span>
          {filters.map((filter) => (
            <span
              key={filter}
              className="rounded-sm shadow-line bg-fill-denied px-2 py-1 font-mono text-xs text-denied"
            >
              {filter}
            </span>
          ))}
        </div>
      ) : null}

      {excerpt.length > 0 ? (
        <div className="px-4 pb-4">
          <span className="text-xs uppercase tracking-wide text-muted">
            The payload, as submitted
          </span>
          {/*
            The blocked text, verbatim, monospaced, on the quarantine fill with a hatched left
            rule. Rendered as text and never as markup: this string is hostile input, and the
            one thing an injection viewer must not do is interpret what it is displaying.
          */}
          <pre
            className={cx(
              'mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded',
              'border-l-2 border-quarantined bg-fill-quarantined px-3 py-2',
              'font-mono text-sm text-primary',
            )}
          >
            {excerpt}
          </pre>
          <p className="mt-2 max-w-prose text-sm text-secondary">
            Blocked before it reached a model. The question was quarantined and the rest of the
            questionnaire continued — one hostile cell does not fail a 312-question review.
          </p>
        </div>
      ) : null}
    </article>
  );
}

function Meta({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
      <Mono title={hint} className="truncate text-sm">
        {value}
      </Mono>
      {hint ? <span className="max-w-xs text-xs text-muted">{hint}</span> : null}
    </div>
  );
}

/** One row, for the list above the detail. */
export function ArmorEventRow({
  event,
  selected,
  onSelect,
}: {
  event: AuditEvent;
  selected: boolean;
  onSelect: () => void;
}) {
  const detail = event.detail ?? {};
  const surface = typeof detail['surface'] === 'string' ? detail['surface'] : 'unknown';
  const chunkIndex = typeof detail['chunk_index'] === 'number' ? detail['chunk_index'] : null;

  return (
    <button
      onClick={onSelect}
      aria-current={selected ? 'true' : undefined}
      className={cx(
        'flex w-full items-center gap-3 border-b border-subtle px-4 py-2 text-left',
        'transition-colors',
        selected ? 'bg-active' : 'hover:bg-hover',
      )}
    >
      <span
        aria-hidden
        className="fill-hatched inline-block h-2 w-2 shrink-0 rounded-sm border border-current text-quarantined"
      />
      <span className="min-w-0 flex-1 truncate text-sm text-primary">
        {surface === 'tool_output' ? 'Tool poisoning' : 'Prompt injection'}
        {event.question_id ? <span className="text-muted"> · {event.question_id}</span> : null}
      </span>
      <Mono dim className="shrink-0">
        {chunkIndex === null ? 'single' : `chunk ${chunkIndex}`}
      </Mono>
    </button>
  );
}
