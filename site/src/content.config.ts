import { defineCollection, z } from 'astro:content';
import { glob, file } from 'astro/loaders';

/**
 * Typed content collections.
 *
 * These schemas are the second half of the citation guarantee: the bibliography is
 * validated on the way in by scripts/validate-bibliography.mjs, and validated again
 * here at build time. A malformed source entry or gap record fails `astro build`.
 */

const TOPICS = [
  'oceanography',
  'bathymetry',
  'geology',
  'ecology',
  'conservation',
  'human-history',
] as const;

/** Section prose, one entry per language. Files live in src/content/{en,es}/**.mdx */
const pages = defineCollection({
  loader: glob({ pattern: '**/*.mdx', base: './src/content/en' }),
  schema: z.object({
    title: z.string(),
    /** Shown under the page title — one sentence on what this page covers. */
    summary: z.string(),
    section: z.enum([
      'overview',
      'oceanography',
      'bathymetry',
      'geology',
      'life',
      'people',
      'conservation',
      'gaps',
      'about',
    ]),
    order: z.number().int().default(0),
    /** Set false while a page is still being drafted; excluded from nav and sitemap. */
    published: z.boolean().default(false),
    updated: z.coerce.date().optional(),
  }),
});

const pagesEs = defineCollection({
  loader: glob({ pattern: '**/*.mdx', base: './src/content/es' }),
  schema: pages.schema,
});

/**
 * The canonical bibliography, loaded straight from the file the validators police.
 * There is deliberately no second copy inside the site.
 */
const sources = defineCollection({
  loader: file('../bibliography/sources.json'),
  schema: z.object({
    id: z.string(),
    type: z.string(),
    title: z.string(),
    authors: z.array(z.string()),
    year: z.number().nullable(),
    container: z.string().nullable(),
    doi: z.string().nullable(),
    url: z.string().nullable(),
    language: z.enum(['en', 'es', 'de', 'other']),
    access: z.enum(['open', 'paywalled', 'request-required', 'unknown']),
    topics: z.array(z.enum(TOPICS)),
    summary: z.string(),
    verbatim_finding: z.string(),
    data_availability: z.string(),
    retrieved: z.object({
      url_checked: z.string(),
      http_status: z.number(),
      date: z.string(),
    }),
    caveat: z.string().nullable().default(null),
  }),
});

/**
 * The gap register — structured, not prose, so it can be filtered, sorted and kept
 * current as knowledge changes. This is the page the whole project exists to produce.
 */
const gaps = defineCollection({
  loader: glob({ pattern: '**/*.yaml', base: './src/content/gaps' }),
  schema: z.object({
    question: z.string().min(20),
    topics: z.array(z.enum(TOPICS)).min(1),
    /** What is currently known, and on whose authority. */
    known: z.string().min(60),
    known_sources: z.array(z.string()).default([]),
    /** Why the gap exists: no survey, data not public, resolution too coarse, etc. */
    why_gap: z.enum([
      'no-survey-conducted',
      'data-not-public',
      'resolution-too-coarse',
      'data-too-old',
      'conflicting-results',
      'not-synthesised',
    ]),
    why_gap_detail: z.string().min(40),
    /** What closing it would take. */
    to_close: z.object({
      approach: z.string().min(30),
      equipment: z.array(z.string()).default([]),
      cost_band: z.enum(['<1k', '1k-10k', '10k-100k', '>100k']),
      effort: z.enum(['days', 'weeks', 'months', 'years']),
    }),
    why_matters: z.string().min(60),
    /** How confident we are that this really is an open question. */
    confidence: z.enum(['high', 'medium', 'low']).default('medium'),
    priority: z.enum(['high', 'medium', 'low']).default('medium'),
    es: z
      .object({
        question: z.string(),
        known: z.string(),
        why_gap_detail: z.string(),
        why_matters: z.string(),
        approach: z.string(),
      })
      .optional(),
  }),
});

export const collections = { pages, pagesEs, sources, gaps };
