#!/usr/bin/env node
/**
 * Gate for Wave A harvest output.
 *
 * An agent's report that it found N good sources is not evidence. This script is
 * the evidence. It checks structure, then goes to the network and confirms that a
 * sample of the DOIs actually resolve to the titles claimed. A hallucinated
 * citation survives a schema check; it does not survive Crossref.
 *
 * Usage:
 *   node scripts/validate-bibliography.mjs               # validate all harvest files
 *   node scripts/validate-bibliography.mjs oceanography  # one topic
 *   node scripts/validate-bibliography.mjs --sample=1.0  # verify every DOI, not a sample
 */

import { readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { HarvestFileSchema, MIN_ENTRIES_PER_TOPIC, TOPICS } from './schema.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const args = process.argv.slice(2);
const sampleArg = args.find((a) => a.startsWith('--sample='));
const SAMPLE_RATE = sampleArg ? Number(sampleArg.split('=')[1]) : 0.25;
const dirArg = args.find((a) => a.startsWith('--dir='));
const HARVEST_DIR = dirArg
  ? path.resolve(ROOT, dirArg.split('=')[1])
  : path.join(ROOT, 'bibliography', 'harvest');
/** In self-test mode the fixture is *expected* to fail; we assert that it does. */
const EXPECT_FAIL = args.includes('--expect-fail');
const onlyTopics = args.filter((a) => !a.startsWith('--'));

const MAILTO = 'sebastian.soecker@gmail.com'; // Crossref asks for a contact in the UA
const UA = `golfodulce-bibliography-validator/1.0 (mailto:${MAILTO})`;

let errors = 0;
let warnings = 0;
/** Which specific detections fired — used by --expect-fail to assert the gate really bit. */
const detected = { ghostDoi: false, titleMismatch: false };
const err = (m) => { console.error(`  \x1b[31mFAIL\x1b[0m ${m}`); errors++; };
const warn = (m) => { console.warn(`  \x1b[33mWARN\x1b[0m ${m}`); warnings++; };
const ok = (m) => console.log(`  \x1b[32mok\x1b[0m   ${m}`);

/** Loose title comparison: punctuation, case, and whitespace differ harmlessly between sources. */
const normalise = (s) =>
  s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();

function titlesAgree(claimed, actual) {
  const a = normalise(claimed);
  const b = normalise(actual);
  if (a === b) return true;
  if (a.startsWith(b) || b.startsWith(a)) return true;
  // Token overlap catches subtitle and translation differences without being blind.
  const ta = new Set(a.split(' ').filter((w) => w.length > 3));
  const tb = new Set(b.split(' ').filter((w) => w.length > 3));
  if (ta.size === 0 || tb.size === 0) return false;
  const shared = [...ta].filter((w) => tb.has(w)).length;
  return shared / Math.min(ta.size, tb.size) >= 0.6;
}

async function resolveDoi(doi) {
  const res = await fetch(`https://api.crossref.org/works/${encodeURIComponent(doi)}`, {
    headers: { 'User-Agent': UA },
    signal: AbortSignal.timeout(20000),
  });
  if (res.status === 404) return { found: false };
  if (!res.ok) return { found: null, status: res.status }; // inconclusive, not a failure
  const body = await res.json();
  return { found: true, title: body?.message?.title?.[0] ?? '' };
}

async function urlReachable(url) {
  try {
    let res = await fetch(url, { method: 'HEAD', redirect: 'follow', headers: { 'User-Agent': UA }, signal: AbortSignal.timeout(20000) });
    // Many academic hosts reject HEAD but serve GET.
    if (res.status === 405 || res.status === 403) {
      res = await fetch(url, { method: 'GET', redirect: 'follow', headers: { 'User-Agent': UA }, signal: AbortSignal.timeout(20000) });
    }
    return res.status;
  } catch {
    return null;
  }
}

async function validateTopic(topic, entries) {
  console.log(`\n\x1b[1m${topic}\x1b[0m — ${entries.length} entries`);

  const parsed = HarvestFileSchema.safeParse(entries);
  if (!parsed.success) {
    for (const issue of parsed.error.issues.slice(0, 25)) {
      err(`[${issue.path.join('.')}] ${issue.message}`);
    }
    if (parsed.error.issues.length > 25) err(`…and ${parsed.error.issues.length - 25} more schema issues`);
    return;
  }
  ok('schema conforms');

  if (entries.length < MIN_ENTRIES_PER_TOPIC) {
    err(`only ${entries.length} entries, need at least ${MIN_ENTRIES_PER_TOPIC}`);
  }

  // Every entry must actually claim the topic it was filed under.
  const misfiled = entries.filter((e) => !e.topics.includes(topic));
  if (misfiled.length) err(`${misfiled.length} entries do not list "${topic}" in topics: ${misfiled.slice(0, 3).map((e) => e.id).join(', ')}`);

  // Duplicate IDs and DOIs within the file.
  const seenId = new Set();
  const seenDoi = new Set();
  for (const e of entries) {
    if (seenId.has(e.id)) err(`duplicate id: ${e.id}`);
    seenId.add(e.id);
    if (e.doi) {
      const d = e.doi.toLowerCase();
      if (seenDoi.has(d)) err(`duplicate DOI ${e.doi} (${e.id})`);
      seenDoi.add(d);
    }
  }

  // A harvest that is entirely English has not done the Spanish half of the job.
  const spanish = entries.filter((e) => e.language === 'es').length;
  if (spanish === 0) {
    err('no Spanish-language sources — the Spanish literature was not searched');
  } else {
    ok(`${spanish}/${entries.length} Spanish-language sources`);
  }

  // Network verification.
  const withDoi = entries.filter((e) => e.doi);
  const n = Math.max(1, Math.ceil(withDoi.length * SAMPLE_RATE));
  const sample = [...withDoi].sort(() => Math.random() - 0.5).slice(0, n);
  console.log(`  verifying ${sample.length}/${withDoi.length} DOIs against Crossref…`);

  for (const e of sample) {
    try {
      const r = await resolveDoi(e.doi);
      if (r.found === false) {
        detected.ghostDoi = true;
        err(`DOI does not exist: ${e.doi} (${e.id}) — likely fabricated`);
      } else if (r.found === null) {
        warn(`Crossref inconclusive (HTTP ${r.status}) for ${e.doi} (${e.id})`);
      } else if (!titlesAgree(e.title, r.title)) {
        detected.titleMismatch = true;
        err(`DOI/title mismatch for ${e.id}\n         claimed: ${e.title}\n         actual:  ${r.title}`);
      }
    } catch (ex) {
      warn(`could not check ${e.doi} (${e.id}): ${ex.message}`);
    }
  }

  // Entries with no DOI lean entirely on their URL, so check those.
  const urlOnly = entries.filter((e) => !e.doi && e.url);
  for (const e of urlOnly.slice(0, 10)) {
    const status = await urlReachable(e.url);
    if (status === null) warn(`URL unreachable (may be transient): ${e.url} (${e.id})`);
    else if (status >= 400) err(`URL returns HTTP ${status}: ${e.url} (${e.id})`);
  }
  if (withDoi.length || urlOnly.length) ok('network verification complete');
}

async function main() {
  if (!existsSync(HARVEST_DIR)) {
    console.error(`No harvest directory at ${HARVEST_DIR} — nothing to validate yet.`);
    process.exit(0);
  }
  const files = (await readdir(HARVEST_DIR)).filter((f) => f.endsWith('.json'));
  if (files.length === 0) {
    console.log('No harvest files yet — nothing to validate.');
    process.exit(0);
  }

  const missing = TOPICS.filter((t) => !files.includes(`${t}.json`));

  for (const file of files) {
    const topic = path.basename(file, '.json');
    if (onlyTopics.length && !onlyTopics.includes(topic)) continue;
    let entries;
    try {
      entries = JSON.parse(await readFile(path.join(HARVEST_DIR, file), 'utf8'));
    } catch (e) {
      console.log(`\n\x1b[1m${topic}\x1b[0m`);
      err(`not valid JSON: ${e.message}`);
      continue;
    }
    if (!Array.isArray(entries)) {
      console.log(`\n\x1b[1m${topic}\x1b[0m`);
      err('top level must be an array of source entries');
      continue;
    }
    await validateTopic(topic, entries);
  }

  if (missing.length && !onlyTopics.length) {
    console.log(`\n\x1b[33mTopics not yet harvested:\x1b[0m ${missing.join(', ')}`);
  }

  if (EXPECT_FAIL) {
    // Self-test: the fixture is designed to be caught. Passing it would mean the gate is broken.
    const missed = [];
    if (!detected.ghostDoi) missed.push('did not catch the non-existent DOI');
    if (!detected.titleMismatch) missed.push('did not catch the DOI/title mismatch');
    if (missed.length) {
      console.error(`\n\x1b[31mGATE IS BROKEN\x1b[0m — ${missed.join('; ')}`);
      process.exit(1);
    }
    console.log('\n\x1b[32mGATE OK\x1b[0m — fabricated DOI and title mismatch were both caught as designed');
    process.exit(0);
  }

  console.log(`\n${errors === 0 ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAILED\x1b[0m'} — ${errors} error(s), ${warnings} warning(s)`);
  process.exit(errors === 0 ? 0 : 1);
}

main();
