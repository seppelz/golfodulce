#!/usr/bin/env node
/**
 * Merges bibliography/harvest/<topic>.json into the single canonical
 * bibliography/sources.json that the site and the citation gate read.
 *
 * Deduplication is by id first (harvest agents work independently and may pick the
 * same id for the same paper) and then by DOI (the more reliable signal — two agents
 * describing the same source under different ids would otherwise both survive).
 * When two entries collide, the one with the longer summary wins, on the theory that
 * more substantive description reflects a closer reading of the source.
 *
 * This does not re-run the Crossref check — validate-bibliography.mjs already gates
 * each harvest file before it reaches here. This script only merges.
 */

import { readFile, writeFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { HarvestFileSchema } from './schema.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const HARVEST_DIR = path.join(ROOT, 'bibliography', 'harvest');
const OUT_FILE = path.join(ROOT, 'bibliography', 'sources.json');

function better(a, b) {
  return a.summary.length >= b.summary.length ? a : b;
}

async function main() {
  const files = (await readdir(HARVEST_DIR)).filter((f) => f.endsWith('.json') && !f.startsWith('_'));
  if (files.length === 0) {
    console.log('No harvest files to merge.');
    process.exit(0);
  }

  const byId = new Map();
  const idByDoi = new Map();
  let rejected = 0;

  for (const file of files) {
    const topic = path.basename(file, '.json');
    const raw = JSON.parse(await readFile(path.join(HARVEST_DIR, file), 'utf8'));
    const parsed = HarvestFileSchema.safeParse(raw);
    if (!parsed.success) {
      console.error(`skipping ${file}: fails schema (${parsed.error.issues.length} issue(s)) — run the validator first`);
      continue;
    }

    for (const entry of parsed.data) {
      const doiKey = entry.doi?.toLowerCase() ?? null;

      // Same DOI already merged under a different id: keep the richer entry, but
      // union the topic lists so the source stays findable under both.
      if (doiKey && idByDoi.has(doiKey)) {
        const existingId = idByDoi.get(doiKey);
        const existing = byId.get(existingId);
        const merged = better(existing, entry);
        merged.topics = [...new Set([...existing.topics, ...entry.topics])];
        byId.set(existingId, merged);
        rejected++;
        continue;
      }

      if (byId.has(entry.id)) {
        const existing = byId.get(entry.id);
        const merged = better(existing, entry);
        merged.topics = [...new Set([...existing.topics, ...entry.topics])];
        byId.set(entry.id, merged);
        rejected++;
      } else {
        byId.set(entry.id, { ...entry });
      }
      if (doiKey) idByDoi.set(doiKey, entry.id);
    }

    console.log(`  ${topic}: ${parsed.data.length} entries`);
  }

  const merged = [...byId.values()].sort((a, b) => a.id.localeCompare(b.id));
  await writeFile(OUT_FILE, JSON.stringify(merged, null, 2) + '\n', 'utf8');

  console.log(`\nMerged ${merged.length} unique sources (${rejected} duplicate reference(s) folded in)`);
  console.log(`Wrote ${path.relative(ROOT, OUT_FILE)}`);
}

main();
