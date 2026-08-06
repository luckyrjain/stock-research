#!/usr/bin/env node
// Design-system CI gate (ENF-01 / ENF-02, design.md §19) — "a rule nothing
// checks is a preference." Catches the exact class of bug that let the
// primary CTA glow in a retired brand color for months (a raw hex nothing
// else could see) and a handful of other rules a TS compiler can't catch.
//
// Deliberately narrow: only checks that are (a) explicitly named in §19 and
// (b) hold cleanly against the actual shipped app today, verified by running
// this exact script against the repo before wiring it into CI. COLOR-03
// (off-ladder tint opacity) is NOT enforced here — a real pre-existing audit
// found 100+ call sites using opacities outside the ladder's literal
// enumerated list (e.g. bg-hold/15, bg-buy/50), spread across ~30 files.
// That's either a stale/too-narrow list in design.md or a large, purely
// cosmetic, visually-unverifiable rewrite — not something to guess at or
// silently mass-edit. Left as a flagged gap, not a false-green check.

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
