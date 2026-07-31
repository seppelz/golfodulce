# Harvest contract

Every literature-harvest agent works to this contract. It exists so that six agents
running in parallel produce one coherent bibliography rather than six incompatible ones,
and so that their output can be checked mechanically rather than taken on trust.

## Output

Write exactly one file: `bibliography/harvest/<topic>.json`, a JSON array of source
entries. `<topic>` is one of:

`oceanography` · `bathymetry` · `geology` · `ecology` · `conservation` · `human-history`

The authoritative schema is [`scripts/schema.mjs`](../scripts/schema.mjs). It is enforced;
read it before writing anything.

## Entry fields

| Field | Notes |
|---|---|
| `id` | `lastname-year-keyword`, lowercase, e.g. `dalsgaard-2003-anammox`. Must be unique. |
| `type` | `journal-article`, `book`, `book-chapter`, `thesis`, `report`, `dataset`, `chart`, `web-page` |
| `title` | Exactly as published. Do not translate it. |
| `authors` | Array, as printed. |
| `year` | Integer or `null` if genuinely undated. |
| `container` | Journal, book, or publisher. `null` if none. |
| `doi` | **Bare** DOI (`10.1038/nature01526`), never a URL. `null` if none exists. |
| `url` | Stable link. `null` if none. At least one of `doi`/`url` is required. |
| `language` | `en`, `es`, `de`, `other` |
| `access` | `open`, `paywalled`, `request-required`, `unknown` |
| `topics` | Array; **must include your own topic**. Add others if genuinely relevant. |
| `summary` | A substantive paragraph (≥120 chars) on what this source *contributes* — not what it is about. |
| `verbatim_finding` | A short **exact quotation** (20–400 chars) from the abstract or text. |
| `data_availability` | What data the source makes available, and how. Say "none" plainly if none. |
| `retrieved` | `{ url_checked, http_status, date }` — the page you actually opened. |
| `caveat` | Contested, superseded, tiny sample, etc. `null` if nothing to flag. |

## Rules

1. **Only record sources you actually retrieved.** Fetch the DOI or URL and record the
   real HTTP status. Do not enter a source you have only seen cited by something else.
2. **`verbatim_finding` must be a real quotation** you can point to in the source. It is
   short deliberately — a fragment, not a paragraph. Never paraphrase into this field.
3. **No invented DOIs.** A plausible-looking DOI that does not resolve is the single worst
   failure mode here, and it is checked directly against Crossref.
4. **Search Spanish as well as English.** A harvest with zero Spanish sources is rejected
   automatically. Useful terms: `batimetría`, `zona de mínimo oxígeno`, `anóxico`,
   `Golfo Dulce`, `Península de Osa`, `manglar`, `pesquería`, `Corcovado`.
5. **Minimum 12 entries per topic.** Quality over volume beyond that — do not pad.
6. **Flag paywalls, don't fight them.** Set `access: "paywalled"` and move on. Sebastian
   decides what is worth requesting.
7. **Prefer primary sources.** A 2003 *Nature* paper beats a 2019 blog post summarising it.

## Where to look

- **Crossref API** — `https://api.crossref.org/works?query=...` (fast, scriptable, gives clean DOIs)
- **CIMAR / UCR** — Centro de Investigación en Ciencias del Mar y Limnología; likely the richest single source
- **Revista de Biología Tropical** — `revistas.ucr.ac.cr/index.php/rbt`, much of it open access
- **SciELO** — `search.scielo.org`, strong for Costa Rican and Latin American work
- **PANGAEA** — `pangaea.de`, datasets with DOIs
- **OBIS / GBIF** — species occurrence, for the ecology topic
- **NOAA NCEI** — bathymetry and oceanographic archives
- **Google Scholar / ResearchGate** — for citation trails; always resolve to a primary link
- **Theses** — often unindexed and data-rich; UCR and UNA repositories

Find the few foundational papers first, then follow who cites them. `10.1038/nature01526`
(Dalsgaard et al. 2003, anammox in the Golfo Dulce anoxic water column) is a known anchor
for the oceanography trail.

## How your work is checked

`node scripts/validate-bibliography.mjs <topic> --sample=1.0`

This validates the schema, rejects duplicate ids and DOIs, requires at least one Spanish
source, requires ≥12 entries, and — the part that matters — **resolves your DOIs against
Crossref and compares the returned title with the one you claimed.** A DOI that does not
exist, or that belongs to a different paper, fails the harvest.

Run it yourself before reporting done. Report the exact command output.
