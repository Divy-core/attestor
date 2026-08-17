/**
 * Starting a review from the browser: four calls, three of them ours.
 *
 *   1. POST /uploads          → a v4 signed URL, minted by the control plane
 *   2. PUT  <the signed URL>  → the file goes straight to GCS, not through us
 *   3. POST /reviews          → the review record
 *   4. POST /reviews/{id}/rounds → publishes `intake_document` and returns the run id
 *
 * ## Why step 2 does not go through this service
 *
 * A 40MB questionnaire relayed through the Next.js route handler would occupy a Cloud Run
 * instance for the whole transfer, count against its memory, and arrive in exactly the same
 * bucket. The control plane's own docstring makes this argument about itself and it applies
 * identically one layer out. The browser holds the signed URL for thirty minutes and PUTs to
 * `storage.googleapis.com` directly.
 *
 * ## The content type has to match, exactly
 *
 * A v4 signed URL signs the `Content-Type` it was minted for. If the PUT sends a different
 * one, GCS rejects the request with a `SignatureDoesNotMatch` that says nothing about content
 * types, so the type is derived from the **file extension** and the same string is used for
 * both calls. `File.type` is not used for this: browsers report it inconsistently and leave it
 * empty outright for `.xlsx` on some platforms, which would mint a URL for `""` and then PUT
 * something else.
 *
 * ## Progress is reported, not spun
 *
 * Each step reports as it starts, because step 2 is the slow one and a spinner that says
 * nothing for forty seconds during a recorded demo looks like a hang. `XMLHttpRequest` rather
 * than `fetch` for the upload, because it is still the only way to observe upload progress in
 * a browser — `fetch` has no equivalent, request streaming is not available for this, and a
 * byte counter on a large file is the difference between "working" and "stuck".
 */

/** Extension → the exact content type used for BOTH the signed URL and the PUT. */
const CONTENT_TYPES: Record<string, string> = {
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xls: 'application/vnd.ms-excel',
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  doc: 'application/msword',
  csv: 'text/csv',
};

export const ACCEPTED_EXTENSIONS = Object.keys(CONTENT_TYPES);
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.map((e) => `.${e}`).join(',');

/**
 * Only the .xlsx path is parsed deterministically today; PDF and DOCX go through the
 * multimodal tier, which intake reaches for but has not been measured at scale in this build.
 * Named here rather than hidden, so the dialog can say so before someone uploads a PDF and
 * waits.
 */
export const FULLY_SUPPORTED_EXTENSIONS = ['xlsx', 'xls'];

/** A generous ceiling. The questionnaire of record is ~90KB; 40MB is a runaway. */
export const MAX_UPLOAD_BYTES = 40 * 1024 * 1024;

export function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot === -1 ? '' : filename.slice(dot + 1).toLowerCase();
}

export function contentTypeFor(filename: string): string | null {
  return CONTENT_TYPES[extensionOf(filename)] ?? null;
}

export type StartStep = 'signing' | 'uploading' | 'creating' | 'publishing' | 'done';

export type StartProgress = {
  step: StartStep;
  /** 0..1 during `uploading`, null when the step has no measurable progress. */
  fraction: number | null;
};

export type StartRequest = {
  file: File;
  customer: string;
  framework: string;
  residency: string;
};

export type StartResult = {
  reviewId: string;
  roundId: string;
  runId: string;
  dedupKey: string;
  gcsUri: string;
};

/**
 * An error carrying the control plane's own words and the step that failed.
 *
 * The two failures worth telling apart in the UI are the capacity refusal (429, the demo
 * guard doing its job, and the user should be told to wait or approve something) and
 * everything else. `status` is preserved so the dialog can do that rather than showing one
 * generic message.
 */
export class StartError extends Error {
  readonly step: StartStep;
  readonly status: number;

  constructor(step: StartStep, status: number, detail: string) {
    super(detail);
    this.name = 'StartError';
    this.step = step;
    this.status = status;
  }
}

async function post<T>(step: StartStep, path: string, body: unknown): Promise<T> {
  const response = await fetch(`/api/attestor/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload: unknown = await response.json();
      if (payload && typeof payload === 'object' && 'detail' in payload) {
        detail = String((payload as { detail: unknown }).detail);
      }
    } catch {
      // A non-JSON error body is not itself worth reporting over the status it came with.
    }
    throw new StartError(step, response.status, detail);
  }
  return (await response.json()) as T;
}

/**
 * PUT the file to the signed URL, reporting bytes as they go.
 *
 * Resolves on 2xx. A signed-URL rejection is surfaced with GCS's own status, because a 403
 * here means the signature or the content type is wrong and that is a different bug from a
 * network failure.
 */
function put(
  url: string,
  file: File,
  contentType: string,
  onProgress: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('PUT', url, true);
    request.setRequestHeader('Content-Type', contentType);
    request.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    });
    request.addEventListener('load', () => {
      if (request.status >= 200 && request.status < 300) {
        resolve();
        return;
      }
      reject(
        new StartError(
          'uploading',
          request.status,
          request.status === 403
            ? 'Cloud Storage refused the signed URL. It may have expired, or the file type ' +
              'changed after the URL was minted.'
            : `Cloud Storage returned ${request.status} for the upload.`,
        ),
      );
    });
    request.addEventListener('error', () =>
      reject(new StartError('uploading', 0, 'The upload connection failed.')),
    );
    request.addEventListener('abort', () =>
      reject(new StartError('uploading', 0, 'The upload was cancelled.')),
    );
    request.send(file);
  });
}

/**
 * Run the whole flow. Every step reports before it begins.
 *
 * Nothing here polls or waits for the review to progress. `POST .../rounds` returns 202 with a
 * run id and the review advances by Pub/Sub message from that point on — which is why the
 * caller's next move is to navigate to `/reviews/{id}`, where the stream is already open.
 */
export async function startReview(
  { file, customer, framework, residency }: StartRequest,
  onProgress: (progress: StartProgress) => void,
): Promise<StartResult> {
  const contentType = contentTypeFor(file.name);
  if (contentType === null) {
    throw new StartError(
      'signing',
      400,
      `Attestor reads ${ACCEPTED_EXTENSIONS.join(', ')}. ` +
        `"${file.name}" is not one of those.`,
    );
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new StartError(
      'signing',
      400,
      `That file is ${(file.size / 1024 / 1024).toFixed(1)}MB and the ceiling is ` +
        `${MAX_UPLOAD_BYTES / 1024 / 1024}MB.`,
    );
  }

  onProgress({ step: 'signing', fraction: null });
  const upload = await post<{ upload_url: string; gcs_uri: string }>('signing', 'uploads', {
    filename: file.name,
    content_type: contentType,
  });

  onProgress({ step: 'uploading', fraction: 0 });
  await put(upload.upload_url, file, contentType, (fraction) =>
    onProgress({ step: 'uploading', fraction }),
  );

  onProgress({ step: 'creating', fraction: null });
  const review = await post<{ review_id: string }>('creating', 'reviews', {
    customer,
    framework,
    residency,
  });

  onProgress({ step: 'publishing', fraction: null });
  const round = await post<{ round_id: string; run_id: string; dedup_key: string }>(
    'publishing',
    `reviews/${encodeURIComponent(review.review_id)}/rounds`,
    { gcs_uri: upload.gcs_uri, ordinal: 1 },
  );

  onProgress({ step: 'done', fraction: null });
  return {
    reviewId: review.review_id,
    roundId: round.round_id,
    runId: round.run_id,
    dedupKey: round.dedup_key,
    gcsUri: upload.gcs_uri,
  };
}
