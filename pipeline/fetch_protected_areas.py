"""Fetch protected-area boundaries around the Golfo Dulce from the WDPA.

Source is UNEP-WCMC and IUCN's World Database on Protected Areas, queried live from
their own public ArcGIS MapServer (no API key required, unlike Protected Planet's REST
API which does). Confirmed authoritative by cross-checking against the conservation
literature harvest: every site returned here — Corcovado, the Golfo Dulce Forest
Reserve, Golfito and Osa wildlife refuges, Piedras Blancas, Marino Ballena, and the
Térraba-Sierpe wetland (both its national and Ramsar designations) — matches what
bibliography/harvest/conservation.json already documents from primary sources.

One deliberate omission, worth stating plainly: the Área Marina de Pesca Responsable
(AMPR) Golfo Dulce — the fisheries co-management zone actually covering the gulf's own
water column, and the single most locally relevant designation — is NOT in the WDPA.
It is an INCOPESCA fisheries instrument, not an IUCN-classified protected area, and no
boundary polygon for it was found in any freely queryable source. That gap is recorded
here and should be reflected honestly on the site rather than papered over by silently
drawing a boundary that was never sourced.

Usage:
    python pipeline/fetch_protected_areas.py
"""

from __future__ import annotations

import json

import requests
import shapely.geometry as sg
from shapely.ops import transform as shp_transform

import shutil

from common import RAW, DERIVED, ROOT, Provenance, USER_AGENT, record, rel, sha256_of, today

WDPA_LAYER = (
    "https://data-gis.unep-wcmc.org/server/rest/services/ProtectedSites/"
    "The_World_Database_of_Protected_Areas/MapServer/1/query"
)

# Identified by name search against the WDPA within a wide Costa Rica bbox, then locked
# to these site_ids so re-running this script is deterministic rather than re-searching.
SITE_IDS = [164, 3317, 12492, 102367, 108151, 145527, 30595, 555643534]

LICENCE = (
    "UNEP-WCMC and IUCN (2026), Protected Planet: The World Database on Protected Areas "
    "(WDPA) [Online], Cambridge, UK. DOI: https://doi.org/10.34892/6fwd-af11"
)
LICENCE_URL = "https://www.protectedplanet.net/en/terms-and-conditions"

RAW_OUT = RAW / "wdpa" / "protected_areas_raw.geojson"
SIMPLIFIED_OUT = DERIVED / "conservation" / "protected_areas.geojson"

# Metres-equivalent tolerance for simplification, applied in degrees (~0.0005 deg is
# roughly 55 m at this latitude) — enough to cut vertex count drastically while keeping
# boundaries visually indistinguishable from the source at web map zoom levels.
SIMPLIFY_TOLERANCE_DEG = 0.0005


def fetch_raw() -> dict:
    where = f"site_id IN ({','.join(str(s) for s in SITE_IDS)})"
    params = {
        "where": where,
        "outFields": "name,desig_eng,iucn_cat,status_yr,gis_area,site_id,realm,mang_auth",
        "returnGeometry": "true",
        "geometryPrecision": 5,
        "outSR": 4326,
        "f": "geojson",
    }
    resp = requests.get(WDPA_LAYER, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def simplify_feature(feat: dict) -> dict:
    geom = sg.shape(feat["geometry"]).simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
    return {"type": "Feature", "properties": feat["properties"], "geometry": sg.mapping(geom)}


def main() -> int:
    print("WDPA — protected areas around the Golfo Dulce\n")

    raw = fetch_raw()
    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    RAW_OUT.write_text(json.dumps(raw), encoding="utf-8")

    features = raw["features"]
    print(f"  fetched {len(features)} feature(s)")

    simplified = [simplify_feature(f) for f in features]
    out = {"type": "FeatureCollection", "features": simplified}

    SIMPLIFIED_OUT.parent.mkdir(parents=True, exist_ok=True)
    SIMPLIFIED_OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    before_kb = RAW_OUT.stat().st_size // 1024
    after_kb = SIMPLIFIED_OUT.stat().st_size // 1024
    print(f"  simplified: {before_kb} KB -> {after_kb} KB")

    for f in simplified:
        p = f["properties"]
        print(f"    {p['name']:<45} {p['desig_eng']:<45} ({p['status_yr']})")

    for path, dataset in [(RAW_OUT, "wdpa-protected-areas-raw"), (SIMPLIFIED_OUT, "wdpa-protected-areas-simplified")]:
        record(
            Provenance(
                dataset=dataset,
                path=rel(path),
                source_url=WDPA_LAYER,
                accessed=today(),
                licence=LICENCE,
                licence_url=LICENCE_URL,
                sha256=sha256_of(path),
                bytes=path.stat().st_size,
                notes=(
                    "AMPR Golfo Dulce, the fisheries co-management zone actually "
                    "covering the gulf's water, is NOT in the WDPA — it is an "
                    "INCOPESCA instrument, not IUCN-classified. No boundary was found "
                    "in any freely queryable source; this is a real gap, not an "
                    "oversight."
                ),
                properties={"site_ids": SITE_IDS, "feature_count": len(simplified)},
            )
        )

    # The site serves this directly as a static asset; keep the copy in sync with the
    # derived output rather than relying on someone remembering a manual step.
    public_copy = ROOT / "site" / "public" / "data" / "protected-areas.geojson"
    public_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SIMPLIFIED_OUT, public_copy)

    print(f"\nWrote {rel(RAW_OUT)}, {rel(SIMPLIFIED_OUT)} and {rel(public_copy)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
