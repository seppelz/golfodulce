"""Fetch species occurrence records for the Golfo Dulce from OBIS and GBIF.

Two registries, two very different shapes of answer, and the difference is the
interesting part.

OBIS is marine by construction: everything in it has been through WoRMS, so the
bbox returns a tractable few thousand records that are all actually about the sea.
We take those in full.

GBIF is not scoped that way. The same bbox returns ~2.04 million records, because
the box necessarily contains the Osa Peninsula and Golfito, and eBird alone has
contributed 1.29 million land-bird observations there. Three facts follow, and they
drive the whole design of this script:

  1. GBIF's search API refuses any request where offset + limit > 100,001. Two
     million records simply cannot be paged out of it. (The bulk download API can,
     but it needs credentials and returns an archive, not JSON.)
  2. At ~6.6 kB per record, two million records would be ~13 GB of raw JSON.
  3. Almost none of it is about the gulf. It is the terrestrial biota of the
     surrounding peninsula.

So the GBIF pull here is scoped to aquatic taxa, by an explicit list of taxon keys
declared below. The scope is a judgement call, so it is made in the open: the full
unscoped census (every dataset, class and phylum with its record count) is
downloaded and kept as its own raw artefact, so the 2.04 million records we did
*not* pull record-level are documented rather than quietly dropped.

One trap worth naming, because it would have silently destroyed the fish data.
GBIF's backbone has no class Actinopterygii. Ray-finned fish orders — Perciformes,
Clupeiformes, Pleuronectiformes and 44 others — hang directly off phylum Chordata
with class set to null. Any scope built on class names therefore contains no bony
fish at all, in a script about a marine basin. The aquatic scope below is built
from the ORDER-rank children of Chordata for exactly this reason, and it is derived
from the API at runtime rather than hardcoded, so it stays correct if the backbone
is reorganised again.

A second trap, also worth naming: GBIF silently ignores query parameters it does
not recognise. Passing `kingdom=Animalia` does not filter, it does nothing, and the
response looks perfectly healthy. Every filter this script relies on is therefore
verified against a known-bad value at startup (see `verify_filters`).

Usage:
    python pipeline/fetch_occurrence.py
    python pipeline/fetch_occurrence.py --force    # re-download instead of reusing raw
    python pipeline/fetch_occurrence.py --verify   # re-check manifest against disk
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import requests

from common import (
    BBOX,
    DERIVED,
    MANIFEST,
    RAW,
    USER_AGENT,
    Provenance,
    download,
    record,
    rel,
    sha256_of,
    today,
)

OBIS_OCCURRENCE = "https://api.obis.org/v3/occurrence"
OBIS_STATISTICS = "https://api.obis.org/v3/statistics"
GBIF_SEARCH = "https://api.gbif.org/v1/occurrence/search"
GBIF_SPECIES = "https://api.gbif.org/v1/species"
GBIF_DATASET = "https://api.gbif.org/v1/dataset"

# Page sizes. Both are the documented maxima; going over returns HTTP 400 rather
# than silently truncating, which we rely on staying true.
OBIS_PAGE = 5000  # hard cap is 10000
GBIF_PAGE = 300  # hard cap is 300
GBIF_MAX_OFFSET = 100_001

SLEEP = 0.15  # be polite; neither API documents a rate limit but both are free

CHORDATA_KEY = 44

# Marine/estuarine phyla. Arthropoda is deliberately absent — it is 498k records in
# this bbox and overwhelmingly Insecta — so the aquatic arthropods come in by class.
MARINE_PHYLA = [
    "Mollusca", "Cnidaria", "Echinodermata", "Annelida", "Porifera", "Bryozoa",
    "Nemertea", "Brachiopoda", "Chaetognatha", "Ctenophora", "Rhodophyta",
    "Ochrophyta", "Chlorophyta", "Foraminifera", "Myzozoa", "Xenacoelomorpha",
    "Platyhelminthes", "Sipuncula", "Echiura", "Kinorhyncha", "Rotifera", "Charophyta",
]

# Non-fish chordate classes that are marine or largely marine. Aves and Mammalia are
# excluded: both are dominated here by terrestrial records, and the marine members
# (pelicans, cetaceans) are already in OBIS, which applies a marine filter properly.
MARINE_CHORDATE_CLASSES = {
    "Elasmobranchii": 121,
    "Holocephali": 120,
    "Myxini": 119,
    "Petromyzonti": 11881065,
    "Ascidiacea": 356,
    "Thaliacea": 207,
    "Leptocardii": 7375758,
    "Coelacanthi": 11733052,
    "Testudines": 11418114,  # sea turtles; the bbox total is only ~314
}

# Aquatic arthropod classes, pulled in individually rather than via Arthropoda.
AQUATIC_ARTHROPOD_CLASSES = {
    "Malacostraca": 229, "Maxillopoda": 775, "Ostracoda": 352,
    "Branchiopoda": 230, "Pycnogonida": 232, "Merostomata": 231,
    "Thecostraca": 7900184, "Copepoda": 1088,
}

OBIS_LICENCE = "Varies by dataset; OBIS aggregate is CC-BY 4.0"
OBIS_LICENCE_URL = "https://obis.org/manual/access/"
GBIF_LICENCE = "Varies by dataset (CC0-1.0 / CC-BY-4.0 / CC-BY-NC-4.0)"
GBIF_LICENCE_URL = "https://www.gbif.org/terms"

WKT = (
    f"POLYGON(({BBOX['west']} {BBOX['south']}, {BBOX['east']} {BBOX['south']}, "
    f"{BBOX['east']} {BBOX['north']}, {BBOX['west']} {BBOX['north']}, "
    f"{BBOX['west']} {BBOX['south']}))"
)
GBIF_BOX = {
    "decimalLatitude": f"{BBOX['south']},{BBOX['north']}",
    "decimalLongitude": f"{BBOX['west']},{BBOX['east']}",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


# --------------------------------------------------------------------------- utils


def effective_url(url: str, params: dict) -> str:
    return requests.Request("GET", url, params=params).prepare().url


def get_json(url: str, params: dict, timeout: int = 300) -> dict:
    """For metadata lookups that are not themselves archived as raw files."""
    r = SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    time.sleep(SLEEP)
    return r.json()


def fetch_page(url: str, params: dict, dest: Path, *, force: bool) -> dict:
    """Download one API response to its own immutable raw file and return the parsed
    body. Existing files are reused unless --force, so a re-run does not churn raw."""
    if dest.exists() and not force:
        return json.loads(dest.read_text(encoding="utf-8"))
    download(url, dest, params=params, timeout=300)
    time.sleep(SLEEP)
    return json.loads(dest.read_text(encoding="utf-8"))


def norm_name(name: str | None) -> str:
    """Canonical-ish comparison key for a taxon name: lowercase, authorship removed,
    whitespace collapsed."""
    if not name:
        return ""
    n = re.sub(r"\(.*?\)", " ", name)  # parenthetical authorship / subgenus
    n = re.sub(r",\s*\d{4}.*$", " ", n)  # trailing ", 1758"
    n = re.sub(r"\s+\d{4}.*$", " ", n)
    n = re.sub(r"[^A-Za-z .×-]", " ", n)
    n = re.sub(r"\s+", " ", n).strip().lower()
    return n


def as_float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # reject NaN


def as_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ filter sanity


def verify_filters() -> None:
    """GBIF drops unrecognised query parameters without complaint, so a filter that
    appears to work may be doing nothing at all. Prove each one narrows the result
    set before trusting it."""
    total = get_json(GBIF_SEARCH, {**GBIF_BOX, "limit": 0})["count"]
    unfiltered = get_json(GBIF_SEARCH, {"limit": 0})["count"]
    if not total < unfiltered:
        raise SystemExit("! GBIF coordinate filter is not narrowing the result set")
    aves = get_json(GBIF_SEARCH, {**GBIF_BOX, "limit": 0, "classKey": 212})["count"]
    bogus = get_json(GBIF_SEARCH, {**GBIF_BOX, "limit": 0, "classKey": 99_999_999})["count"]
    if not (aves < total and bogus == 0):
        raise SystemExit("! GBIF classKey/taxonKey filtering is not being applied")
    print(f"  filter check ok: bbox {total:,} of {unfiltered:,} global; "
          f"classKey narrows to {aves:,}, bogus key -> {bogus}")


# ------------------------------------------------------------------------- OBIS


def fetch_obis(force: bool) -> tuple[list[dict], list[Path], dict]:
    print("\nOBIS")
    stats_path = RAW / "obis" / "obis_statistics.json"
    stats = fetch_page(OBIS_STATISTICS, {"geometry": WKT}, stats_path, force=force)
    print(f"  statistics: {stats.get('records'):,} records, {stats.get('species')} species, "
          f"{stats.get('datasets')} datasets, years {stats.get('yearrange')}")

    total = stats.get("records")
    records: list[dict] = []
    paths = [stats_path]
    after: str | None = None
    page = 0
    seen_ids: set[str] = set()

    while True:
        page += 1
        params: dict[str, Any] = {"geometry": WKT, "size": OBIS_PAGE}
        if after:
            params["after"] = after
        dest = RAW / "obis" / f"obis_occurrence_p{page:03d}.json"
        body = fetch_page(OBIS_OCCURRENCE, params, dest, force=force)
        results = body.get("results", [])
        paths.append(dest)
        print(f"  page {page}: {len(results):,} records -> {rel(dest)}")
        if not results:
            paths.pop()
            dest.unlink(missing_ok=True)
            break
        for r in results:
            rid = r.get("id")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            records.append(r)
        after = results[-1].get("id")
        if len(results) < OBIS_PAGE:
            break
        if page > 50:
            raise SystemExit("! OBIS paging did not terminate")

    print(f"  downloaded {len(records):,} records (API total reported {total:,})")
    if total and abs(len(records) - total) > 0:
        print(f"  ! paging returned {len(records):,}, statistics said {total:,}")
    return records, paths, stats


# ------------------------------------------------------------------------- GBIF


def aquatic_taxon_keys() -> dict[str, list[int]]:
    """Build the aquatic scope from the live backbone.

    The fish component is every ORDER-rank child of Chordata. That is not a
    stylistic choice: GBIF has no Actinopterygii class, so the orders *are* the
    only handle on bony fish."""
    kids: list[dict] = []
    offset = 0
    while True:
        body = get_json(f"{GBIF_SPECIES}/{CHORDATA_KEY}/children",
                        {"limit": 100, "offset": offset})
        kids += body.get("results", [])
        if body.get("endOfRecords", True):
            break
        offset += 100
    fish = sorted({k["key"] for k in kids if k.get("rank") == "ORDER"})

    phyla: dict[str, int] = {}
    for name in MARINE_PHYLA:
        m = get_json(f"{GBIF_SPECIES}/match", {"name": name, "rank": "PHYLUM"})
        if m.get("usageKey") and m.get("matchType") != "NONE":
            phyla[name] = m["usageKey"]

    print(f"  aquatic scope: {len(fish)} fish orders (Chordata ORDER-rank children), "
          f"{len(phyla)}/{len(MARINE_PHYLA)} marine phyla resolved, "
          f"{len(MARINE_CHORDATE_CLASSES)} chordate classes, "
          f"{len(AQUATIC_ARTHROPOD_CLASSES)} arthropod classes")
    return {
        "fish_orders": fish,
        "marine_phyla": sorted(phyla.values()),
        "chordate_classes": sorted(MARINE_CHORDATE_CLASSES.values()),
        "arthropod_classes": sorted(AQUATIC_ARTHROPOD_CLASSES.values()),
    }


def facet_all(field: str, limit: int = 300) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        body = get_json(GBIF_SEARCH, {**GBIF_BOX, "limit": 0, "facet": field,
                                      "facetLimit": limit, "facetOffset": offset})
        facets = body.get("facets") or []
        counts = facets[0]["counts"] if facets else []
        out += counts
        if len(counts) < limit:
            break
        offset += limit
    return out


def fetch_gbif(force: bool) -> tuple[list[dict], list[Path], dict, dict]:
    print("\nGBIF")
    scope = aquatic_taxon_keys()
    keys = sorted({k for group in scope.values() for k in group})

    # The unscoped census: what the bbox really holds, kept so the scoping decision
    # is auditable rather than invisible.
    census_path = RAW / "gbif" / "gbif_census.json"
    if census_path.exists() and not force:
        census = json.loads(census_path.read_text(encoding="utf-8"))
    else:
        total = get_json(GBIF_SEARCH, {**GBIF_BOX, "limit": 0})["count"]
        census = {
            "_comment": "Unscoped census of the full bbox. Documents the records that "
                        "were NOT pulled at record level, and why.",
            "bbox": BBOX,
            "accessed": today(),
            "source_url": effective_url(GBIF_SEARCH, {**GBIF_BOX, "limit": 0}),
            "total_records_in_bbox": total,
            "gbif_max_offset": GBIF_MAX_OFFSET,
            "facets": {f: facet_all(f) for f in
                       ("datasetKey", "classKey", "phylumKey", "kingdomKey", "basisOfRecord")},
        }
        census_path.parent.mkdir(parents=True, exist_ok=True)
        census_path.write_text(json.dumps(census, indent=2) + "\n", encoding="utf-8")
    bbox_total = census["total_records_in_bbox"]
    ds_facet = census["facets"]["datasetKey"]
    print(f"  full bbox holds {bbox_total:,} records across {len(ds_facet)} datasets "
          f"(sum of dataset facet = {sum(c['count'] for c in ds_facet):,})")
    over = [c for c in ds_facet if c["count"] > GBIF_MAX_OFFSET]
    print(f"  {len(over)} datasets individually exceed the {GBIF_MAX_OFFSET:,} offset cap")

    scoped_params = {**GBIF_BOX, "taxonKey": keys}
    total = get_json(GBIF_SEARCH, {**scoped_params, "limit": 0})["count"]
    print(f"  aquatic scope selects {total:,} records "
          f"({100 * total / bbox_total:.2f}% of the bbox)")
    if total > GBIF_MAX_OFFSET:
        raise SystemExit("! aquatic scope exceeds the offset cap; needs partitioning")

    records: list[dict] = []
    paths: list[Path] = [census_path]
    offset = 0
    page = 0
    while True:
        page += 1
        params = {**scoped_params, "limit": GBIF_PAGE, "offset": offset}
        dest = RAW / "gbif" / f"gbif_occurrence_p{page:03d}.json"
        body = fetch_page(GBIF_SEARCH, params, dest, force=force)
        results = body.get("results", [])
        if not results:
            dest.unlink(missing_ok=True)
            break
        paths.append(dest)
        records += results
        if page % 10 == 0 or body.get("endOfRecords"):
            print(f"  page {page}: {len(records):,}/{total:,}")
        if body.get("endOfRecords"):
            break
        offset += GBIF_PAGE
        if offset + GBIF_PAGE > GBIF_MAX_OFFSET:
            print("  ! hit GBIF offset cap; remainder not retrievable via search API")
            break

    print(f"  downloaded {len(records):,} records over {page} pages")

    # Dataset metadata, for the licence picture.
    keys_seen = sorted({r.get("datasetKey") for r in records if r.get("datasetKey")})
    ds_path = RAW / "gbif" / "gbif_datasets.json"
    if ds_path.exists() and not force:
        datasets = json.loads(ds_path.read_text(encoding="utf-8"))
    else:
        datasets = {}
        for i, k in enumerate(keys_seen, 1):
            m = get_json(f"{GBIF_DATASET}/{k}", {})
            datasets[k] = {"title": m.get("title"), "license": m.get("license"),
                           "publisher": m.get("publishingOrganizationKey"),
                           "doi": m.get("doi")}
            if i % 25 == 0:
                print(f"    dataset metadata {i}/{len(keys_seen)}")
        ds_path.write_text(json.dumps(datasets, indent=2) + "\n", encoding="utf-8")
    paths.append(ds_path)
    print(f"  {len(datasets)} contributing datasets described")
    return records, paths, census, datasets


# -------------------------------------------------------------------- normalise


def canonical_name(r: dict, source: str) -> str:
    if source == "obis":
        return (r.get("species") or r.get("scientificName") or "").strip()
    sp = r.get("species")
    if sp:
        return sp.strip()
    gn, se = r.get("genericName"), r.get("specificEpithet")
    if gn and se:
        return f"{gn} {se}"
    # Higher ranks carry no authorship in GBIF, so the raw name is already canonical.
    return (r.get("scientificName") or "").strip()


def depth_of(r: dict, source: str) -> float | None:
    """Observed depth only.

    OBIS decorates every record with a `bathymetry` field looked up from a global
    grid. That is the depth of the seabed at the coordinate, not the depth the
    organism was found at, and treating it as an observation would invent data —
    especially here, where the whole point is what lives above and below the
    anoxic boundary. It is deliberately not consulted."""
    for f in ("depth", "minimumDepthInMeters", "maximumDepthInMeters"):
        d = as_float(r.get(f))
        if d is not None:
            return d
    return None


def year_of(r: dict, source: str) -> int | None:
    y = as_int(r.get("date_year") if source == "obis" else r.get("year"))
    if y and 1500 < y < 2100:
        return y
    ev = r.get("eventDate")
    if isinstance(ev, str) and len(ev) >= 4 and ev[:4].isdigit():
        y = int(ev[:4])
        if 1500 < y < 2100:
            return y
    return None


def dedup_key(r: dict, source: str) -> tuple:
    """OBIS and GBIF republish many of the same underlying datasets — iNaturalist
    and the museum collections appear in both — so concatenating them double-counts.
    occurrenceID is the Darwin Core identifier the original publisher assigned, and
    it survives into both aggregators unchanged, which makes it the honest join."""
    oid = (r.get("occurrenceID") or "").strip().lower()
    oid = re.sub(r"^https?://", "", oid).rstrip("/")
    if oid:
        return ("oid", oid)
    inst = (r.get("institutionCode") or "").strip().lower()
    cat = (r.get("catalogNumber") or "").strip().lower()
    if inst and cat:
        return ("cat", inst, cat)
    lat, lon = as_float(r.get("decimalLatitude")), as_float(r.get("decimalLongitude"))
    return ("obs", norm_name(canonical_name(r, source)),
            round(lat, 5) if lat is not None else None,
            round(lon, 5) if lon is not None else None,
            (r.get("eventDate") or "")[:10])


def normalise(r: dict, source: str) -> dict:
    return {
        "source": source,
        "name": canonical_name(r, source),
        "rank": (r.get("taxonRank") or "").lower() or None,
        "kingdom": r.get("kingdom"),
        "phylum": r.get("phylum"),
        "class": r.get("class"),
        "order": r.get("order"),
        "family": r.get("family"),
        "aphia_id": as_int(r.get("aphiaID")),
        "gbif_key": as_int(r.get("speciesKey") or r.get("taxonKey")),
        "year": year_of(r, source),
        "depth": depth_of(r, source),
        "dataset": (r.get("datasetName") or r.get("dataset_id")
                    or r.get("datasetKey") or "unknown"),
        "dataset_key": r.get("datasetKey") or r.get("dataset_id"),
        "licence": r.get("license") or r.get("licence"),
        "lat": as_float(r.get("decimalLatitude")),
        "lon": as_float(r.get("decimalLongitude")),
        "key": dedup_key(r, source),
    }


# ---------------------------------------------------------------------- combine


def build_species(obis: list[dict], gbif: list[dict], datasets: dict) -> dict:
    rows = [normalise(r, "obis") for r in obis] + [normalise(r, "gbif") for r in gbif]

    merged: dict[tuple, dict] = {}
    dup_pairs = 0
    for row in rows:
        k = row["key"]
        prev = merged.get(k)
        if prev is None:
            merged[k] = row
            continue
        if prev["source"] != row["source"]:
            dup_pairs += 1
            # OBIS taxonomy comes via WoRMS and is the better marine authority;
            # GBIF is the better source of a populated class for non-fish.
            base, other = (prev, row) if prev["source"] == "obis" else (row, prev)
            for f in ("class", "order", "family", "phylum", "kingdom", "depth", "year"):
                if base.get(f) is None and other.get(f) is not None:
                    base[f] = other[f]
            base["source"] = "both"
            merged[k] = base

    deduped = list(merged.values())

    # Bony fish reach GBIF with class=null (no Actinopterygii in the backbone).
    # Where OBIS knows the class for the same name, borrow it, so the derived file
    # is not full of holes that are an artefact of one registry's taxonomy.
    class_by_name: dict[str, str] = {}
    for row in deduped:
        n = norm_name(row["name"])
        if n and row.get("class") and n not in class_by_name:
            class_by_name[n] = row["class"]
    backfilled = 0
    for row in deduped:
        if not row.get("class"):
            c = class_by_name.get(norm_name(row["name"]))
            if c:
                row["class"] = c
                backfilled += 1

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in deduped:
        n = norm_name(row["name"])
        if n:
            groups[n].append(row)

    species = []
    for _, rs in groups.items():
        years = [r["year"] for r in rs if r["year"]]
        depths = [r["depth"] for r in rs if r["depth"] is not None]
        srcs = {r["source"] for r in rs}
        sources = sorted({s for src in srcs for s in (["obis", "gbif"] if src == "both" else [src])})
        lic = sorted({r["licence"] for r in rs if r["licence"]})
        dsets = sorted({r["dataset"] for r in rs if r["dataset"]})

        def most_common(field: str) -> Any:
            vals = [r[field] for r in rs if r.get(field)]
            return max(set(vals), key=vals.count) if vals else None

        species.append({
            "scientific_name": most_common("name") or rs[0]["name"],
            "taxon_rank": most_common("rank"),
            "kingdom": most_common("kingdom"),
            "phylum": most_common("phylum"),
            "class": most_common("class"),
            "order": most_common("order"),
            "family": most_common("family"),
            "aphia_id": most_common("aphia_id"),
            "gbif_taxon_key": most_common("gbif_key"),
            "records": len(rs),
            "records_obis": sum(1 for r in rs if r["source"] in ("obis", "both")),
            "records_gbif": sum(1 for r in rs if r["source"] in ("gbif", "both")),
            "year_min": min(years) if years else None,
            "year_max": max(years) if years else None,
            "records_with_depth": len(depths),
            "depth_min_m": min(depths) if depths else None,
            "depth_max_m": max(depths) if depths else None,
            "sources": sources,
            "datasets": dsets[:25],
            "dataset_count": len(dsets),
            "licences": lic,
        })

    species.sort(key=lambda s: (-s["records"], s["scientific_name"]))

    all_depths = [r["depth"] for r in deduped if r["depth"] is not None]
    lic_counts: dict[str, int] = defaultdict(int)
    for r in deduped:
        lic_counts[r["licence"] or "unspecified"] += 1
    ds_lic: dict[str, int] = defaultdict(int)
    for meta in datasets.values():
        ds_lic[meta.get("license") or "unspecified"] += 1

    return {
        "generated": today(),
        "bbox": BBOX,
        "geometry_wkt": WKT,
        "sources": {
            "obis": {"endpoint": OBIS_OCCURRENCE, "records_downloaded": len(obis),
                     "scope": "complete: every OBIS record in the bbox"},
            "gbif": {"endpoint": GBIF_SEARCH, "records_downloaded": len(gbif),
                     "scope": "aquatic taxa only; see notes"},
        },
        "notes": [
            "GBIF holds ~2.04M records in this bbox, overwhelmingly terrestrial "
            "(eBird 1.29M land-bird observations, INBio/iBOL insects ~500k). Its "
            "search API caps offset+limit at 100,001, so the full set is not "
            "retrievable this way and would be ~13 GB regardless. The GBIF pull is "
            "therefore scoped to aquatic taxa; the unscoped census of every dataset, "
            "class and phylum is kept at data/raw/gbif/gbif_census.json.",
            "GBIF's backbone has no class Actinopterygii: bony-fish orders hang "
            "directly off Chordata with class=null. The scope is built from the "
            "ORDER-rank children of Chordata so fish are not silently excluded.",
            "Depth is observed depth only. OBIS's `bathymetry` field is a seabed "
            "lookup from a global grid, not an observation, and is not used.",
            "Deduplication is on Darwin Core occurrenceID where present, falling "
            "back to institutionCode+catalogNumber, then to name+coordinate+date.",
        ],
        "totals": {
            "records_obis_raw": len(obis),
            "records_gbif_raw": len(gbif),
            "records_concatenated": len(rows),
            "records_deduplicated": len(deduped),
            "duplicates_removed": len(rows) - len(deduped),
            "cross_source_duplicate_pairs": dup_pairs,
            "records_from_both_registries": sum(1 for r in deduped if r["source"] == "both"),
            "species_or_taxa": len(species),
            "records_with_depth": len(all_depths),
            "depth_min_m": min(all_depths) if all_depths else None,
            "depth_max_m": max(all_depths) if all_depths else None,
            "class_backfilled_from_sibling_records": backfilled,
            "year_min": min((r["year"] for r in deduped if r["year"]), default=None),
            "year_max": max((r["year"] for r in deduped if r["year"]), default=None),
        },
        "licences": {
            "by_record": dict(sorted(lic_counts.items(), key=lambda kv: -kv[1])),
            "by_gbif_dataset": dict(sorted(ds_lic.items(), key=lambda kv: -kv[1])),
        },
        "species": species,
    }


# ------------------------------------------------------------------- provenance


def record_file(dataset: str, path: Path, url: str, licence: str, licence_url: str,
                notes: str, props: dict) -> None:
    record(Provenance(
        dataset=dataset,
        path=rel(path),
        source_url=url,
        accessed=today(),
        licence=licence,
        licence_url=licence_url,
        sha256=sha256_of(path),
        bytes=path.stat().st_size,
        notes=notes,
        properties=props,
    ))


def count_records_in(path: Path) -> int:
    """Count records by reading the file back off disk. Deliberately independent of
    the download loop's own counter — the point is to catch a loop that lied."""
    body = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(body, dict) and isinstance(body.get("results"), list):
        return len(body["results"])
    if isinstance(body, dict) and isinstance(body.get("species"), list):
        return len(body["species"])
    if isinstance(body, dict):
        return len(body)
    return len(body) if isinstance(body, list) else 0


def verify() -> int:
    """Re-read the manifest and check every claim in it against the files on disk."""
    if not MANIFEST.exists():
        print("no manifest", file=sys.stderr)
        return 1
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = MANIFEST.parent.parent
    bad = 0
    total_claimed = 0
    for name, e in sorted(entries.items()):
        p = root / e["path"]
        if not p.exists():
            print(f"  MISSING {name}: {e['path']}")
            bad += 1
            continue
        digest = sha256_of(p)
        size = p.stat().st_size
        if digest != e["sha256"]:
            print(f"  SHA MISMATCH {name}")
            bad += 1
        if size != e["bytes"]:
            print(f"  SIZE MISMATCH {name}: disk {size} vs manifest {e['bytes']}")
            bad += 1
        props = e.get("properties") or {}
        claimed = props.get("records") or props.get("species_or_taxa")
        if claimed is not None and p.suffix == ".json":
            actual = count_records_in(p)
            if actual != claimed:
                print(f"  COUNT MISMATCH {name}: file has {actual}, manifest claims {claimed}")
                bad += 1
            else:
                total_claimed += actual
    print(f"\n  {len(entries)} manifest entries checked, {bad} problem(s), "
          f"{total_claimed:,} records verified by independent recount")
    return 1 if bad else 0


# ------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download raw files")
    ap.add_argument("--verify", action="store_true", help="verify manifest only")
    args = ap.parse_args()

    # stdout/stderr are already forced to UTF-8 by common.py.
    if args.verify:
        return verify()

    print("Species occurrence - Golfo Dulce")
    print(f"  bbox {BBOX['west']},{BBOX['south']} -> {BBOX['east']},{BBOX['north']}")
    verify_filters()

    obis, obis_paths, stats = fetch_obis(args.force)
    gbif, gbif_paths, census, datasets = fetch_gbif(args.force)

    print("\nProvenance")
    for p in obis_paths:
        is_stats = p.name.startswith("obis_statistics")
        body = json.loads(p.read_text(encoding="utf-8"))
        props: dict[str, Any] = ({"statistics": body} if is_stats
                                 else {"records": len(body.get("results", []))})
        record_file(
            f"obis-{p.stem.replace('obis_', '')}", p,
            effective_url(OBIS_STATISTICS if is_stats else OBIS_OCCURRENCE,
                          {"geometry": WKT} if is_stats else {"geometry": WKT, "size": OBIS_PAGE}),
            OBIS_LICENCE, OBIS_LICENCE_URL,
            "OBIS aggregates marine datasets under varying per-record licences; the "
            "`license` field on each record is authoritative, not this line.",
            props,
        )
    for p in gbif_paths:
        if p.name == "gbif_census.json":
            props = {"total_records_in_bbox": census["total_records_in_bbox"],
                     "datasets": len(census["facets"]["datasetKey"]),
                     "classes": len(census["facets"]["classKey"])}
            note = ("Unscoped facet census of the full bbox. Records the 2.04M records "
                    "that exist but were not pulled record-level, and why.")
        elif p.name == "gbif_datasets.json":
            props = {"records": len(datasets)}
            note = "Per-dataset title, DOI and licence for every contributing GBIF dataset."
        else:
            body = json.loads(p.read_text(encoding="utf-8"))
            props = {"records": len(body.get("results", [])),
                     "count_reported": body.get("count"),
                     "offset": body.get("offset")}
            note = ("Aquatic-scope page. GBIF licences vary per dataset; see "
                    "gbif_datasets.json and each record's `license` field.")
        record_file(f"gbif-{p.stem.replace('gbif_', '')}", p,
                    effective_url(GBIF_SEARCH, {**GBIF_BOX, "limit": GBIF_PAGE}),
                    GBIF_LICENCE, GBIF_LICENCE_URL, note, props)

    print("\nCombining")
    combined = build_species(obis, gbif, datasets)
    out = DERIVED / "occurrence" / "species.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    t = combined["totals"]
    print(f"  {t['records_obis_raw']:,} OBIS + {t['records_gbif_raw']:,} GBIF "
          f"= {t['records_concatenated']:,} concatenated")
    print(f"  {t['records_deduplicated']:,} after dedup "
          f"({t['duplicates_removed']:,} removed, "
          f"{t['cross_source_duplicate_pairs']:,} cross-registry)")
    print(f"  {t['species_or_taxa']:,} distinct taxa, "
          f"{t['records_with_depth']:,} records carry a depth")

    record_file("occurrence-species", out,
                "derived from obis-* and gbif-* raw files",
                "CC-BY 4.0 (aggregate); component records vary - see licences block",
                "https://creativecommons.org/licenses/by/4.0/",
                "Deduplicated union of OBIS and GBIF occurrence records for the bbox. "
                "GBIF component is aquatic-scoped; see the notes block in the file.",
                {"species_or_taxa": t["species_or_taxa"],
                 "records_deduplicated": t["records_deduplicated"],
                 "records_with_depth": t["records_with_depth"]})

    print(f"\nWrote {rel(out)} and provenance to {rel(MANIFEST)}")
    print("\nVerifying manifest against disk")
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
