/**
 * The one place that talks to the deployed control plane. Server-side only.
 *
 * Two rules, both learned in the Python half of this build and both worth restating in
 * TypeScript because the failure modes are identical:
 *
 * **Every call has a timeout.** A `fetch` with no `AbortSignal` inherits whatever the
 * platform decides, which on Cloud Run is long enough that a slow dependency looks like a
 * hung page. Production Bar item 1.
 *
 * **A failure is never an empty result.** `ApiError` is thrown, never swallowed into `[]`.
 * "This review has no answers" and "the read failed" render as completely different things,
 * and conflating them is the mistake this codebase has now made five times in Python and
 * intends not to make once in the UI. An empty grid during a recorded demo, caused by a
 * 503 nobody surfaced, is the worst version of it.
 */

import { env, READ_TIMEOUT_MS, WRITE_TIMEOUT_MS } from '@/lib/env';
import type { ApprovalRequest } from '@/lib/types/generated';
import type { ThreadPayload } from '@/lib/types/thread';

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  readonly detail: string;

  constructor(path: string, status: number, detail: string) {
    super(`${path} -> ${status}: ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.path = path;
    this.detail = detail;
  }

  /**
   * What to show a person. No apology, no vagueness: what happened, and what to do.
   */
  get human(): string {
    if (this.status === 404) return 'Not found. It may have been torn down, or never existed.';
    if (this.status === 503) {
      // The registry endpoint is the one that does this, and it does it on purpose.
      return `A backing service is unreachable. ${this.detail}`;
    }
    if (this.status === 0) return `The control plane did not respond. ${this.detail}`;
    return this.detail || `The control plane returned ${this.status}.`;
  }
}

type Method = 'GET' | 'POST';

async function call<T>(
  path: string,
  { method = 'GET', body }: { method?: Method; body?: unknown } = {},
): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = method === 'GET' ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${env.controlPlaneUrl}${path}`, {
      method,
      signal: controller.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      // Firestore is the source of truth and it changes while a review runs. Anything
      // cached here would be a stale grid that looks like a stalled system.
      cache: 'no-store',
    });
  } catch (cause) {
    const reason =
      cause instanceof Error && cause.name === 'AbortError'
        ? `No response within ${Math.round(timeoutMs / 1000)}s.`
        : cause instanceof Error
          ? cause.message
          : String(cause);
    throw new ApiError(path, 0, reason);
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    // FastAPI puts the message in `detail`. Keep the service's own words: on the registry
    // 503 the detail names the host and the HTTP status, which is the whole diagnostic.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload: unknown = await response.json();
      if (payload && typeof payload === 'object' && 'detail' in payload) {
        detail = String((payload as { detail: unknown }).detail);
      }
    } catch {
      // A non-JSON error body (a proxy's HTML 404 page, for instance) is not itself an
      // error worth reporting over the status it came with.
    }
    throw new ApiError(path, response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------------

export const api = {
  listReviews: (limit = 50) => call<ReviewRow[]>(`/reviews?limit=${limit}`),

  /**
   * Every review with the state a person needs to choose which one to open.
   *
   * A separate endpoint from `listReviews` because the counts are Firestore aggregations
   * over the answers collection — cheap, but not free, and a plain listing should not pay
   * for them.
   */
  reviewBoard: (limit = 100, includeArchived = false) =>
    call<ReviewCard[]>(
      `/reviews/board?limit=${limit}&include_archived=${includeArchived ? 'true' : 'false'}`,
    ),
  getReview: (reviewId: string) =>
    call<ReviewDetailRow>(`/reviews/${encodeURIComponent(reviewId)}`),
  listQuestions: (roundId: string) =>
    call<QuestionRow[]>(`/rounds/${encodeURIComponent(roundId)}/questions`),
  listAnswers: (roundId: string) =>
    call<AnswerRow[]>(`/rounds/${encodeURIComponent(roundId)}/answers`),
  listAudit: (reviewId: string, limit = 1000) =>
    call<AuditEvent[]>(`/reviews/${encodeURIComponent(reviewId)}/audit?limit=${limit}`),
  listArmor: (reviewId: string, limit = 200) =>
    call<AuditEvent[]>(`/reviews/${encodeURIComponent(reviewId)}/armor?limit=${limit}`),
  listRegistry: () => call<RegistryAgent[]>('/registry'),

  /**
   * Whether the watched mailbox is actually being watched.
   *
   * Rendered on the fleet page because a lapsed Gmail watch is invisible from outside: it
   * expires after seven days without warning, and a mailbox that has stopped notifying
   * looks exactly like a mailbox nobody has emailed.
   */
  inbox: () => call<InboxStatus>('/inbox'),

  /**
   * Gmail, Drive and Slack: connected or not, which account, which scopes, since when.
   *
   * The page this feeds is the one that removed `tools/gmail_watch.py --apply` from the
   * product. Connecting Gmail registers the watch.
   */
  connections: (probe = true) =>
    call<Connections>(`/connections?probe=${probe ? 'true' : 'false'}`),

  /** Every file this review produced, and where it went. */
  listArtifacts: (reviewId: string) =>
    call<ArtifactRow[]>(`/reviews/${encodeURIComponent(reviewId)}/artifacts`),

  /**
   * The review as a conversation between the agents that worked it.
   *
   * Aggregated by the control plane rather than here. A 312-question round writes ~1,200
   * audit events, and serialising all of them into the page payload so the browser can pick
   * a dozen summaries out of them is half a megabyte of JSON to render fifteen lines. The
   * projection is `attestor_platform.thread`; it reads records that already exist, writes
   * nothing, and calls no model.
   */
  getThread: (reviewId: string, roundId?: string) =>
    call<ThreadPayload>(
      `/reviews/${encodeURIComponent(reviewId)}/thread` +
        (roundId ? `?round_id=${encodeURIComponent(roundId)}` : ''),
    ),

  /**
   * Approve or edit one answer.
   *
   * The control plane **publishes** this rather than applying it, so the dispatcher applies
   * it and a redelivered approval is idempotent rather than usually-fine. The 202 carries
   * the dedup key, which is worth surfacing: it is the thing that makes the second delivery
   * a no-op, and showing it is showing the mechanism.
   */
  approve: (roundId: string, questionId: string, body: ApprovalRequest) =>
    call<{ accepted: boolean; dedup_key: string; run_id: string }>(
      `/rounds/${encodeURIComponent(roundId)}/answers/${encodeURIComponent(questionId)}/approval`,
      { method: 'POST', body },
    ),
} as const;

// ---------------------------------------------------------------------------------
// Row shapes
//
// ## What comes from the generated contract, and what does not
//
// The enums and the SSE event union below are imported from `lib/types/generated.ts`, which
// is generated from `attestor_core.protocol` and fails CI if stale. That is the drift-prone
// surface the generator exists for and it is used here as intended: `AnswerStatus`,
// `Confidence`, `Department`, `Framework`, `Residency`, the `AttestorEvent` union and
// `ArmorEventDto` are all single-sourced from Python.
//
// The *response shapes* of the read endpoints are declared here instead, and that is a
// deviation worth naming rather than hiding.
//
// `generated.ts` also contains a block of DTOs -- `ReviewDetail`, `ReviewSummary`,
// `QuestionDto`, `ApprovalResponse` -- which describe an API surface that
// `control_plane/api.py` does not implement. Compiling this file for the first time in Phase
// 6 is what surfaced it. Two examples:
//
//   generated ReviewDetail      { review, questions }
//   GET /reviews/{id} returns   { ...review fields, rounds: [...] }
//
//   generated ApprovalResponse  { question_id, status, resumed }
//   POST .../approval returns   { accepted, dedup_key, run_id }
//
// Those DTOs were written in Phase 1 as the intended shape, never referenced by any code,
// and never compiled -- Phase 1 recorded `tsc --noEmit` as PARTIAL because there was no
// `package.json` to run it with. So they are not stale copies of a live contract; they are a
// design sketch that the implementation moved away from. Typing this client against them
// would make the UI wrong about a working backend.
//
// The endpoints are what is deployed, tested and driving the demo, so the endpoints win, and
// the shapes they actually return are declared below with `Row` names to keep them visibly
// distinct from the generated `Dto` names. Reconciling the two -- deleting the unused DTOs or
// implementing them -- is a change to the frozen protocol and belongs in Phase 7 with a
// decision logged, not in a UI commit.
// ---------------------------------------------------------------------------------

import type {
  AnswerStatus,
  Confidence,
  Department,
  Framework,
  Residency,
} from '@/lib/types/generated';

export type { AnswerStatus, Confidence, Department, Framework, Residency };

/** Mirrors `attestor_core.state.ReviewState`, which the generator does not emit as a union.
 *  Kept narrow so an unexpected value is a type error at the boundary rather than a silently
 *  unstyled badge. */
export type ReviewState =
  | 'intake'
  | 'triaging'
  | 'drafting'
  | 'awaiting_evidence'
  | 'awaiting_human'
  | 'assembling'
  | 'delivered'
  | 'follow_up'
  // `blocked` was missing from this union until Phase 8, and the omission was found by
  // writing an exhaustive switch over it rather than by seeing one: a blocked review would
  // have rendered with no label at all. It is a real state -- a recoverable halt that
  // remembers where it came from -- and the state machine has always been able to reach it.
  | 'blocked'
  | 'failed';

export type ReviewRow = {
  review_id: string;
  customer: string;
  framework: Framework;
  residency: Residency;
  created_at: string;
  current_round: number;
  state: ReviewState;
  blocked_from: ReviewState | null;
  /** Out of the working set. Hidden by default, behind a control that names the count. */
  archived: boolean;
  /** The date the customer asked for, as they wrote it. Empty when they named none. */
  deadline?: string;
};

/**
 * One review, with enough of its round on it to be a card rather than a row.
 *
 * `counted` is the honest half. When an aggregation fails the counts stay `null` and this
 * is `false`; they are never zeroed. A card reading `0 held` because a read failed would
 * send somebody past the review that is waiting on them.
 */
export type ReviewCard = ReviewRow & {
  round_id: string | null;
  questions: number | null;
  answered: number | null;
  held: number | null;
  counted: boolean;
  opened_at: string | null;
  closed_at: string | null;
};

/** One file the review produced. Drive ids and links are not secrets: the files are shared
 *  with nobody, so a link somebody cannot open is a string. */
export type ArtifactRow = {
  review_id: string;
  round_id: string;
  kind: string;
  file_id: string;
  name: string;
  mime_type: string;
  link: string;
  size_bytes: number;
  produced_by: string;
  produced_at: string;
};

/** `GET /inbox`. Firestore only -- the control plane holds no Gmail credential. */
export type InboxStatus = {
  watching: boolean;
  address: string;
  topic: string;
  history_id: string;
  registered_at: string;
  expires_at: string;
  expires_in_hours: number | null;
  expired: boolean;
};

/** One scope, and what it actually permits. Shown plainly: the narrowness is the point. */
export type ScopeGrant = { scope: string; grants: string };

/** Whether Gmail can deliver to the topic at all, and who is listening. */
export type TopicDelivery = {
  exists: boolean;
  publisher_bound: boolean;
  subscriptions: string[];
  deliverable: boolean;
  note: string;
};

export type GmailConnection = {
  connected: boolean;
  address: string;
  topic: string;
  history_id: string;
  registered_at: string;
  expires_at: string;
  expires_in_hours: number | null;
  expired: boolean;
  scopes: ScopeGrant[];
  refusal: string;
  /** Whether a consent document exists at all. Absent means nothing can be registered. */
  consented?: boolean;
  delivery?: TopicDelivery | null;
  topic_path?: string;
};

export type DriveConnection = {
  connected: boolean;
  scopes: ScopeGrant[];
  /** Drive rides the same consent as Gmail; there is nothing separate to register. */
  shares_consent_with: string;
};

export type SlackConnection = { connected: boolean; scopes: ScopeGrant[]; available: boolean };

/**
 * `GET /connections`.
 *
 * `manageable` is false when the service holding the mailbox credential could not be
 * reached, and `unavailable` says why. That is deliberately not the same as `connected:
 * false` — a page reporting a disconnection caused by a service scaling from zero would be
 * the failure-impersonating-empty shape this codebase keeps finding, on the one page whose
 * job is reporting whether something is connected.
 */
export type Connections = {
  gmail: GmailConnection;
  drive: DriveConnection;
  slack: SlackConnection;
  manageable: boolean;
  unavailable: string;
};

export type RoundRow = {
  round_id: string;
  review_id: string;
  ordinal: number;
  received_at: string;
  closed_at: string | null;
  state: ReviewState;
};

export type ReviewDetailRow = ReviewRow & { rounds: RoundRow[] };

export type SourceRef = {
  sheet?: string | null;
  row?: number | null;
  page?: number | null;
  cell?: string | null;
};

export type QuestionRow = {
  question_id: string;
  text: string;
  raw_text: string;
  department: Department;
  source_ref: SourceRef | null;
  framework_hint: string | null;
};

export type Citation = {
  document_uri: string;
  document_title: string;
  section: string | null;
  snippet: string;
  retrieval_score: number;
  retrieved_at: string;
};

/** Mirrors `attestor_core.domain.enums.SupportVerdict`. */
export type SupportVerdict = 'supported' | 'partially_supported' | 'unsupported' | 'unknown';

export type AnswerRow = {
  question_id: string;
  round_id: string;
  text: string;
  citations: Citation[];
  confidence: Confidence;
  status: AnswerStatus;
  authored_by: string;
  /**
   * The agent that checked this answer against its own citations, and its verdict.
   *
   * Empty and `unknown` for every answer written before the verifier existed, which is the
   * honest reading of those: nobody checked them. The detail pane says so rather than
   * leaving the field out, because an absent verification and a passed one look identical
   * when only the passes are rendered.
   */
  verified_by?: string;
  support?: SupportVerdict;
  created_at: string;
};

/**
 * One row of the compliance plane. `detail` is deliberately `unknown`-valued: the shape
 * varies by `kind` and the trace viewer renders it structurally rather than assuming fields
 * that only some kinds carry.
 */
export type AuditEvent = {
  event_id?: string;
  seq?: number;
  kind: string;
  actor?: string | null;
  review_id?: string;
  run_id?: string | null;
  round_id?: string | null;
  question_id?: string | null;
  recorded_at?: string;
  /** Stamped by `AuditEventRepository.append`. Present on every event the API returns. */
  occurred_at?: string;
  detail?: Record<string, unknown> | null;
};

/**
 * As the Agent Registry list endpoint returns it.
 *
 * `effective_identity` and `identity_type` are `null` on **every** entry from the list call
 * -- measured, not assumed (`docs/proof/registry-listing.json`). The registry page must not
 * render "distinct identities, per the registry" on the strength of this payload; identity
 * comes from the engine resource and is labelled with that source. See `components/registry`.
 */
export type RegistryAgent = {
  agent_id: string;
  display_name?: string | null;
  name?: string | null;
  resource_name?: string | null;
  engine_id?: string | null;
  department?: string | null;
  version?: string | null;
  effective_identity?: string | null;
  identity_type?: string | null;
  agent_framework?: string | null;
  scopes?: string[] | null;
  tools?: string[] | null;
};
