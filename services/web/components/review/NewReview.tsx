'use client';

import { useCallback, useId, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

import { Modal } from '@/components/ui/Modal';
import {
  Button,
  Field,
  Mono,
  Progress,
  Select,
  TextInput,
} from '@/components/ui/primitives';
import {
  ACCEPT_ATTRIBUTE,
  ACCEPTED_EXTENSIONS,
  FULLY_SUPPORTED_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  StartError,
  contentTypeFor,
  extensionOf,
  startReview,
  type StartProgress,
} from '@/lib/api/start';
import type { Framework, Residency } from '@/lib/api/client';

/**
 * The front door.
 *
 * Until Phase 6.5 every review on the deployed site had been created by
 * `uv run python tools/run_review.py`, and the homepage said so in its own copy: "nothing is
 * started from it". That was written as a security property and it is a defensible one — but
 * it also told a judge that the interface was a viewer for work a developer ran from a
 * terminal, and the rubric's largest category rewards autonomous action with little
 * hand-holding. You cannot observe hand-holding-free operation if the only way to hand work in
 * is a CLI.
 *
 * So: a customer name, a framework, a residency, and a file. Four fields, one of which is a
 * drop target. Nothing else, because everything else about how the review runs is a decision
 * the system makes.
 *
 * ## What this component does not do
 *
 * It does not upload through this service, does not parse the spreadsheet, and does not wait
 * for the review to progress. `lib/api/start.ts` mints a signed URL, the browser PUTs to GCS
 * directly, and `POST .../rounds` returns a 202 with a run id — at which point the review
 * advances by Pub/Sub message and this component navigates to the page where the stream is
 * already open. The last thing it does is get out of the way.
 */

const FRAMEWORKS: ReadonlyArray<{ value: Framework; label: string }> = [
  { value: 'caiq', label: 'CAIQ (Cloud Security Alliance)' },
  { value: 'soc2', label: 'SOC 2' },
  { value: 'iso27001', label: 'ISO/IEC 27001' },
  { value: 'bespoke', label: 'Bespoke — the customer’s own format' },
];

const RESIDENCIES: ReadonlyArray<{ value: Residency; label: string }> = [
  { value: 'us', label: 'United States' },
  { value: 'eu', label: 'European Union' },
  { value: 'in', label: 'India' },
  { value: 'any', label: 'No residency constraint' },
];

/** What each step says while it is happening. Plain verbs, present tense. */
const STEP_COPY: Record<StartProgress['step'], string> = {
  signing: 'Minting a signed upload URL.',
  uploading: 'Uploading to Cloud Storage',
  creating: 'Creating the review.',
  publishing: 'Publishing intake_document to attestor.work.',
  done: 'Started. Opening the review.',
};

export function NewReviewButton({ tone = 'primary' }: { tone?: 'primary' | 'secondary' }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button tone={tone} onClick={() => setOpen(true)}>
        New review
      </Button>
      {open ? <NewReviewDialog onClose={() => setOpen(false)} /> : null}
    </>
  );
}

/**
 * The same action, in the rail, styled as navigation.
 *
 * It used to sit top-right in accent blue, which is where a marketing page puts its call to
 * action and not where a console puts a primary action. In the rail it reads as the first
 * thing this application does — above Threads, above Fleet — and it stops competing for
 * attention with whatever the page it is on happens to be showing.
 *
 * Not a link, because it opens a dialog rather than navigating, and dressing a button as a
 * link is how a person ends up middle-clicking it into a dead tab. Everything else about it
 * matches the rail items around it.
 */
export function NewReviewRailAction() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 rounded-sm px-2 py-2 text-left text-sm text-primary transition-colors hover:bg-hover"
        title="Hand a questionnaire in"
      >
        <span aria-hidden className="font-mono text-xs text-muted">
          +
        </span>
        New review
      </button>
      {open ? <NewReviewDialog onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function NewReviewDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const ids = useId();
  const fileInput = useRef<HTMLInputElement>(null);

  const [customer, setCustomer] = useState('');
  const [framework, setFramework] = useState<Framework>('caiq');
  const [residency, setResidency] = useState<Residency>('us');
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);

  const [progress, setProgress] = useState<StartProgress | null>(null);
  const [error, setError] = useState<{ detail: string; status: number } | null>(null);

  const busy = progress !== null && progress.step !== 'done';
  const fileProblem = file === null ? null : describeFileProblem(file);
  const ready = customer.trim().length > 0 && file !== null && fileProblem === null;

  const accept = useCallback((incoming: File | null | undefined) => {
    if (!incoming) return;
    setFile(incoming);
    setError(null);
  }, []);

  const submit = useCallback(async () => {
    if (!ready || file === null) return;
    setError(null);
    try {
      const result = await startReview(
        { file, customer: customer.trim(), framework, residency },
        setProgress,
      );
      // `replace` rather than `push`: the dialog's state is not something to come back to with
      // the back button, and the review page is where the work now is.
      router.replace(`/reviews/${result.reviewId}`);
    } catch (cause) {
      setProgress(null);
      setError(
        cause instanceof StartError
          ? { detail: cause.message, status: cause.status }
          : { detail: String(cause), status: 0 },
      );
    }
  }, [ready, file, customer, framework, residency, router]);

  return (
    <Modal
      title="New review"
      description="Hand Attestor a customer questionnaire. The fleet triages it, drafts against the corpus under each department’s own identity, and holds back whatever it will not stand behind alone."
      onClose={busy ? () => {} : onClose}
      footer={
        <>
          <Button tone="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button tone="primary" onClick={() => void submit()} disabled={!ready || busy}>
            {busy ? 'Starting…' : 'Start review'}
          </Button>
        </>
      }
    >
      <Field
        id={`${ids}-customer`}
        label="Customer"
        hint="Whose questionnaire this is. It appears on the export."
      >
        <TextInput
          id={`${ids}-customer`}
          value={customer}
          onChange={setCustomer}
          placeholder="Northwind Traders"
          disabled={busy}
          autoFocus
        />
      </Field>

      <div className="grid grid-cols-2 gap-4">
        <Field
          id={`${ids}-framework`}
          label="Framework"
          hint="A hint for triage, not a parser selector."
        >
          <Select
            id={`${ids}-framework`}
            value={framework}
            options={FRAMEWORKS}
            onChange={setFramework}
            disabled={busy}
          />
        </Field>
        <Field
          id={`${ids}-residency`}
          label="Data residency"
          hint="Recorded on the review and carried into the audit trail."
        >
          <Select
            id={`${ids}-residency`}
            value={residency}
            options={RESIDENCIES}
            onChange={setResidency}
            disabled={busy}
          />
        </Field>
      </div>

      <Field
        id={`${ids}-file`}
        label="Questionnaire"
        hint={
          <>
            {ACCEPTED_EXTENSIONS.map((e) => `.${e}`).join(', ')} up to{' '}
            {MAX_UPLOAD_BYTES / 1024 / 1024}MB. Spreadsheets are parsed cell by cell and
            deterministically; other formats go through the multimodal tier, which this build
            has not measured at scale.
          </>
        }
      >
        <div
          onDragOver={(event) => {
            event.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            if (!busy) accept(event.dataTransfer.files?.[0]);
          }}
          className={[
            'flex flex-col items-start gap-2 rounded-sm border border-dashed px-4 py-6',
            'transition-colors',
            dragging ? 'border-focus bg-hover' : 'border-line bg-sunken',
          ].join(' ')}
        >
          <input
            ref={fileInput}
            id={`${ids}-file`}
            type="file"
            accept={ACCEPT_ATTRIBUTE}
            disabled={busy}
            onChange={(event) => accept(event.target.files?.[0])}
            className="sr-only"
          />
          {file === null ? (
            <>
              <p className="text-sm text-secondary">
                Drop a questionnaire here, or choose a file.
              </p>
              <Button onClick={() => fileInput.current?.click()} disabled={busy}>
                Choose file
              </Button>
            </>
          ) : (
            <>
              <div className="flex w-full min-w-0 items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-sm text-primary">{file.name}</span>
                <Mono dim>{formatBytes(file.size)}</Mono>
              </div>
              <Mono dim>{contentTypeFor(file.name) ?? 'unrecognised type'}</Mono>
              {fileProblem !== null ? (
                <p className="text-sm text-denied">{fileProblem}</p>
              ) : null}
              <Button onClick={() => fileInput.current?.click()} disabled={busy}>
                Choose a different file
              </Button>
            </>
          )}
        </div>
      </Field>

      {progress !== null ? (
        <div className="flex flex-col gap-2 border-t border-subtle pt-3">
          {progress.step === 'uploading' && progress.fraction !== null ? (
            <Progress fraction={progress.fraction} label={STEP_COPY.uploading} />
          ) : (
            <p className="text-sm text-secondary">{STEP_COPY[progress.step]}</p>
          )}
          {/* Named while it happens rather than after. The upload does not transit this
              service, and saying so at the moment it is not happening is the clearest place
              to say it. */}
          <Mono dim>
            {progress.step === 'uploading'
              ? 'PUT → storage.googleapis.com · direct, not through this service'
              : 'control plane → attestor.work → Cloud Run'}
          </Mono>
        </div>
      ) : null}

      {error !== null ? (
        <div className="flex flex-col gap-1 border-l-2 border-denied bg-fill-denied px-3 py-2">
          <h3 className="text-sm font-medium text-primary">
            {error.status === 429
              ? 'Too many reviews are already running.'
              : error.status === 401 || error.status === 503
                ? 'This deployment refused the write.'
                : 'The review could not be started.'}
          </h3>
          {/* The control plane's own words. Its 429 names which reviews are in flight and what
              to do about them, and paraphrasing that into "please try later" throws away the
              only actionable part. */}
          <p className="max-w-prose font-mono text-xs text-secondary">{error.detail}</p>
        </div>
      ) : null}
    </Modal>
  );
}

function describeFileProblem(file: File): string | null {
  if (contentTypeFor(file.name) === null) {
    return `Attestor reads ${ACCEPTED_EXTENSIONS.map((e) => `.${e}`).join(', ')}. This is .${
      extensionOf(file.name) || 'unknown'
    }.`;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `${formatBytes(file.size)} is over the ${MAX_UPLOAD_BYTES / 1024 / 1024}MB ceiling.`;
  }
  if (file.size === 0) {
    return 'That file is empty.';
  }
  if (!FULLY_SUPPORTED_EXTENSIONS.includes(extensionOf(file.name))) {
    // Not an error -- it will run. Said plainly so nobody is surprised by a slower intake.
    return null;
  }
  return null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
