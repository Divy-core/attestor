'use client';

import { useState } from 'react';

import { Mono, cx } from '@/components/ui/primitives';
import { documentName, score } from '@/lib/format';
import type { Citation } from '@/lib/api/client';

/**
 * The provenance chain, rendered as a structural device rather than as a footnote.
 *
 * This is the signature element. Every answer is a claim with a paper trail —
 * question → department → agent → passage → relevance → citation — and the product's whole
 * thesis is that the trail is *checkable*. Provenance a user can open is the product;
 * provenance they have to trust is a claim, and this build has spent five phases refusing to
 * make claims.
 *
 * So a citation expands, in place, to the passage text the retriever actually scored. Not a
 * link out to a document, not a modal that covers the answer it supports: the evidence opens
 * next to the sentence it backs, and both stay on screen.
 *
 * ## Why the left rule and not a card
 *
 * A chain wants to read as one continuous thing. Five boxes read as five unrelated items; a
 * shared vertical rule with entries hanging off it reads as a trail, which is what it is. The
 * rule is also the one place in this interface where a shape is doing rhetorical work, and
 * spending the boldness here is deliberate — everything around it is quiet so this can be
 * loud.
 */
export function CitationList({
  citations,
  dense = false,
}: {
  citations: Citation[];
  dense?: boolean;
}) {
  const [open, setOpen] = useState<number | null>(null);

  if (citations.length === 0) {
    return (
      <p className="border-l-2 border-dashed border-no-evidence py-1 pl-3 text-sm text-secondary">
        No citations. This answer is not backed by a retrieved passage, and the system says so
        rather than presenting it as evidenced.
      </p>
    );
  }

  return (
    <ol className="flex flex-col border-l-2 border-cited">
      {citations.map((citation, index) => {
        const expanded = open === index;
        return (
          <li key={`${citation.document_uri}-${citation.section ?? index}`} className="relative">
            <button
              onClick={() => setOpen(expanded ? null : index)}
              aria-expanded={expanded}
              className={cx(
                'flex w-full items-baseline gap-3 py-1.5 pl-3 pr-2 text-left',
                'transition-colors hover:bg-hover',
                dense ? 'text-xs' : 'text-sm',
              )}
            >
              {/* The score first, monospace and tabular. It is the number a reader scans the
                  list by, and a column of figures that shifts width is unreadable. */}
              <Mono className="w-12 shrink-0 tabular-nums">{score(citation.retrieval_score)}</Mono>
              <span className="min-w-0 flex-1">
                <span className="text-primary">{documentName(citation.document_uri)}</span>
                {citation.section ? (
                  <span className="text-muted"> · {citation.section}</span>
                ) : null}
              </span>
              <span aria-hidden className="shrink-0 text-xs text-muted">
                {expanded ? 'hide' : 'passage'}
              </span>
            </button>

            {expanded ? (
              <div className="ml-3 mb-2 mr-2 rounded border border-subtle bg-sunken p-3">
                {/* The passage as the retriever saw it. This is the whole point: the reader
                    checks the claim against the text that was scored, not against a summary
                    of it. */}
                <p className="whitespace-pre-wrap text-sm text-secondary">{citation.snippet}</p>
                <div className="mt-2 border-t border-subtle pt-2">
                  <Mono dim title="The object this passage was retrieved from">
                    {citation.document_uri}
                  </Mono>
                </div>
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
