#!/usr/bin/env node
/**
 * The translation gate.
 *
 * Translation is where content quietly goes missing: a section gets dropped, a caveat
 * gets smoothed away, or a claim appears in Spanish that has no English original and
 * therefore never passed the citation gate. Comparing heading structure and citation
 * sets catches all three without needing to judge the Spanish prose itself.
 */

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { CONTENT_DIR, listMdx, stripNonProse, extractCiteIds, extractHeadings } from './lib-content.mjs';

let errors = 0;
let warnings = 0;
const err = (m) => { console.error(`  \x1b[31mFAIL\x1b[0m ${m}`); errors++; };
const warn = (m) => { console.warn(`  \x1b[33mWARN\x1b[0m ${m}`); warnings++; };

async function read(locale, rel) {
  const body = stripNonProse(await readFile(path.join(CONTENT_DIR, locale, rel), 'utf8'));
  return { headings: extractHeadings(body), cites: extractCiteIds(body), body };
}

async function main() {
  const en = await listMdx(path.join(CONTENT_DIR, 'en'));
  const es = await listMdx(path.join(CONTENT_DIR, 'es'));

  if (en.length === 0 && es.length === 0) {
    console.log('No MDX content yet — parity gate idle until Waves D/E.');
    process.exit(0);
  }

  const enSet = new Set(en);
  const esSet = new Set(es);

  // Spanish pages with no English original never passed the citation gate.
  for (const rel of es) if (!enSet.has(rel)) err(`es/${rel} has no English counterpart`);
  // Missing translations are expected mid-build, so they warn rather than fail.
  for (const rel of en) if (!esSet.has(rel)) warn(`en/${rel} not yet translated`);

  const shared = en.filter((rel) => esSet.has(rel));
  console.log(`Comparing ${shared.length} translated page(s)\n`);

  for (const rel of shared) {
    const a = await read('en', rel);
    const b = await read('es', rel);
    const problems = [];

    if (a.headings.length !== b.headings.length) {
      problems.push(`heading count differs (en ${a.headings.length}, es ${b.headings.length})`);
    } else {
      // Depths must match position by position; heading *text* is expected to differ.
      const depthsA = a.headings.map((h) => h.split(':')[0]).join(',');
      const depthsB = b.headings.map((h) => h.split(':')[0]).join(',');
      if (depthsA !== depthsB) problems.push(`heading nesting differs (en ${depthsA} vs es ${depthsB})`);
    }

    const setA = new Set(a.cites);
    const setB = new Set(b.cites);
    const missing = [...setA].filter((c) => !setB.has(c));
    const extra = [...setB].filter((c) => !setA.has(c));
    if (missing.length) problems.push(`citations dropped in es: ${missing.join(', ')}`);
    if (extra.length) problems.push(`citations in es with no English original: ${extra.join(', ')}`);

    // A Spanish page far shorter than its source usually means content was summarised away.
    const ratio = b.body.length / Math.max(1, a.body.length);
    if (ratio < 0.6) problems.push(`Spanish text is ${Math.round(ratio * 100)}% the length of English — likely abridged`);

    if (problems.length) {
      console.log(`\x1b[1m${rel}\x1b[0m`);
      problems.forEach(err);
    }
  }

  console.log(`\n${errors === 0 ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAILED\x1b[0m'} — ${errors} error(s), ${warnings} warning(s)`);
  process.exit(errors === 0 ? 0 : 1);
}

main();
