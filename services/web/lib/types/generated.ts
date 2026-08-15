// GENERATED FILE -- DO NOT EDIT BY HAND.
//
// Source of truth: packages/attestor-core/src/attestor_core/protocol/
// Regenerate:      make types-gen      (or: python tools/gen_types.py)
// CI fails if this file is stale; see `make types-check`.
//
// The SSE event union below is the most drift-prone surface in the system. It exists
// once, in Python, and is generated here rather than maintained twice.


/** What a worker is being asked to do. */
export type WorkKind = "intake_document" | "triage_questions" | "draft_answer" | "gather_evidence" | "assemble_round" | "close_round" | "open_follow_up" | "resume_after_human" | "timer_fired";

export type WorkEnvelope = {
  message_id: string;
  dedup_key: string;
  attempt?: number;
  occurred_at?: string;
  review_id: string;
  run_id: string;
  round_id?: string | null;
  question_id?: string | null;
  kind: WorkKind;
  payload?: Record<string, unknown>;
};

export type AnswerDrafted = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "answer_drafted";
  question_id: string;
  authored_by: string;
  status: AnswerStatus;
  confidence: Confidence;
  citation_count: number;
  preview?: string | null;
};

/** Lifecycle of a single answer. */
export type AnswerStatus = "draft" | "drafted" | "needs_human" | "flagged_no_evidence" | "quarantined" | "approved" | "delivered";

/** Model Armor refused content. Rendered in red; this is a video beat. */
export type ArmorBlocked = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "armor_blocked";
  decision: ArmorDecision;
  surface: string;
  question_id?: string | null;
  matched_filters?: string[];
  chunk_index?: number | null;
  excerpt?: string | null;
};

/** What to do about a Model Armor verdict. */
export type ArmorDecision = "allow" | "quarantine" | "deny";

export type AwaitingHuman = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "awaiting_human";
  question_id: string;
  reason: string;
  confidence: Confidence;
};

export type CitationAdded = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "citation_added";
  question_id: string;
  document_uri: string;
  document_title: string;
  section?: string | null;
  retrieval_score: number;
};

/** A durable statement to the customer was captured in round 1. */
export type CommitmentRecorded = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "commitment_recorded";
  commitment_id: string;
  question_id: string;
  statement: string;
  round_ordinal: number;
};

/** Confidence in an answer. */
export type Confidence = "high" | "medium" | "low";

/** An answer in round N>1 was evaluated against a prior-round commitment. */
export type ConsistencyChecked = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "consistency_checked";
  question_id: string;
  commitment_id: string;
  prior_statement: string;
  prior_round_ordinal: number;
  verdict: ContradictionVerdict;
  constrained?: boolean;
};

/** Whether a draft answer contradicts a prior-round commitment. */
export type ContradictionVerdict = "no_contradiction" | "possible_contradiction" | "contradiction" | "unknown";

/** Who owns a question, and therefore which corpus may be read to answer it. */
export type Department = "security" | "legal" | "engineering" | "unassigned";

/** Keeps buffering proxies from closing an idle SSE stream. */
export type Heartbeat = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "heartbeat";
};

export type HumanResolved = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "human_resolved";
  question_id: string;
  approved: boolean;
  resolved_by: string;
  edited?: boolean;
};

export type QuestionTriaged = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "question_triaged";
  question_id: string;
  department: Department;
  model: string;
};

export type RoundClosed = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "round_closed";
  round_id: string;
  ordinal: number;
  answered: number;
  flagged: number;
  commitments_recorded: number;
};

export type RunCompleted = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "run_completed";
  duration_ms: number;
  answered: number;
  flagged: number;
  blocked: number;
};

export type RunFailed = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "run_failed";
  error_type: string;
  message: string;
};

export type RunStarted = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "run_started";
  round_id: string;
  ordinal: number;
  question_count: number;
};

/** Tri-state interceptor result for a tool call. */
export type ToolDecision = "allow" | "ask" | "deny";

/** A cross-department access attempt was refused. Also a video beat. */
export type ToolDenied = {
  review_id: string;
  run_id: string;
  seq: number;
  emitted_at?: string;
  type?: "tool_denied";
  agent: string;
  agent_department: Department;
  tool_name: string;
  resource_ref?: string | null;
  decision: ToolDecision;
  reason: string;
};

export type AttestorEvent = {
  event: RunStarted | QuestionTriaged | AnswerDrafted | CitationAdded | ArmorBlocked | ToolDenied | AwaitingHuman | HumanResolved | CommitmentRecorded | ConsistencyChecked | RoundClosed | RunCompleted | RunFailed | Heartbeat;
};

export type HealthResponse = {
  status: string;
  version: string;
};

export type ReadyResponse = {
  status: string;
  version: string;
  firestore: string;
  error?: string | null;
};

export type UploadUrlRequest = {
  filename: string;
  content_type: string;
  size_bytes: number;
};

export type UploadUrlResponse = {
  upload_url: string;
  gcs_uri: string;
  expires_at: string;
};

/** The compliance framework a questionnaire is drawn from. */
export type Framework = "soc2" | "iso27001" | "caiq" | "gdpr" | "bespoke";

/** Data residency demanded by the customer for this review. */
export type Residency = "us" | "eu" | "in" | "any";

export type CreateReviewRequest = {
  customer: string;
  framework?: Framework;
  residency?: Residency;
  gcs_uri: string;
};

export type ReviewSummary = {
  review_id: string;
  customer: string;
  framework: Framework;
  residency: Residency;
  state: string;
  current_round: number;
  created_at: string;
  question_count?: number;
  answered_count?: number;
  flagged_count?: number;
};

export type AnswerDto = {
  question_id: string;
  round_id: string;
  text: string;
  citations?: CitationDto[];
  confidence: Confidence;
  status: AnswerStatus;
  authored_by: string;
  created_at: string;
};

export type CitationDto = {
  document_uri: string;
  document_title: string;
  section?: string | null;
  snippet: string;
  retrieval_score: number;
};

export type QuestionDto = {
  question_id: string;
  text: string;
  department: Department;
  framework_hint?: string | null;
  answer?: AnswerDto | null;
};

export type ReviewDetail = {
  review: ReviewSummary;
  questions?: QuestionDto[];
};

export type ApprovalRequest = {
  question_id: string;
  approved: boolean;
  edited_text?: string | null;
  resolved_by: string;
};

export type ApprovalResponse = {
  question_id: string;
  status: AnswerStatus;
  resumed: boolean;
};

export type RegistryAgentDto = {
  agent_id: string;
  display_name: string;
  resource_name?: string | null;
  department?: Department;
  identity_type?: string | null;
  effective_identity?: string | null;
  agent_framework?: string | null;
  scopes?: string[];
  tools?: string[];
};

export type ArmorEventDto = {
  event_id: string;
  review_id: string;
  run_id: string;
  question_id?: string | null;
  surface: string;
  decision: string;
  matched_filters?: string[];
  chunk_index?: number | null;
  excerpt?: string | null;
  occurred_at: string;
};

export type SpanDto = {
  span_id: string;
  parent_span_id?: string | null;
  name: string;
  start_ms: number;
  duration_ms: number;
  attributes?: Record<string, unknown>;
};

export type TraceDto = {
  trace_id: string;
  run_id: string;
  review_id: string;
  spans?: SpanDto[];
};
