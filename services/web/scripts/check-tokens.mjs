/**
 * Fails the build on a hardcoded colour anywhere in the component tree.
 *
 * Deleting Tailwind's default palette (see `tailwind.config.ts`) removes `bg-red-500` as a
 * reachable utility, which covers the common case. It does not cover the three ways a colour
 * can still get in:
 *
 *   1. an arbitrary value  --  `bg-[#ff0000]`, `text-[rgb(255,0,0)]`
 *   2. an inline style     --  `style={{ color: '#ff0000' }}`
 *   3. a raw var reference --  `var(--n-9)` in a component, bypassing the semantic layer
 *
 * The third is the one worth a check of its own. Reaching past `--text-secondary` to
 * `--n-8` compiles, looks right, and quietly breaks the dark theme — because the semantic
 * token is what flips and the ramp step is not. Every one of those is a light-mode bug that
 * only shows up when someone toggles the theme, which on a recording day is too late.
 *
 * `styles/tokens.css` is the one exempt file. It is where the decisions live.
 */

import { readdir, readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SEARCH = ['app', 'components', 'lib'];
const EXEMPT = ['styles/tokens.css'];

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
    pattern: /var\(\s*--n-\d+/g,
    ignore: () => false,
  },
  {
    name: 'raw state hue -- use --state-* or --fill-*',
    pattern: /var\(\s*--hue-/g,
    ignore: () => false,
  },
  {
    name: 'named CSS colour in a style prop',
    pattern: /\b(?:color|background|borderColor|fill|stroke)\s*:\s*['"](?:red|green|blue|orange|purple|yellow|grey|gray|black|white)['"]/g,
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
    if (EXEMPT.includes(rel)) continue;
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

if (findings.length > 0) {
  console.error(`\ncheck-tokens: ${findings.length} hardcoded colour(s)\n`);
  for (const f of findings) {
    console.error(`  ${f.rel}:${f.line}  ${f.match}  -- ${f.rule}`);
  }
  console.error('\nEvery colour decision belongs in styles/tokens.css.\n');
  process.exit(1);
}

console.log('check-tokens: no hardcoded colours');
