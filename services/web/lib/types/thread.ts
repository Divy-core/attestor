/**
 * The Review Thread's wire shapes.
 *
 * ## Why these are hand-written and `generated.ts` is not
 *
 * `lib/types/generated.ts` is emitted from `attestor_core.protocol`, which is FROZEN, and
 * `make types-check` fails CI if it drifts. That is the right treatment for the SSE event
 * union: it is a contract two services must agree on forever.
 *
 * The thread is not that. It is a **view** — `attestor_platform.thread` composes it out of
 * records that already exist, nothing persists in this shape, and it is expected to change
 * as the surface does. Running it through the protocol generator would either freeze a
 * presentation layer or thaw the protocol, and both are worse than one mirrored file with
 * this paragraph at the top of it.
 *
 * The mirror is `attestor_platform/thread/model.py`. Every field below has a matching
 * `as_dict` key there, and the two are small enough to read side by side.
 */

/** One labelled fact inside an expanded block. */
export type ThreadRow = {
  label: string;
  value: string;
  /** Machine output — an id, a score, a resource name, a URI. Rendered monospace. */
  mono: boolean;
  /** Set when the row points at a question. Becomes a jump into the grid. */
  question_id: string | null;
};

/** One block of a post's expansion. `note` carries what the rows cannot say themselves. */
export type ThreadDetail = {
  heading: string;
  rows: ThreadRow[];
  note: string;
};

export type ThreadActionKind =
  | 'approve'
  | 'questions'
  | 'artifacts'
  | 'export'
  | 'connect_gmail';

/** A control rendered inside a post. Never a link to another page to do the work. */
export type ThreadAction = {
  kind: ThreadActionKind;
  label: string;
  count: number;
};

/** A live counter on a working post. */
export type ThreadProgress = {
  label: string;
  done: number;
  total: number;
};

/** One participant saying one thing, with its evidence folded underneath. */
export type ThreadPost = {
  post_id: string;
  /** The agent's own name, or a person's. Same field, deliberately. */
  actor: string;
  kind: string;
  at: string;
  summary: string;
  lines: string[];
  details: ThreadDetail[];
  progress: ThreadProgress[];
  actions: ThreadAction[];
  working: boolean;
  /** The last event folded into this post, when it stands for more than one. */
  through: string | null;
  /** How many audit events this post stands for. */
  events: number;
};

export type ThreadPayload = {
  review_id: string;
  posts: ThreadPost[];
  /** The audit read hit its ceiling. Counts stay exact; the narrative is partial. */
  truncated: boolean;
  events_read: number;
  participants: string[];
  /** The run whose event stream to watch. Read off the trail, not off the round. */
  run_id: string | null;
  /** Whether this review came in on an email thread, and can therefore be replied to. */
  arrived_by_email: boolean;
};

/** What `POST /reviews/{id}/ask` returns: the reply, already composed and already stored. */
export type AskReply = {
  asked_at: string;
  answer: string;
  lines: string[];
  details: ThreadDetail[];
  question_id: string | null;
};

/**
 * What `POST /reviews/{id}/message` returns.
 *
 * One line in, one of three things out. The client does not decide which — the command
 * grammar lives in `attestor_platform.thread.commands` and the server is the only thing
 * that reads it, so there is one copy of it rather than two that drift.
 */
export type MessageReply =
  /** Nothing matched a command. The line was answered from the audit trail. */
  | ({ kind: 'answered' } & AskReply)
  /**
   * An irreversible command was recognised and **nothing has happened**. Re-post with
   * `confirm` set to `action` to go through with it.
   */
  | { kind: 'confirm'; action: string; prompt: string; text: string }
  /** A work envelope is on the bus. */
  | {
      kind: 'dispatched';
      action: string;
      run_id: string;
      round_id: string;
      /** The `WorkKind` published, or `none` for a command that produces no work. */
      work: string;
      dedup_key: string;
      question_id: string | null;
      question_label: string;
    };
