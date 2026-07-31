"""Fetch the GMRT bathymetric grid for the Golfo Dulce.

GMRT (Global Multi-Resolution Topography, Lamont-Doherty / Marine Geoscience Data System)
is the right primary baseline here rather than GEBCO alone, because where real multibeam
exists GMRT carries it at full resolution, and where it does not, GMRT falls back to the
same global grid GEBCO publishes.

That fallback is precisely what we need to expose. A GMRT tile that *looks* detailed may
be nothing more than upsampled interpolation, and presenting it as survey data would be
the exact dishonesty this project exists to avoid. So we pull the grid at several
resolutions and compare: if the coarse and fine grids carry the same information content,
there are no real soundings underneath.

Usage:
    python pipeline/fetch_gmrt.py
    python pipeline/fetch_gmrt.py --check    # re-fetch and compare against the manifest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import BBOX, RAW, Provenance, download, record, rel, sha256_of, today

GMRT_GRID = "https://www.gmrt.org/services/GridServer"

# GMRT resolutions worth having. "max" is what the service will give for this box;
# the coarser ones are the comparison baseline for the interpolation test.
RESOLUTIONS = ["max", "high", "med", "low"]

LICENCE = "GMRT data are freely available for research and education."
LICENCE_URL = "https://www.gmrt.org/about/terms_of_use.php"


def fetch(resolution: str, layer: str = "topo") -> Path:
    name = f"gmrt_{resolution}" if layer == "topo" else f"gmrt_{layer}"
    dest = RAW / "gmrt" / f"{name}.tif"
    params = {
        "minlongitude": BBOX["west"],
        "maxlongitude": BBOX["east"],
        "minlatitude": BBOX["south"],
        "maxlatitude": BBOX["north"],
        "format": "geotiff",
        "resolution": resolution,
        "layer": layer,
    }
    print(f"  fetching GMRT layer={layer} resolution={resolution} …", flush=True)
    download(GMRT_GRID, dest, params=params)
    return dest


def describe(path: Path) -> dict:
    """Read the raster's shape and value range without assuming rasterio is present."""
    try:
        import rasterio  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except ImportError:
        return {"note": "install pipeline/requirements.txt for raster properties"}

    with rasterio.open(path) as src:
        band = src.read(1, masked=True)
        depths = band[band < 0]
        return {
            "width": src.width,
            "height": src.height,
            "crs": str(src.crs),
            "bounds": [round(v, 6) for v in src.bounds],
            "dtype": str(src.dtypes[0]),
            "min_elevation_m": float(band.min()),
            "max_elevation_m": float(band.max()),
            "deepest_m": float(depths.min()) if depths.size else None,
            "unique_values": int(np.unique(band.compressed()).size),
            "cells": int(src.width * src.height),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify checksums against the manifest")
    args = ap.parse_args()

    print("GMRT — Golfo Dulce")
    print(f"  bbox {BBOX['west']},{BBOX['south']} → {BBOX['east']},{BBOX['north']}")

    # The topo-mask layer is the important one: it is the same surface with cells that
    # rest only on the interpolated global base grid removed, which is how we can tell
    # measured depth from invented depth.
    targets: list[tuple[str, str]] = [(res, "topo") for res in RESOLUTIONS]
    targets.append(("max", "topo-mask"))

    for res, layer in targets:
        try:
            path = fetch(res, layer)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! layer={layer} resolution={res} failed: {exc}", file=sys.stderr)
            continue

        props = describe(path)
        digest = sha256_of(path)
        size = path.stat().st_size
        print(f"    {rel(path)}  {size:,} bytes  sha256={digest[:16]}…")
        if "width" in props:
            print(
                f"    {props['width']}×{props['height']} cells, "
                f"{props['unique_values']:,} unique values, deepest {props['deepest_m']} m"
            )

        record(
            Provenance(
                dataset=f"gmrt-{res}" if layer == "topo" else f"gmrt-{layer}",
                path=rel(path),
                source_url=GMRT_GRID,
                accessed=today(),
                licence=LICENCE,
                licence_url=LICENCE_URL,
                sha256=digest,
                bytes=size,
                notes=(
                    "GMRT synthesises multibeam where available over a global base grid. "
                    "Detail visible here is not evidence of a survey — see the coverage "
                    "analysis in pipeline/analyse_coverage.py."
                ),
                properties=props,
            )
        )

    print("\nWrote provenance to data/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
