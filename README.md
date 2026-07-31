# Golfo Dulce

An open, sourced knowledge base on the **Golfo Dulce**, Costa Rica — one of a small number
of tropical anoxic fjord-like basins in the world: a deep inner basin separated from the
Pacific by a shallow sill, with oxygen-depleted water below roughly 100–200 m.

The site documents the gulf's oceanography, bathymetry, geology, ecology, conservation and
human history — and, just as importantly, maintains an explicit register of **what is not
known**. That gap register is the point. It is what makes the site useful to researchers
rather than merely informative, and it is the design document for any later field survey.

Bilingual: English and Spanish. The Spanish audience here is primary, not secondary.

## How this repository guarantees its claims

The promise of this project is that every factual claim traces to a real source. That is
enforced mechanically, not editorially:

| Gate | What it stops |
|---|---|
| `scripts/validate-bibliography.mjs` | Fabricated citations. Resolves DOIs against Crossref and compares the returned title with the one claimed. A DOI that doesn't exist, or belongs to a different paper, fails. |
| `scripts/validate-citations.mjs` | Dangling references. Any `[@source-id]` in prose that isn't in `bibliography/sources.json` fails the build. |
| `scripts/validate-translation-parity.mjs` | Content lost or invented in translation. English and Spanish must share heading structure and citation sets. |

The bibliography gate has its own regression test, so the gate itself cannot rot:

```bash
npm run test:gate
```

It runs the validator against a fixture containing a real source, a real DOI paired with
the wrong title, and an invented DOI — and fails if the validator does not catch the last two.

## Layout

```
data/            raw downloads (immutable) + derived products; manifest.json records
                 provenance, licence and checksum for every dataset
pipeline/        Python fetch + raster processing; reproducible end to end
bibliography/    harvest output per topic, merged into the canonical sources.json
scripts/         the validation gates
site/            Astro site (MDX content collections, MapLibre depth map)
```

## Getting started

```bash
npm install
npm run dev
```

Data pipeline:

```bash
pip install -r pipeline/requirements.txt
python pipeline/fetch_gmrt.py
```

Full check:

```bash
npm run validate && npm run build
```

## Status

Phase 1 — building the baseline from existing public knowledge. No new survey data yet.
See [`bibliography/HARVEST-CONTRACT.md`](bibliography/HARVEST-CONTRACT.md) for how sources
are gathered and checked.

## Licensing

Site text is CC BY 4.0. Underlying datasets retain their originators' licences — recorded
per dataset in `data/manifest.json` and per source in the bibliography.
