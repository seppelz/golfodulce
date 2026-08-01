"""Build the species occurrence summary the site serves, from OBIS and GBIF raw pulls.

Replaces an earlier version of this file. The species.json this project shipped for a
while was NOT produced by any committed script — it was written directly by a background
agent that kept working after being given up on, then swept into a commit via `git add -A`
without its provenance being checked. Two problems surfaced on review:

1. No script reproduced it, breaking this project's core promise that every derived
   dataset can be regenerated from raw inputs by a script in this repository.
2. Its depth statistics were substantially wrong. GBIF's own "Atta" dataset (Costa Rican
   terrestrial mollusc records) has a field-population bug: `depth` is copied verbatim
   from `elevation` for preserved specimens collected on mountainsides, e.g. a land snail
   collected inland near San Vito at 980 m elevation carries `depth: 980.0` in the same
   record. Checked directly against the raw pages: 5,854 of 7,568 GBIF records carrying a
   depth value (77%) show depth == elevation exactly, all from that one dataset — land
   elevation mislabelled as ocean depth. Left uncorrected, this made the public occurrence
   record look far better sampled at depth than it actually is, which is the opposite
   error this project exists to avoid.

This version excludes any record where depth equals elevation (both non-null) from depth
statistics, since a real marine observation cannot simultaneously have a meaningful
elevation above sea level and a depth below it.

OBIS: fetched complete for the bbox (6,513 records, matching the API's own reported
total). GBIF: fetched pre-scoped to aquatic taxa via the query itself, not a post-hoc
filter — its 2.04M unscoped records for this bbox are overwhelmingly terrestrial
(eBird, INBio insects) and were never downloaded at record level; see
data/raw/gbif/gbif_census.json for that count and pipeline/fetch_occurrence.py history.

Usage:
    python pipeline/build_occurrence.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from common import DERIVED, RAW, Provenance, record, rel, sha256_of, today

OBIS_DIR = RAW / "obis"
GBIF_DIR = RAW / "gbif"
OUT_DIR = DERIVED / "occurrence"
OUT_FILE = OUT_DIR / "species.json"

OBIS_LICENCE = "Per-dataset; OBIS aggregates records under a mix of CC0 and CC-BY."
GBIF_LICENCE_NOTE = "Per-record; see licences.by_record for the distribution across this pull."


def load_pages(directory: Path, pattern: str) -> list[dict]:
    out: list[dict] = []
    for path in sorted(directory.glob(pattern)):
        if path.suffix == ".part":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        out.extend(payload.get("results", []))
    return out


def obis_depth(r: dict) -> float | None:
    """OBIS's `bathymetry` field is a seabed lookup from a global grid at the point, not
    an observation of where the specimen actually was — using it would misrepresent a
    surface sighting as a deep one. Only explicit measured depth fields count."""
    for field in ("depth", "maximumDepthInMeters"):
        v = r.get(field)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def gbif_depth(r: dict) -> float | None:
    """See module docstring: GBIF's Atta dataset copies elevation into depth for
    terrestrial specimens. depth == elevation, both non-null, is that bug's signature —
    a real marine record cannot have a meaningful value in both fields at once."""
    depth = r.get("depth")
    elevation = r.get("elevation")
    if not isinstance(depth, (int, float)):
        return None
    if isinstance(elevation, (int, float)) and depth == elevation:
        return None
    return float(depth)


def dedup_key(r: dict, source: str) -> str:
    """Best-effort cross-registry identity. Prefer a stable catalogue identifier; fall
    back to name + coordinate + date, which is not perfect but catches the common case
    of the same specimen/sighting mobilised through both OBIS and GBIF."""
    occ_id = r.get("occurrenceID") or r.get("id")
    if occ_id:
        return f"occid:{occ_id}"
    inst = r.get("institutionCode") or r.get("institutioncode")
    cat = r.get("catalogNumber") or r.get("catalognumber")
    if inst and cat:
        return f"cat:{inst}:{cat}"
    name = r.get("species") or r.get("scientificName") or "?"
    lat = r.get("decimalLatitude")
    lon = r.get("decimalLongitude")
    date = r.get("eventDate") or r.get("date_year")
    return f"name:{name}:{lat}:{lon}:{date}"


def main() -> int:
    obis_raw = load_pages(OBIS_DIR, "obis_occurrence_p*.json")
    gbif_raw = load_pages(GBIF_DIR, "gbif_occurrence_p*.json")

    stats_path = OBIS_DIR / "obis_statistics.json"
    obis_stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}

    species: dict[str, dict] = defaultdict(
        lambda: {
            "records": 0,
            "records_obis": 0,
            "records_gbif": 0,
            "records_with_depth": 0,
            "depths": [],
            "years": [],
            "datasets": set(),
            "licences": set(),
            "class": None,
            "order": None,
            "family": None,
            "phylum": None,
            "kingdom": None,
            "aphia_id": None,
            "gbif_taxon_key": None,
        }
    )

    seen_keys: set[str] = set()
    duplicates = 0
    licence_counts: dict[str, int] = defaultdict(int)
    record_depths: list[float] = []  # every individual record's depth, for band counts

    def ingest(records: list[dict], source: str, name_field: str, depth_fn):
        nonlocal duplicates
        for r in records:
            name = r.get(name_field) or r.get("scientificName")
            if not name:
                continue
            key = dedup_key(r, source)
            if key in seen_keys:
                duplicates += 1
                continue
            seen_keys.add(key)

            s = species[name]
            s["records"] += 1
            s[f"records_{source}"] += 1
            s["kingdom"] = s["kingdom"] or r.get("kingdom")
            s["phylum"] = s["phylum"] or r.get("phylum")
            s["class"] = s["class"] or r.get("class")
            s["order"] = s["order"] or r.get("order")
            s["family"] = s["family"] or r.get("family")
            s["aphia_id"] = s["aphia_id"] or r.get("aphiaID")
            s["gbif_taxon_key"] = s["gbif_taxon_key"] or r.get("taxonKey")

            year = r.get("date_year") or (
                int(r["eventDate"][:4]) if r.get("eventDate", "")[:4].isdigit() else None
            )
            if isinstance(year, int):
                s["years"].append(year)

            ds = r.get("datasetName") or r.get("dataset_id") or r.get("datasetKey")
            if ds:
                s["datasets"].add(str(ds))
            lic = r.get("license") or r.get("license_url") or "unspecified"
            s["licences"].add(lic)
            licence_counts[lic] += 1

            depth = depth_fn(r)
            if depth is not None:
                s["records_with_depth"] += 1
                s["depths"].append(depth)
                record_depths.append(depth)

    # OBIS first so an OBIS-GBIF duplicate keeps its OBIS-derived (verified) depth field
    # rather than a GBIF depth that may need the elevation-confusion check to fail it.
    ingest(obis_raw, "obis", "species", obis_depth)
    ingest(gbif_raw, "gbif", "species", gbif_depth)

    # GBIF's backbone has no class for bony fish — Actinopterygii orders hang directly
    # off Chordata with class=null. Backfill from any sibling record of the same species
    # that does carry a class, so fish are not silently miscounted as classless.
    backfilled = 0
    for name, s in species.items():
        if s["class"] is None and s["order"]:
            for other_name, other in species.items():
                if other["order"] == s["order"] and other["class"]:
                    s["class"] = other["class"]
                    backfilled += 1
                    break

    rows = []
    for name, s in species.items():
        rows.append(
            {
                "scientific_name": name,
                "kingdom": s["kingdom"],
                "phylum": s["phylum"],
                "class": s["class"],
                "order": s["order"],
                "family": s["family"],
                "aphia_id": s["aphia_id"],
                "gbif_taxon_key": s["gbif_taxon_key"],
                "records": s["records"],
                "records_obis": s["records_obis"],
                "records_gbif": s["records_gbif"],
                "year_min": min(s["years"]) if s["years"] else None,
                "year_max": max(s["years"]) if s["years"] else None,
                "records_with_depth": s["records_with_depth"],
                "depth_min_m": min(s["depths"]) if s["depths"] else None,
                "depth_max_m": max(s["depths"]) if s["depths"] else None,
                "sources": sorted({"obis"} if s["records_obis"] else set() | ({"gbif"} if s["records_gbif"] else set())),
                "datasets": sorted(s["datasets"])[:5],
                "dataset_count": len(s["datasets"]),
                "licences": sorted(s["licences"]),
            }
        )
    rows.sort(key=lambda r: (-r["records"], r["scientific_name"]))

    total_records = sum(r["records"] for r in rows)
    total_with_depth = sum(r["records_with_depth"] for r in rows)
    all_years = [y for r in rows for y in (r["year_min"], r["year_max"]) if y]

    out = {
        "generated": today(),
        "bbox": {"west": -83.60, "east": -83.00, "south": 8.35, "north": 8.80},
        "sources": {
            "obis": {
                "endpoint": "https://api.obis.org/v3/occurrence",
                "records_downloaded": len(obis_raw),
                "scope": "complete: every OBIS record in the bbox",
            },
            "gbif": {
                "endpoint": "https://api.gbif.org/v1/occurrence/search",
                "records_downloaded": len(gbif_raw),
                "scope": "aquatic taxa only; see notes",
            },
        },
        "notes": [
            "GBIF holds ~2.04M records in this bbox, overwhelmingly terrestrial (eBird "
            "land-bird observations, INBio/iBOL insects). Its search API caps "
            "offset+limit at 100,001, so the full set is not retrievable this way "
            "regardless. The GBIF pull is scoped to aquatic taxa at query time; the "
            "unscoped census is kept at data/raw/gbif/gbif_census.json.",
            "GBIF's backbone has no class Actinopterygii: bony-fish orders hang "
            "directly off Chordata with class=null. Class is backfilled from any "
            "sibling record sharing the same order so fish are not silently excluded "
            f"from class-level counts ({backfilled} species backfilled this run).",
            "Depth is observed depth only. OBIS's `bathymetry` field is a seabed "
            "lookup from a global grid, not an observation, and is not used.",
            "GBIF's 'Atta' dataset (Costa Rican terrestrial mollusc records) copies "
            "elevation into the depth field for preserved specimens — depth == "
            "elevation, both non-null, is that bug's signature. Those values are "
            "excluded from depth statistics rather than counted as marine "
            "observations; see gbif_depth() in this script.",
            "Deduplication is on occurrenceID/id where present, falling back to "
            "institutionCode+catalogNumber, then to name+coordinate+date.",
        ],
        "totals": {
            "records_obis_raw": len(obis_raw),
            "records_gbif_raw": len(gbif_raw),
            "records_concatenated": len(obis_raw) + len(gbif_raw),
            "records_deduplicated": total_records,
            "duplicates_removed": duplicates,
            "species_or_taxa": len(rows),
            "records_with_depth": total_with_depth,
            "records_with_depth_pct": round(100 * total_with_depth / total_records, 1) if total_records else 0,
            "depth_min_m": min((r["depth_min_m"] for r in rows if r["depth_min_m"] is not None), default=None),
            "depth_max_m": max((r["depth_max_m"] for r in rows if r["depth_max_m"] is not None), default=None),
            "records_at_or_below_100m": sum(1 for d in record_depths if d >= 100),
            "records_at_or_below_200m": sum(1 for d in record_depths if d >= 200),
            "class_backfilled_from_sibling_records": backfilled,
            "year_min": min(all_years) if all_years else None,
            "year_max": max(all_years) if all_years else None,
        },
        "licences": {"by_record": dict(sorted(licence_counts.items(), key=lambda kv: -kv[1]))},
        "depth_histogram_m": [
            {"band": "0-25", "records": sum(1 for d in record_depths if 0 <= d < 25)},
            {"band": "25-50", "records": sum(1 for d in record_depths if 25 <= d < 50)},
            {"band": "50-100", "records": sum(1 for d in record_depths if 50 <= d < 100)},
            {"band": "100-150", "records": sum(1 for d in record_depths if 100 <= d < 150)},
            {"band": "150-200", "records": sum(1 for d in record_depths if 150 <= d < 200)},
            {"band": "200+", "records": sum(1 for d in record_depths if d >= 200)},
        ],
        "top_classes": [
            {"class": cls, "species": n}
            for cls, n in sorted(
                defaultdict(
                    int,
                    {c: sum(1 for r in rows if r["class"] == c) for c in {r["class"] for r in rows if r["class"]}},
                ).items(),
                key=lambda kv: -kv[1],
            )[:10]
        ],
        "species": rows,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    record(
        Provenance(
            dataset="occurrence-species",
            path=rel(OUT_FILE),
            source_url="derived from obis-occurrence_p*, gbif-occurrence_p*",
            accessed=today(),
            licence="Per-record; see licences.by_record inside the file itself.",
            licence_url="",
            sha256=sha256_of(OUT_FILE),
            bytes=OUT_FILE.stat().st_size,
            notes=(
                "Regenerated after finding the previously-committed version was not "
                "reproducible by any script in this repo and had a GBIF depth/elevation "
                "confusion bug inflating 'records with depth' from ~12% to ~46%. See "
                "module docstring."
            ),
            properties={
                "species": out["totals"]["species_or_taxa"],
                "records_deduplicated": out["totals"]["records_deduplicated"],
                "records_with_depth": out["totals"]["records_with_depth"],
            },
        )
    )

    t = out["totals"]
    print("Golfo Dulce — species occurrence (corrected depth handling)\n")
    print(f"  OBIS raw: {t['records_obis_raw']:,}   GBIF raw: {t['records_gbif_raw']:,}")
    print(f"  deduplicated: {t['records_deduplicated']:,}  ({t['duplicates_removed']:,} duplicates removed)")
    print(f"  species/taxa: {t['species_or_taxa']:,}")
    print(f"  with depth: {t['records_with_depth']:,} ({t['records_with_depth_pct']}%)  "
          f"range {t['depth_min_m']} to {t['depth_max_m']} m")
    print(f"  years {t['year_min']}-{t['year_max']}")
    print(f"\nWrote {rel(OUT_FILE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
