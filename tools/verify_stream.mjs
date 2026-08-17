#!/usr/bin/env node
/**
 * The three ways the event stream fails, exercised against the deployed control plane.
 *
 *     CONTROL_PLANE_URL=https://... node tools/verify_stream.mjs --run-id run-1786974407
 *
 * Phase 6's exit criteria ask for the fallback to engage when the stream goes **silent**, not
 * only when it errors, and for `seq` gap detection to backfill after a reconnect. Both were
 * implemented and reasoned about in `services/web/lib/{sse,poll}.ts`, and neither had been
 * measured — which is the difference between a claim and a result, and the whole standing rule
 * of this repo.
 *
 * This does not use `EventSource`, because there isn't one in Node without a dependency, and
 * adding a browser shim to test browser behaviour would test the shim. It parses the SSE wire
 * format directly, which is also the only way to check the two properties that live *below*
 * `EventSource`:
 *
 *   - whether the heartbeat is a frame a browser could observe at all, or only a comment;
 *   - whether data frames are named, which decides whether `onmessage` ever fires.
 *
 * Both of those were wrong until Phase 6 and neither is visible from inside a client library.
 *
 * ## The three cases
 *
 *   1. `live`     -- open the stream, sit idle past two heartbeat intervals, confirm beats
 *                    arrive and are observable.
 *   2. `silent`   -- `use_listener=false`, which is the control plane's own switch for
 *                    disabling the realtime listener. The stream stays open and delivers
 *                    nothing but heartbeats. A fallback armed on `onerror` sits idle here.
 *   3. `resume`   -- reconnect with `Last-Event-ID` mid-stream and confirm the server resumes
 *                    from that sequence rather than replaying the run or skipping the middle.
 */

const CONTROL = (process.env.CONTROL_PLANE_URL ?? '').replace(/\/+$/, '');
if (!CONTROL) {
  console.error('error: CONTROL_PLANE_URL must be set');
  process.exit(2);
}

const args = process.argv.slice(2);
const runIdIndex = args.indexOf('--run-id');
const RUN_ID = runIdIndex >= 0 ? args[runIdIndex + 1] : null;
if (!RUN_ID) {
  console.error('error: --run-id is required');
  process.exit(2);
}

/** Two heartbeat intervals plus slack: long enough that a missing beat is a real absence. */
const IDLE_SECONDS = Number(args[args.indexOf('--idle') + 1]) || 45;

/**
 * Read an SSE stream for `seconds`, returning what actually came down the wire.
 *
 * Frames are parsed rather than interpreted: `named` counts frames carrying an `event:` line,
 * which is the property that decides whether a browser's `onmessage` fires at all.
 */
async function observe(url, seconds, headers = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), seconds * 1000);
  const result = {
    status: 0,
    openComment: false,
    heartbeatComments: 0,
    heartbeatEvents: 0,
    dataFrames: 0,
    namedFrames: 0,
    seqs: [],
    firstByteMs: null,
    error: null,
  };
  const started = Date.now();

  try {
    const response = await fetch(url, {
      headers: { Accept: 'text/event-stream', ...headers },
      signal: controller.signal,
    });
    result.status = response.status;
    if (!response.body) throw new Error('no response body');

    const decoder = new TextDecoder();
    let buffer = '';
    for await (const chunk of response.body) {
      if (result.firstByteMs === null) result.firstByteMs = Date.now() - started;
      buffer += decoder.decode(chunk, { stream: true });
      // Frames are separated by a blank line.
      let split;
      while ((split = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        classify(frame, result);
      }
    }
  } catch (cause) {
    // An abort is how this harness ends every successful observation.
    if (cause?.name !== 'AbortError') result.error = String(cause);
  } finally {
    clearTimeout(timer);
  }
  return result;
}

function classify(frame, result) {
  const lines = frame.split('\n');
  let named = false;
  let data = null;
  let id = null;

  for (const line of lines) {
    if (line.startsWith(': open')) result.openComment = true;
    else if (line.startsWith(': heartbeat')) result.heartbeatComments += 1;
    else if (line.startsWith('event: ')) {
      named = true;
      if (line.slice(7).trim() === 'heartbeat') result.heartbeatEvents += 1;
    } else if (line.startsWith('data: ')) data = line.slice(6);
    else if (line.startsWith('id: ')) id = line.slice(4).trim();
  }

  if (data === null) return;

  let payload;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }

  // The heartbeat is a named frame AND carries data, and that is deliberate -- it is named
  // precisely so a client can register one listener for it and keep it off the data path.
  // So it must be excluded before counting named DATA frames, or the check reports a
  // correctly-framed stream as broken. It did, on the first run, and the FAIL was this line's
  // fault rather than the control plane's.
  if (payload?.kind === 'heartbeat') return;

  if (named) result.namedFrames += 1;
  result.dataFrames += 1;
  const seq = typeof payload?.seq === 'number' ? payload.seq : Number(id);
  if (Number.isFinite(seq)) result.seqs.push(seq);
}

function line(label, value) {
  console.log(`  ${label.padEnd(38)} ${value}`);
}

const report = { case: 'sse_behaviour', run_id: RUN_ID, control_plane: CONTROL, cases: {} };

console.log('='.repeat(78));
console.log('SSE BEHAVIOUR -- against the deployed control plane');
console.log('='.repeat(78));
console.log(`  run      : ${RUN_ID}`);
console.log(`  idle for : ${IDLE_SECONDS}s\n`);

// -- 1. live -------------------------------------------------------------------------------
console.log('1. LIVE -- open, sit idle past two heartbeat intervals');
const live = await observe(`${CONTROL}/runs/${RUN_ID}/events`, IDLE_SECONDS);
line('status', live.status);
line('immediate ": open" flush', live.openComment);
line('time to first byte', `${live.firstByteMs}ms`);
line('heartbeat comments (proxy flush)', live.heartbeatComments);
line('heartbeat EVENTS (browser-observable)', live.heartbeatEvents);
line('data frames', live.dataFrames);
line('named data frames', `${live.namedFrames} (must be 0)`);
line('highest seq', live.seqs.length ? Math.max(...live.seqs) : '--');

// The two properties a client library cannot see, and both were wrong before Phase 6.
const observableHeartbeat = live.heartbeatEvents > 0;
const unnamedData = live.namedFrames === 0;
report.cases.live = {
  ...live,
  survives_idle: live.error === null && live.status === 200,
  heartbeat_observable_by_eventsource: observableHeartbeat,
  data_frames_reach_onmessage: unnamedData,
};

console.log(
  `\n  ${observableHeartbeat ? 'PASS' : 'FAIL'} a browser watchdog can see the heartbeat` +
    (observableHeartbeat ? '' : ' -- comments never reach JavaScript'),
);
console.log(
  `  ${unnamedData ? 'PASS' : 'FAIL'} data frames reach onmessage` +
    (unnamedData ? '' : ' -- named frames need addEventListener per kind'),
);

// -- 2. silent -----------------------------------------------------------------------------
console.log('\n2. SILENT -- listener disabled, socket open, nothing delivered');
const silent = await observe(
  `${CONTROL}/runs/${RUN_ID}/events?use_listener=false`,
  IDLE_SECONDS,
);
line('status', silent.status);
line('heartbeat EVENTS', silent.heartbeatEvents);
line('transport error', silent.error ?? 'none');
// This is the case the whole design turns on: nothing errored, so an `onerror`-armed fallback
// would have stayed idle, and only a heartbeat-fed watchdog notices anything at all.
const silentlyHealthy = silent.error === null && silent.status === 200;
report.cases.silent = {
  ...silent,
  errored: silent.error !== null,
  detectable_only_by_watchdog: silentlyHealthy && silent.heartbeatEvents > 0,
};
console.log(
  `\n  ${silentlyHealthy ? 'PASS' : 'FAIL'} the stream stays open and reports no error` +
    ' -- an onerror-armed fallback would sit idle here',
);

// -- 3. resume -----------------------------------------------------------------------------
console.log('\n3. RESUME -- reconnect with Last-Event-ID');
const full = await observe(`${CONTROL}/runs/${RUN_ID}/events`, 12);
const midpoint = full.seqs.length > 2 ? full.seqs[Math.floor(full.seqs.length / 2)] : 0;
const resumed = await observe(`${CONTROL}/runs/${RUN_ID}/events`, 12, {
  'Last-Event-ID': String(midpoint),
});
const lowest = resumed.seqs.length ? Math.min(...resumed.seqs) : null;
line('backfilled frames, no resume point', full.seqs.length);
line('resume point sent', midpoint);
line('lowest seq after resume', lowest ?? '--');
// Strictly greater than the resume point: an inclusive resume would re-deliver an event the
// client had already applied, and on a citation that means counting it twice.
const resumesCorrectly = lowest === null || lowest > midpoint;
report.cases.resume = {
  full_backfill_frames: full.seqs.length,
  resume_point: midpoint,
  lowest_seq_after_resume: lowest,
  contiguous: resumed.seqs.every((s, i, all) => i === 0 || s === all[i - 1] + 1),
  resumes_after_the_point: resumesCorrectly,
};
console.log(
  `\n  ${resumesCorrectly ? 'PASS' : 'FAIL'} resumes after the sent sequence rather than replaying it`,
);

const passed =
  observableHeartbeat && unnamedData && silentlyHealthy && resumesCorrectly;
report.pass = passed;
console.log(`\n  RESULT : ${passed ? 'PASS' : 'FAIL'}`);

if (args.includes('--write-proof')) {
  const { writeFileSync } = await import('node:fs');
  const out = 'docs/proof/sse-behaviour.json';
  writeFileSync(out, JSON.stringify(report, null, 2) + '\n', 'utf8');
  console.log(`\nwrote ${out}`);
}

process.exit(passed ? 0 : 1);
