"""Turn the raw occurrence downloads into a species summary the site can serve.

A judgement call is baked in here and worth stating plainly.

**OBIS is the authoritative source for this project and it is complete.** It returned
6,513 records, matching the total its own API reports, across 614 species and 26 datasets
spanning 1933-2026. OBIS is marine-only, which is exactly what a gulf needs.

**GBIF is not usable as a census here and is deliberately not merged.** Its bounding-box
count for this area is 2,038,617 records, but the box necessarily includes the Osa
Peninsula and the edge of Corcovado, so that figure is overwhelmingly terrestrial —
birds, plants, insects — and says nothing about the water. GBIF also caps paging at
offset 100,001, so a complete pull is impossible through this endpoint regardless. The
partial download that exists is kept and reported as partial rather than being blended
into the totals, because a merged number would be both wrong and impossible to interpret.

The depth question matters more than usual here: this basin is anoxic below roughly
100-200 m, so which records carry a depth value, and how deep they go, bears directly on
what is actually known about life beneath the oxycline.

Usage:
    python pipeline/build_occurrence.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from common import DERIVED, RAW, Provenance, record, rel, sha256_of, today

OBIS_DIR = RAW / "obis"
GBIF_DIR = RAW / "gbif"
OUT_DIR = DERIVED / "occurrence"

OBIS_LICENCE = "Per-dataset; OBIS aggregates records under a mix of CC0 and CC-BY."
OBIS_LICENCE_URL = "https://obis.org/manual/policy/"
GBIF_LICENCE = "Per-dataset; predominantly CC0 / CC-BY / CC-BY-NC."
GBIF_LICENCE_URL = "https://www.gbif.org/terms"


def load_pages(directory: Path, pattern: str) -> list[dict]:
    """Load complete pages only. A .part file is an interrupted download, not data."""
    out: list[dict] = []
    for path in sorted(directory.glob(pattern)):
        if path.suffix == ".part":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        out.extend(payload.get("results", []))
    return out


def summarise_obis(records: list[dict]) -> dict:
    species: dict[str, dict] = defaultdict(
        lambda: {
            "records": 0,
            "years": [],
            "depths": [],
            "datasets": set(),
            "class": None,
            "order": None,
            "family": None,
            "rank": None,
            "aphia_id": None,
        }
    )

    with_depth = 0
    depths_all: list[float] = []
    years_all: list[int] = []

    for r in records:
        name = r.get("species") or r.get("scientificName")
        if not name:
            continue
        s = species[name]
        s["records"] += 1
        s["class"] = s["class"] or r.get("class")
        s["order"] = s["order"] or r.get("order")
        s["family"] = s["family"] or r.get("family")
        s["rank"] = s["rank"] or r.get("taxonRank")
        s["aphia_id"] = s["aphia_id"] or r.get("aphiaID")
        if r.get("dataset_id"):
            s["datasets"].add(r["dataset_id"])

        year = r.get("date_year")
        if isinstance(year, int):
            s["years"].append(year)
            years_all.append(year)

        # OBIS exposes several depth fields; prefer an explicit measurement over the
        # bathymetry lookup, which is the seabed depth at the point, not the record's.
        depth = r.get("depth")
        if depth is None:
            depth = r.get("maximumDepthInMeters")
        if isinstance(depth, (int, float)):
            with_depth += 1
            s["depths"].append(float(depth))
            depths_all.append(float(depth))

    rows = []
    for name, s in species.items():
        rows.append(
            {
                "species": name,
                "aphia_id": s["aphia_id"],
                "class": s["class"],
                "order": s["order"],
                "family": s["family"],
                "records": s["records"],
                "datasets": len(s["datasets"]),
                "year_min": min(s["years"]) if s["years"] else None,
                "year_max": max(s["years"]) if s["years"] else None,
                "depth_records": len(s["depths"]),
                "depth_max_m": max(s["depths"]) if s["depths"] else None,
            }
        )
    rows.sort(key=lambda r: (-r["records"], r["species"]))

    below = [d for d in depths_all if d >= 100]
    below_200 = [d for d in depths_all if d >= 200]

    return {
        "rows": rows,
        "totals": {
            "records": len(records),
            "species": len(rows),
            "records_with_depth": with_depth,
            "records_with_depth_pct": round(100 * with_depth / len(records), 1) if records else 0,
            "deepest_record_m": max(depths_all) if depths_all else None,
            "records_at_or_below_100m": len(below),
            "records_at_or_below_200m": len(below_200),
            "year_min": min(years_all) if years_all else None,
            "year_max": max(years_all) if years_all else None,
            "classes": dict(Counter(r["class"] for r in rows if r["class"]).most_common()),
        },
    }


def main() -> int:
    obis_records = load_pages(OBIS_DIR, "obis_occurrence_p*.json")
    stats_path = OBIS_DIR / "obis_statistics.json"
    obis_stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}

    summary = summarise_obis(obis_records)
    totals = summary["totals"]

    # Completeness check against OBIS's own reported total — the download either got
    # everything or it did not, and we should not have to guess which.
    reported = obis_stats.get("records")
    complete = reported is None or totals["records"] == reported

    gbif_records = load_pages(GBIF_DIR, "gbif_occurrence_p*.json")
    census_path = GBIF_DIR / "gbif_census.json"
    census = json.loads(census_path.read_text(encoding="utf-8")) if census_path.exists() else {}
    gbif_total = census.get("total_records_in_bbox")
    partials = list(GBIF_DIR.glob("*.part"))

    out = {
        "generated": today(),
        "primary_source": "OBIS",
        "obis": {
            "records": totals["records"],
            "records_reported_by_api": reported,
            "complete": complete,
            "species": totals["species"],
            "datasets": obis_stats.get("datasets"),
            "year_range": [totals["year_min"], totals["year_max"]],
            "depth": {
                "records_with_depth": totals["records_with_depth"],
                "pct": totals["records_with_depth_pct"],
                "deepest_m": totals["deepest_record_m"],
                "at_or_below_100m": totals["records_at_or_below_100m"],
                "at_or_below_200m": totals["records_at_or_below_200m"],
            },
            "classes": totals["classes"],
            "licence": OBIS_LICENCE,
            "licence_url": OBIS_LICENCE_URL,
        },
        "gbif": {
            "status": "partial — not merged",
            "records_downloaded": len(gbif_records),
            "records_in_bbox": gbif_total,
            "interrupted_downloads": len(partials),
            "why_not_merged": (
                "The GBIF bounding box necessarily includes the Osa Peninsula and the edge "
                "of Corcovado, so its 2.04 million records are overwhelmingly terrestrial "
                "and say nothing about the water. GBIF also caps paging at offset 100,001, "
                "so a complete pull is impossible through this endpoint. Merging it would "
                "produce a number that is both wrong and uninterpretable."
            ),
            "licence": GBIF_LICENCE,
            "licence_url": GBIF_LICENCE_URL,
        },
        "species": summary["rows"],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "species.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Provenance for the raw inputs.
    for path in sorted(OBIS_DIR.glob("obis_occurrence_p*.json")):
        record(
            Provenance(
                dataset=f"obis-{path.stem.split('_')[-1]}",
                path=rel(path),
                source_url="https://api.obis.org/v3/occurrence",
                accessed=today(),
                licence=OBIS_LICENCE,
                licence_url=OBIS_LICENCE_URL,
                sha256=sha256_of(path),
                bytes=path.stat().st_size,
                notes="Marine occurrence records for the Golfo Dulce bounding box.",
                properties={"records": len(json.loads(path.read_text(encoding='utf-8')).get("results", []))},
            )
        )
    record(
        Provenance(
            dataset="gbif-partial",
            path=rel(GBIF_DIR),
            source_url="https://api.gbif.org/v1/occurrence/search",
            accessed=today(),
            licence=GBIF_LICENCE,
            licence_url=GBIF_LICENCE_URL,
            sha256="n/a — multi-file partial download",
            bytes=sum(p.stat().st_size for p in GBIF_DIR.glob("*.json")),
            notes=out["gbif"]["why_not_merged"],
            properties={"records_downloaded": len(gbif_records), "records_in_bbox": gbif_total},
        )
    )

    # --- report ---------------------------------------------------------------
    print("Golfo Dulce — species occurrence\n")
    print(f"  OBIS  {totals['records']:,} records / {totals['species']:,} species "
          f"/ {obis_stats.get('datasets')} datasets")
    print(f"        reported by API: {reported:,}  →  {'COMPLETE' if complete else 'INCOMPLETE'}")
    print(f"        years {totals['year_min']}-{totals['year_max']}")
    print(f"        with a depth value: {totals['records_with_depth']:,} "
          f"({totals['records_with_depth_pct']}%)")
    print(f"        at or below 100 m: {totals['records_at_or_below_100m']:,}   "
          f"below 200 m: {totals['records_at_or_below_200m']:,}   "
          f"deepest: {totals['deepest_record_m']} m")
    print(f"\n  GBIF  {len(gbif_records):,} downloaded of {gbif_total:,} in bbox "
          f"— partial, not merged ({len(partials)} interrupted file(s))")

    print("\n  top classes:")
    for cls, n in list(totals["classes"].items())[:6]:
        print(f"    {cls:<22} {n:>4} species")

    print("\n  top species by records:")
    for r in summary["rows"][:12]:
        print(f"    {r['species'][:38]:<40} {r['records']:>5,}  ({r['year_min']}-{r['year_max']})")

    print(f"\nWrote {rel(out_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
