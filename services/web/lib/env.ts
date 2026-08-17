/**
 * Server-only configuration. Read once, validated once, at module load.
 *
 * `CONTROL_PLANE_URL` is deliberately NOT a `NEXT_PUBLIC_` variable. Everything the browser
 * needs goes through route handlers under `/api/attestor/*`, which means:
 *
 *   - no CORS configuration on either service, so there is no cross-origin surface to get
 *     wrong at deploy time;
 *   - the control plane's origin is not baked into the client bundle, so it is not in view
 *     source during a recorded demo;
 *   - the proxy is the one place that can add auth later. The control plane is currently
 *     `--allow-unauthenticated`, which is a stated scope decision (multi-tenant auth is out
 *     of scope for this build), not an oversight — and when that changes, it changes here
 *     and nowhere else.
 *
 * Fail-fast rather than defaulting: a UI silently pointed at `localhost:8000` in production
 * renders empty states that look exactly like a system with no reviews in it.
 */

const isBuild = process.env.NEXT_PHASE === 'phase-production-build';

function required(name: string, fallbackDuringBuild: string): string {
  const value = process.env[name];
  if (value && value.length > 0) return value.replace(/\/+$/, '');
  // `next build` runs route modules to collect metadata without the runtime environment
  // present. Throwing there would fail the image build for a variable that will be set on
  // the Cloud Run service; throwing at request time is the check that matters.
  if (isBuild) return fallbackDuringBuild;
  throw new Error(
    `${name} is not set. The web service cannot reach the control plane. ` +
      `Set it on the Cloud Run service, or in .env.local for development.`,
  );
}

export const env = {
  get controlPlaneUrl(): string {
    return required('CONTROL_PLANE_URL', 'http://control-plane.invalid');
  },
  /** Shown as monospace metadata in the footer: visible proof of where this runs. */
  get projectId(): string {
    return process.env.PROJECT_ID ?? 'unknown-project';
  },
  get region(): string {
    return process.env.REGION ?? 'us-central1';
  },
  get revision(): string {
    return process.env.K_REVISION ?? 'local';
  },
  get serviceUrl(): string {
    return process.env.SERVICE_URL ?? '';
  },
} as const;

/** How long a read may take before the UI shows an error instead of hanging. */
export const READ_TIMEOUT_MS = 20_000;
/** Writes are slower: the control plane publishes to Pub/Sub before it answers. */
export const WRITE_TIMEOUT_MS = 30_000;
