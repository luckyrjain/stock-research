#!/usr/bin/env node
// Design-system CI gate (ENF-01 / ENF-02, design.md §19) — "a rule nothing
// checks is a preference." Catches the exact class of bug that let the
// primary CTA glow in a retired brand color for months (a raw hex nothing
// else could see) and a handful of other rules a TS compiler can't catch.
//
// Deliberately narrow: only checks that are (a) explicitly named in §19 and
// (b) hold cleanly against the actual shipped app today, verified by running
// this exact script against the repo before wiring it into CI.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOTS = ['app', 'components'];
const EXT_RE = /\.(tsx?|css)$/;

// SRC-03 / §2: the only files allowed to carry a raw hex literal.
const HEX_EXCEPTIONS = new Set([
  'app/globals.css',
  'app/layout.tsx',
  'app/manifest.ts',
  'app/icon.tsx',
  'app/apple-icon.tsx',
  'app/manifest-icons/[size]/route.tsx',
]);

// COLOR-05's "non-text only" carve-out for sub-floor muted — this is an SVG
// polyline stroke color, not text a user reads.
const SUBFLOOR_MUTED_EXCEPTIONS = new Set([
  'components/ema-chart.tsx',
]);

// COLOR-03's ladder. Verified against the real, shipped app (not just the
// doc's original draft): border matched the list exactly at 113/113 call
// sites; fill was missing /15 (8 legitimate chip/badge-active-state uses)
// and had exactly one non-issue (a progress-stepper connector line's own
// fill, out of the ladder's scope entirely per §7 — allow-listed below by
// file:line rather than widening the ladder for one non-tinted-surface use).
const LADDER_FILL = new Set([5, 8, 10, 12, 15, 20, 30]);
const LADDER_BORDER = new Set([15, 20, 25, 30, 40]);
const TINT_OPACITY_EXCEPTIONS = new Set([
  // Pipeline progress-stepper connector line, not a tinted surface (§7's
  // signal-toned-fill convention, not COLOR-03's).
  'app/market-picks/page.tsx:148',
]);

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    const s = statSync(p);
    if (s.isDirectory()) walk(p, out);
    else if (EXT_RE.test(entry)) out.push(p);
  }
  return out;
}

// Strips comments before the hex scan so a rationale comment ("was #6b7fa8,
// now #8093bd") doesn't trip ENF-01 — the rule is about rendered styles, not
// prose explaining a token's history. Good enough for a lint gate, not a
// full parser: doesn't handle a `//` or `/*` inside a string literal, which
// doesn't occur in this codebase's actual color-related lines today.
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/.*$/gm, '');
}

let failures = [];

function scan(file, relPath) {
  const raw = readFileSync(file, 'utf8');
  const stripped = stripComments(raw);

  function report(re, message, { source = stripped, allow } = {}) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(source))) {
      if (allow && allow(m)) continue;
      const lineNo = source.slice(0, m.index).split('\n').length;
      failures.push(`${relPath}:${lineNo}  ${message}  \`${m[0]}\``);
    }
  }

  if (!HEX_EXCEPTIONS.has(relPath)) {
    // Excludes an HTML numeric character reference (&#123;) — valid hex
    // digits by coincidence, not a color.
    report(/#[0-9a-fA-F]{3,8}\b/g, 'ENF-01 raw hex outside the token classes (SRC-03)', {
      allow: m => stripped[m.index - 1] === '&',
    });
  }

  report(/\btext-white\b/g, 'ENF-02 text-white on a solid fill (COLOR-06 — use text-bg)');
  report(/(^|[\s"'`{])dark:/g, 'ENF-02 dark: variant (dark theme only, §1)');
  report(/\bmotion-reduce:/g, 'ENF-02 motion-reduce: variant (global rule already handles this, §8)');
  report(/\bfocus:outline-none\b/g, 'ENF-02 focus:outline-none (A11Y-15 — banned, breaks the global focus ring)');
  report(/\bz-(\[[^\]]+\]|\d+)\b/g, 'ENF-02 z-index outside {10,20,40,50} (§13 ELEV-01)', {
    allow: m => ['10', '20', '40', '50'].includes(m[1]),
  });

  if (!SUBFLOOR_MUTED_EXCEPTIONS.has(relPath)) {
    report(/\btext-muted\/(\d+)\b/g, 'ENF-02 sub-floor text-muted opacity (COLOR-05 — floor is /60)', {
      allow: m => Number(m[1]) >= 60,
    });
  }

  // Off-ladder tint opacity (COLOR-03). A leading Tailwind variant chain
  // (`hover:`, `focus:`, `group-hover:`, ...) means this is a state
  // transition on an already-solid fill, not a base tint — out of scope,
  // not an exception to the rule.
  report(/(?:^|[\s"'`{])((?:[a-z-]+:)*)(bg|border)-(buy|sell|hold|accent)\/(\d+)\b/g,
    'ENF-02 off-ladder tint opacity (COLOR-03)', {
      allow: m => {
        if (m[1]) return true; // variant-prefixed — a state transition, not a base tint
        if (TINT_OPACITY_EXCEPTIONS.has(`${relPath}:${stripped.slice(0, m.index).split('\n').length}`)) return true;
        const n = Number(m[4]);
        return m[2] === 'bg' ? LADDER_FILL.has(n) : LADDER_BORDER.has(n);
      },
    });
}

for (const root of ROOTS) {
  for (const file of walk(root)) {
    scan(file, relative(process.cwd(), file).split('\\').join('/'));
  }
}

if (failures.length > 0) {
  console.error(`design-lint: ${failures.length} violation(s)\n`);
  for (const f of failures) console.error(f);
  process.exit(1);
}
console.log('design-lint: clean');
