"""Fetch a GEBCO-comparable global bathymetric compilation for the Golfo Dulce.

Worth being precise about what this actually is, because the honest label matters more
than the convenient one.

GEBCO's own subsetting infrastructure was tested directly and found non-functional for
scripted access at time of writing: the WCS service (`wms.gebco.net`, service=WCS)
advertises capabilities but rejects every coverage id tried, including the one its own
WMS lists (`InvalidParameterValue ... COVERAGE=GEBCO_LATEST not found`); the WMS GetMap
endpoint works but only returns rendered RGB PNG/TIFF images, not numeric elevation
values; and the GEBCO download tool at download.gebco.net is a client-side Next.js
application whose data API was not identified within a reasonable search.

Instead this pulls from NOAA NCEI's **DEM_global_mosaic** ImageServer, which blends
several global bathymetric/topographic compilations — GEBCO among them — into one
queryable raster service, and does return real float32 elevation values on request
(verified: -191 m at 8.60N/-83.30W, matching GMRT and the published literature at the
same point). This is a legitimate substitute for the "GEBCO extract" called for in the
original research plan, and it happens to also satisfy that plan's separate request for
satellite-derived bathymetry of the shallow margins, since the mosaic's shallow-water
tiles are satellite-derived (SRTM15+/altimetry-based) where no soundings exist.

It is explicitly NOT a pure GEBCO grid, and the site/bibliography should not describe it
as one — the citation for the GEBCO_2026 grid itself stays in bibliography/sources.json
as a literature reference regardless of whether this script could pull its raw cells.

Usage:
    python pipeline/fetch_gebco.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio

from common import BBOX, RAW, Provenance, download, record, rel, sha256_of, today

IMAGESERVER = (
    "https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics/DEM_global_mosaic/ImageServer/exportImage"
)

LICENCE = (
    "NOAA NCEI DEM mosaic: public domain (US government work). Constituent compilations, "
    "including GEBCO, retain their own attribution requirements — see the GEBCO_2026 grid "
    "entry in the bibliography for GEBCO's own terms."
)
LICENCE_URL = "https://www.ncei.noaa.gov/products/national-integrated-heat-health-information/policy"


def fetch() -> Path:
    dest = RAW / "gebco" / "dem_mosaic.tif"
    # ~60 m cells across the bbox, matching the GMRT grid's resolution so the two are
    # directly comparable cell-for-cell in analyse_coverage.py if useful later.
    params = {
        "bbox": f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}",
        "bboxSR": 4326,
        "imageSR": 4326,
        "size": "1095,832",
        "format": "tiff",
        "pixelType": "F32",
        "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "RSP_NearestNeighbor",
        "f": "image",
    }
    print("  fetching NOAA DEM_global_mosaic export for the Golfo Dulce bbox …", flush=True)
    download(IMAGESERVER, dest, params=params)
    return dest


def describe(path: Path) -> dict:
    with rasterio.open(path) as src:
        band = src.read(1)
        return {
            "width": src.width,
            "height": src.height,
            "crs": str(src.crs),
            "bounds": [round(v, 6) for v in src.bounds],
            "dtype": str(src.dtypes[0]),
            "min_elevation_m": float(np.nanmin(band)),
            "max_elevation_m": float(np.nanmax(band)),
            "sample_check_8p60N_83p30W_m": None,  # filled in below
        }


def main() -> int:
    print("NOAA DEM_global_mosaic (GEBCO-comparable) — Golfo Dulce")
    print(f"  bbox {BBOX['west']},{BBOX['south']} -> {BBOX['east']},{BBOX['north']}")

    path = fetch()
    props = describe(path)
    with rasterio.open(path) as src:
        band = src.read(1)
        t = src.transform
        col = int((-83.30 - t.c) / t.a)
        row = int((8.60 - t.f) / t.e)
        props["sample_check_8p60N_83p30W_m"] = float(band[row, col])

    digest = sha256_of(path)
    size = path.stat().st_size
    print(f"    {rel(path)}  {size:,} bytes  sha256={digest[:16]}…")
    print(
        f"    {props['width']}x{props['height']} cells, "
        f"range {props['min_elevation_m']:.1f} to {props['max_elevation_m']:.1f} m, "
        f"mid-gulf check {props['sample_check_8p60N_83p30W_m']} m"
    )

    record(
        Provenance(
            dataset="gebco-comparable-dem-mosaic",
            path=rel(path),
            source_url=IMAGESERVER,
            accessed=today(),
            licence=LICENCE,
            licence_url=LICENCE_URL,
            sha256=digest,
            bytes=size,
            notes=(
                "NOT a pure GEBCO grid — GEBCO's own WCS/download API was found "
                "non-functional for scripted subsetting at time of writing (WCS rejects "
                "all coverage ids; WMS serves rendered images only). This is NOAA NCEI's "
                "global DEM mosaic, which blends several compilations including GEBCO. "
                "See pipeline/fetch_gebco.py module docstring for the full investigation."
            ),
            properties=props,
        )
    )
    print("\nWrote provenance to data/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
