import { z } from 'zod';

/**
 * The canonical shape of a bibliography entry.
 *
 * This is the contract harvest agents must satisfy. It is deliberately strict:
 * the whole value of this project rests on every claim tracing to a real,
 * reachable source, so the schema refuses entries that cannot be checked.
 */

export const TOPICS = [
  'oceanography',
  'bathymetry',
  'geology',
  'ecology',
  'conservation',
  'human-history',
];

/** Minimum entries a harvest agent must return per topic before its output is accepted. */
export const MIN_ENTRIES_PER_TOPIC = 12;

/** Stable IDs look like `dalsgaard-2003-anammox`: lastname-year-keyword. */
const idPattern = /^[a-z0-9]+(-[a-z0-9]+)*-\d{4}[a-z]?-[a-z0-9]+(-[a-z0-9]+)*$/;

export const SourceSchema = z
  .object({
    id: z
      .string()
      .regex(idPattern, 'id must be lastname-year-keyword, all lowercase (e.g. dalsgaard-2003-anammox)'),

    type: z.enum([
      'journal-article',
      'book',
      'book-chapter',
      'thesis',
      'report',
      'dataset',
      'chart',
      'web-page',
    ]).describe('publication type'),

    title: z.string().min(8),
    authors: z.array(z.string().min(2)).min(1, 'at least one author required'),
    year: z.number().int().min(1500).max(2027).nullable(),
    container: z.string().nullable().describe('journal, book, or publisher'),

    doi: z
      .string()
      .regex(/^10\.\d{4,9}\/\S+$/, 'must be a bare DOI, no https:// prefix')
      .nullable(),
    url: z.string().url().nullable(),

    language: z.enum(['en', 'es', 'de', 'other']),
    access: z.enum(['open', 'paywalled', 'request-required', 'unknown']),

    topics: z.array(z.enum(TOPICS)).min(1),

    /** One paragraph on what this source actually contributes to our understanding. */
    summary: z.string().min(120, 'summary must be a substantive paragraph, not a sentence'),

    /**
     * A short verbatim line from the abstract or text supporting the summary.
     * This is the anti-fabrication hook: a quote that cannot be found in the
     * real source is detectable, whereas a paraphrase is not.
     */
    verbatim_finding: z.string().min(20).max(400),

    /** What data, if any, the source makes available, and in what form. */
    data_availability: z.string().min(10),

    /** Provenance of our own retrieval. */
    retrieved: z.object({
      url_checked: z.string().url(),
      http_status: z.number().int(),
      date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, 'ISO date, YYYY-MM-DD'),
    }),

    /** Optional free-text caveat: contested findings, small sample, superseded, etc. */
    caveat: z.string().nullable().default(null),
  })
  .strict()
  .refine((s) => s.doi !== null || s.url !== null, {
    message: 'entry must have at least one of doi or url — an unreachable source is not admissible',
  });

export const HarvestFileSchema = z.array(SourceSchema);
