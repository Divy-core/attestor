/**
 * Fails the build on a hardcoded colour anywhere in the component tree, and on the two
 * claims `styles/tokens.css` makes about itself that a human will not keep true by hand.
 *
 * ## Part one: no colour outside the token file
 *
 * Deleting Tailwind's default palette (see `tailwind.config.ts`) removes `bg-red-500` as a
 * reachable utility, which covers the common case. It does not cover the ways a colour can
 * still get in:
 *
 *   1. an arbitrary value  --  `bg-[#ff0000]`, `text-[rgb(255,0,0)]`
 *   2. an inline style     --  `style={{ color: '#ff0000' }}`
 *   3. a raw ramp step     --  `var(--gray-900)` in a component, past the semantic layer
 *
 * The third is the one worth a check of its own. Reaching past `--text-secondary` to
 * `--gray-900` compiles, looks right, and quietly breaks one of the themes — because the
 * semantic token is what flips and the ramp step is what it flips *to*. Every one of those
 * is a bug that only appears when someone toggles the theme, which on a recording day is
 * too late.
 *
 * ## Part two: the token file's own claims
 *
 * `tokens.css` asserts two things in prose that nothing enforced until Phase 7, and this
 * ramp has now been rebuilt three times because of the first one:
 *
 *   - **Every grey is achromatic.** R = G = B, every step, both themes. Two previous
 *     versions of this ramp carried a hue — first blue, then warm — and in both cases the
 *     prose said the neutrals were neutral while the values said otherwise. A claim about
 *     colour that can be checked arithmetically should be.
 *   - **The two dark blocks are identical.** The dark theme is declared twice, once under
 *     `prefers-color-scheme` and once under `[data-theme='dark']`, because the viewer has
 *     three states and only two of them stamp an attribute. Keeping two copies in sync by
 *     hand is a standing hazard whose failure mode is a toggle that produces a subtly
 *     different theme from the system default.
 */

import { readdir, readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SEARCH = ['app', 'components', 'lib'];
const TOKENS = 'styles/tokens.css';

const RULES = [
  {
    name: 'hex colour literal',
    pattern: /#[0-9a-fA-F]{3,8}\b/g,
    // `#` also starts a fragment identifier and an id selector; require it to look like a
    // colour and not be part of a URL or a jsx anchor.
    ignore: (line) => /https?:|href=|url\(|\/#/.test(line),
  },
  {
    name: 'rgb()/hsl() literal',
    pattern: /\b(?:rgba?|hsla?)\(\s*\d/g,
    ignore: () => false,
  },
  {
    name: 'raw ramp step -- use a semantic token',
    pattern: /var\(\s*--(?:gray|background|alpha)-\d+/g,
    ignore: () => false,
  },
  {
    name: 'raw state hue -- use --state-* or --fill-*',
    pattern: /var\(\s*--hue-/g,
    ignore: () => false,
  },
  {
    name: 'named CSS colour in a style prop',
    pattern:
      /\b(?:color|background|borderColor|fill|stroke)\s*:\s*['"](?:red|green|blue|orange|purple|yellow|grey|gray|black|white)['"]/g,
    ignore: () => false,
  },
];

async function* walk(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      yield* walk(full);
    } else if (/\.(tsx?|css|mjs)$/.test(entry.name)) {
      yield full;
    }
  }
}

const findings = [];

for (const dir of SEARCH) {
  for await (const file of walk(join(ROOT, dir))) {
    const rel = relative(ROOT, file).replace(/\\/g, '/');
    const text = await readFile(file, 'utf8');
    text.split('\n').forEach((line, index) => {
      for (const rule of RULES) {
        if (rule.ignore(line)) continue;
        const matches = line.match(rule.pattern);
        if (matches) {
          findings.push({ rel, line: index + 1, rule: rule.name, match: matches[0] });
        }
      }
    });
  }
}

// ---- part two: the token file's own claims -------------------------------------------

const tokens = await readFile(join(ROOT, TOKENS), 'utf8');

/** Expand `#rgb` and `#rrggbb` to three channel values. Returns null for anything else. */
function channels(hex) {
  const value = hex.slice(1);
  if (value.length === 3) {
    return [...value].map((c) => parseInt(c + c, 16));
  }
  if (value.length === 6) {
    return [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16));
  }
  return null;
}

const GREY_TOKEN = /^\s*(--(?:gray-\d+|background-\d+)):\s*(#[0-9a-fA-F]{3,8})\s*;/;
tokens.split('\n').forEach((line, index) => {
  const match = GREY_TOKEN.exec(line);
  if (!match) return;
  const [r, g, b] = channels(match[2]) ?? [];
  if (r === undefined) return;
  if (r !== g || g !== b) {
    findings.push({
      rel: TOKENS,
      line: index + 1,
      rule: `grey ramp must be achromatic (R=G=B); ${match[1]} is ${r},${g},${b}`,
      match: match[2],
    });
  }
});

/** The body of a block, normalised to a comparable string. */
function block(source, selector) {
  const start = source.indexOf(selector);
  if (start === -1) return null;
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        return source
          .slice(open + 1, i)
          .replace(/\/\*[\s\S]*?\*\//g, '')
          .split(/\r?\n/)
          .map((l) => l.trim())
          .filter(Boolean)
          .join('\n');
      }
    }
  }
  return null;
}

const viaMedia = block(tokens, ":root:not([data-theme='light'])");
const viaAttribute = block(tokens, ":root[data-theme='dark']");
if (viaMedia === null || viaAttribute === null) {
  findings.push({
    rel: TOKENS,
    line: 0,
    rule: 'both dark blocks must exist -- one for prefers-color-scheme, one for the toggle',
    match: viaMedia === null ? 'prefers-color-scheme block missing' : 'data-theme block missing',
  });
} else if (viaMedia !== viaAttribute) {
  const a = viaMedia.split('\n');
  const b = viaAttribute.split('\n');
  const first = a.findIndex((line, i) => line !== b[i]);
  findings.push({
    rel: TOKENS,
    line: 0,
    rule: 'the two dark blocks have drifted -- a toggled theme would differ from the system one',
    match: `first difference: ${a[first] ?? '(absent)'} vs ${b[first] ?? '(absent)'}`,
  });
}

if (findings.length > 0) {
  console.error(`\ncheck-tokens: ${findings.length} problem(s)\n`);
  for (const f of findings) {
    console.error(`  ${f.rel}:${f.line}  ${f.match}  -- ${f.rule}`);
  }
  console.error('\nEvery colour decision belongs in styles/tokens.css.\n');
  process.exit(1);
}

console.log('check-tokens: no hardcoded colours; grey ramp achromatic; dark blocks agree');
