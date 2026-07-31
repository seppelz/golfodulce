/** Shared helpers for walking site content. Used by the citation and parity validators. */

import { readdir, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const CONTENT_DIR = path.join(ROOT, 'site', 'src', 'content');
export const SOURCES_FILE = path.join(ROOT, 'bibliography', 'sources.json');
export const LOCALES = ['en', 'es'];

/** Recursively list .mdx files under a directory, returned as paths relative to it. */
export async function listMdx(dir) {
  if (!existsSync(dir)) return [];
  const out = [];
  async function walk(current, prefix) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const abs = path.join(current, entry.name);
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) await walk(abs, rel);
      else if (entry.name.endsWith('.mdx')) out.push(rel);
    }
  }
  await walk(dir, '');
  return out.sort();
}

/** Strip frontmatter, fenced code, and inline code so we never parse citations out of examples. */
export function stripNonProse(raw) {
  return raw
    .replace(/^---\n[\s\S]*?\n---\n/, '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`\n]*`/g, '');
}

/** Citation syntax is [@source-id]; multiple ids may be separated by semicolons. */
export function extractCiteIds(body) {
  const ids = [];
  for (const m of body.matchAll(/\[@([^\]]+)\]/g)) {
    for (const part of m[1].split(';')) {
      const id = part.trim();
      if (id) ids.push(id);
    }
  }
  return ids;
}

/** Markdown ATX headings, as `depth:text` strings, for structural comparison across locales. */
export function extractHeadings(body) {
  return [...body.matchAll(/^(#{1,6})\s+(.+?)\s*$/gm)].map(
    (m) => `${m[1].length}:${m[2].replace(/\[@[^\]]+\]/g, '').trim()}`
  );
}

export async function loadSources() {
  if (!existsSync(SOURCES_FILE)) return null;
  return JSON.parse(await readFile(SOURCES_FILE, 'utf8'));
}
