/**
 * Fails the build when the product argues with the reader.
 *
 * Three phases running, an interface has shipped carrying sentences addressed to whoever
 * built it. Phase 7 had `tools/gmail_watch.py --apply` printed on the fleet page as an
 * instruction. Phase 8 replaced it and left this, on Connections, under a Slack row:
 *
 *     "Listed rather than omitted. An integration that does not exist and one nobody has
 *      connected look identical when only the working ones are shown, and the difference
 *      is the one a reader is trying to establish."
 *
 * That is a design argument rendered as product copy. It is the same failure each time --
 * the interface documenting the system instead of being it -- and the reason it keeps
 * coming back is that nothing stopped it.
 *
 * **The rule: if a sentence would be at home in an ADR, it does not belong on the screen.**
 * The product shows state and offers actions. The rationale is not lost; it lives in
 * `PROGRESS.md`, in the ADRs, and in the source comments right beside the code -- all of
 * which this check ignores, because none of them are rendered.
 *
 * ## What is scanned
 *
 * Rendered text only: JSX text nodes, and the string props that carry copy. Comments are
 * stripped first, so a paragraph of reasoning above a component is untouched. `aria-label`
 * and `alt` are scanned too -- a screen reader hearing a design rationale is the same
 * defect, delivered to someone with less ability to skip it.
 */

import { readdir, readFile } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SEARCH = ['app', 'components', 'lib'];

/**
 * Phrases that only appear when a sentence is defending a choice.
 *
 * Every one of these was taken from copy that shipped. They are matched on rendered text
 * only, so "because" in a comment is fine and "because" in a tooltip is not.
 */
const BANNED = [
  { phrase: /\brather than\b/i, note: 'names the alternative that was not chosen' },
  { phrase: /\bbecause\b/i, note: 'justifies a design decision' },
  { phrase: /\bwhich is why\b/i, note: 'justifies a design decision' },
  { phrase: /\bthe reason\b/i, note: 'justifies a design decision' },
  { phrase: /\bworth noting\b/i, note: 'addresses the reader as someone to convince' },
  { phrase: /\bdeliberately\b/i, note: 'defends a choice' },
  { phrase: /\bby design\b/i, note: 'defends a choice' },
  { phrase: /\bon purpose\b/i, note: 'defends a choice' },
  { phrase: /\bthat is why\b/i, note: 'justifies a design decision' },
  { phrase: /\bwould (?:be|have|mean)\b/i, note: 'argues about a hypothetical' },
  { phrase: /\binstead of\b/i, note: 'names the alternative that was not chosen' },
  { phrase: /\bnot a design choice\b/i, note: 'defends a choice' },
];

/** Copy-carrying props. Their values are rendered or read aloud. */
const COPY_PROPS =
  /\b(?:hint|title|detail|what|label|note|placeholder|meta|emptyHint|summary|purpose|caption|aria-label|alt)\s*=\s*(?:"([^"]*)"|\{\s*['"]([^'"]*)['"]\s*\})/g;

/**
 * Strip comments without breaking on `https://` or on an apostrophe inside a string.
 *
 * A regex cannot do this: `//` inside a string literal is not a comment, and `'` inside a
 * comment is not a string. So this walks the file once, tracking which of the five states
 * it is in, and blanks out comment spans while leaving offsets intact -- line numbers in a
 * finding have to match the file.
 */
function stripComments(source) {
  const out = source.split('');
  let i = 0;
  let state = 'code';
  let quote = '';
  while (i < source.length) {
    const c = source[i];
    const next = source[i + 1];
    if (state === 'code') {
      if (c === '/' && next === '/') state = 'line';
      else if (c === '/' && next === '*') state = 'block';
      else if (c === '"' || c === "'" || c === '`') {
        state = 'string';
        quote = c;
      }
    } else if (state === 'string') {
      // A backslash escape: skip the pair so an escaped quote cannot close the string.
      if (c.charCodeAt(0) === 92) {
        i += 2;
        continue;
      }
      if (c === quote) state = 'code';
    } else if (state === 'line') {
      if (c === '\n') state = 'code';
      else out[i] = ' ';
    } else if (state === 'block') {
      if (c === '*' && next === '/') {
        out[i] = ' ';
        out[i + 1] = ' ';
        i += 2;
        state = 'code';
        continue;
      }
      if (c !== '\n') out[i] = ' ';
    }
    i += 1;
  }
  return out.join('');
}

/** Every stretch of rendered text in a file, with the line it starts on. */
function renderedText(source) {
  const clean = stripComments(source);
  const found = [];
  const at = (index) => clean.slice(0, index).split('\n').length;

  // JSX text nodes: between a closing `>` and an opening `<`, with no braces or tags in
  // between. Requires two letters in a row, which drops `{' '}`, punctuation and operators.
  for (const m of clean.matchAll(/>([^<>{}]*[A-Za-z]{2}[^<>{}]*)</g)) {
    found.push({ line: at(m.index), text: m[1] });
  }
  for (const m of clean.matchAll(COPY_PROPS)) {
    found.push({ line: at(m.index), text: m[1] ?? m[2] ?? '' });
  }
  return found;
}

async function* walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (/\.tsx?$/.test(entry.name)) yield full;
  }
}

const findings = [];
for (const root of SEARCH) {
  for await (const file of walk(join(ROOT, root))) {
    const source = await readFile(file, 'utf8');
    const rel = relative(ROOT, file).split(sep).join('/');
    if (rel.endsWith('scripts/check-copy.mjs')) continue;
    for (const { line, text } of renderedText(source)) {
      const flat = text.replace(/\s+/g, ' ').trim();
      if (flat.length < 4) continue;
      for (const rule of BANNED) {
        const hit = flat.match(rule.phrase);
        if (hit) findings.push({ rel, line, phrase: hit[0], note: rule.note, flat });
      }
    }
  }
}

if (findings.length > 0) {
  const byFile = new Map();
  for (const f of findings) byFile.set(f.rel, (byFile.get(f.rel) ?? 0) + 1);
  console.error(`\ncheck-copy: ${findings.length} sentence(s) argue with the reader\n`);
  for (const f of findings) {
    console.error(`  ${f.rel}:${f.line}`);
    console.error(`    "${f.flat.slice(0, 120)}${f.flat.length > 120 ? '…' : ''}"`);
    console.error(`    -> "${f.phrase}" ${f.note}\n`);
  }
  console.error('  per file:');
  for (const [rel, n] of [...byFile].sort((a, b) => b[1] - a[1])) {
    console.error(`    ${n.toString().padStart(3)}  ${rel}`);
  }
  console.error(
    '\nThe product shows state and offers actions. Rationale belongs in PROGRESS.md,\n' +
      'in an ADR, or in a comment beside the code -- none of which are scanned.\n',
  );
  process.exit(1);
}

console.log('check-copy: no rationale in rendered text');
