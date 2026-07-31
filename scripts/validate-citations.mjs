#!/usr/bin/env node
/**
 * The citation gate.
 *
 * "Every claim traces to a source" is an editorial rule, and editorial rules decay.
 * This turns it into a build failure: any [@id] in any MDX file that does not resolve
 * to an entry in bibliography/sources.json stops the build. A content agent therefore
 * cannot invent a citation and have it reach the site.
 *
 * It also reports uncited factual paragraphs as warnings — not fatal, because prose
 * legitimately includes framing and transitions, but visible so thin sections show up.
 */

import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import {
  CONTENT_DIR, LOCALES, SOURCES_FILE,
  listMdx, stripNonProse, extractCiteIds, loadSources,
} from './lib-content.mjs';

let errors = 0;
let warnings = 0;
const err = (m) => { console.error(`  \x1b[31mFAIL\x1b[0m ${m}`); errors++; };
const warn = (m) => { console.warn(`  \x1b[33mWARN\x1b[0m ${m}`); warnings++; };

/**
 * A paragraph asserting fact rather than framing. Heuristic, deliberately conservative:
 * long prose containing a number, a unit, or a hedge-worthy claim verb.
 */
const FACTUAL = /\b(\d|metres?|meters?|km|m\b|depth|oxygen|salinity|species|per cent|percent|%|recorded|measured|estimated|reported|observed|found)\b/i;

async function main() {
  const sources = await loadSources();
  if (!sources) {
    console.log(`No ${path.relative(process.cwd(), SOURCES_FILE)} yet — citation gate idle until Wave A completes.`);
    process.exit(0);
  }

  const byId = new Map(sources.map((s) => [s.id, s]));
  console.log(`Bibliography: ${byId.size} sources\n`);

  const usedIds = new Set();
  let totalCites = 0;
  let anyContent = false;

  for (const locale of LOCALES) {
    const dir = path.join(CONTENT_DIR, locale);
    const files = await listMdx(dir);
    if (files.length === 0) continue;
    anyContent = true;
    console.log(`\x1b[1m${locale}\x1b[0m — ${files.length} file(s)`);

    for (const rel of files) {
      const raw = await readFile(path.join(dir, rel), 'utf8');
      const body = stripNonProse(raw);
      const ids = extractCiteIds(body);
      totalCites += ids.length;

      const dangling = [...new Set(ids)].filter((id) => !byId.has(id));
      for (const id of dangling) {
        err(`${locale}/${rel}: [@${id}] does not exist in sources.json`);
      }
      ids.forEach((id) => usedIds.add(id));

      // Uncited factual paragraphs.
      const paragraphs = body.split(/\n\s*\n/)
        .map((p) => p.trim())
        .filter((p) => p.length > 180 && !p.startsWith('#') && !p.startsWith('<') && !p.startsWith('|'));
      const uncited = paragraphs.filter((p) => !p.includes('[@') && FACTUAL.test(p));
      if (uncited.length) {
        warn(`${locale}/${rel}: ${uncited.length} factual paragraph(s) carry no citation`);
        for (const p of uncited.slice(0, 2)) {
          warn(`    "${p.replace(/\s+/g, ' ').slice(0, 110)}…"`);
        }
      }

      if (ids.length === 0 && paragraphs.length > 0) {
        warn(`${locale}/${rel}: no citations at all in a file with ${paragraphs.length} substantive paragraph(s)`);
      }
    }
  }

  if (!anyContent) {
    console.log('No MDX content yet — citation gate idle until Wave D.');
    process.exit(0);
  }

  // Sources nobody cites are not an error, but they signal harvest/content drift.
  const unused = [...byId.keys()].filter((id) => !usedIds.has(id));
  console.log(`\n${totalCites} citation(s) used, ${usedIds.size}/${byId.size} sources referenced`);
  if (unused.length) {
    console.log(`\x1b[33m${unused.length} source(s) harvested but never cited\x1b[0m${unused.length <= 12 ? ': ' + unused.join(', ') : ''}`);
  }

  console.log(`\n${errors === 0 ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAILED\x1b[0m'} — ${errors} error(s), ${warnings} warning(s)`);
  process.exit(errors === 0 ? 0 : 1);
}

main();
