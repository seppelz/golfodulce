"""Determine how much of the Golfo Dulce depth surface rests on real soundings.

This is the honest core of the project. A bathymetric grid renders as a smooth, confident
surface everywhere, whether or not anyone measured the seabed underneath. Publishing that
as knowledge would be exactly the failure this site exists to avoid.

Two independent signals are computed:

1. **Sounding coverage.** GMRT publishes `topo` (the complete surface) and `topo-mask`
   (the same surface with cells resting only on the interpolated global base grid blanked
   out). Differencing them says, cell by cell, which depths are measured.

2. **Artefacts.** The grid contains clusters of implausibly deep cells inside the inner
   basin — down to roughly -2000 m in a basin the literature puts at a little over
   -200 m. These are flagged separately rather than quietly averaged away, because a
   naive user of GEBCO or GMRT for this gulf would inherit them.

Outputs:
    data/derived/coverage/coverage_stats.json
    data/derived/coverage/coverage.tif    1 = measured, 0 = interpolated, 255 = not gulf

Usage:
    python pipeline/analyse_coverage.py
"""

from __future__ import annotations

import json

import numpy as np
import rasterio
from scipy import ndimage

from common import DERIVED, RAW, rel, today
from geometry import cell_area_km2, cell_coords, gulf_mask

TOPO = RAW / "gmrt" / "gmrt_max.tif"
MASK = RAW / "gmrt" / "gmrt_topo-mask.tif"
OUT_DIR = DERIVED / "coverage"

# The oxygen structure of this basin turns over somewhere around 100-200 m, so coverage
# either side of that boundary is what actually matters scientifically.
BANDS = [
    ("0 to 50 m", -50, 0),
    ("50 to 100 m", -100, -50),
    ("100 to 150 m", -150, -100),
    ("150 to 200 m", -200, -150),
    ("200 to 250 m", -250, -200),
    ("deeper than 250 m", -1_000_000, -250),
]

# Published maximum depth of the Golfo Dulce is a little over 200 m. Anything markedly
# past that inside the basin is treated as suspect rather than as bathymetry.
PLAUSIBLE_MAX_DEPTH_M = -250


def band_rows(depth: np.ndarray, measured: np.ndarray, sel: np.ndarray) -> list[dict]:
    rows = []
    for label, lo, hi in BANDS:
        band = sel & (depth > lo) & (depth <= hi)
        n = int(band.sum())
        m = int((band & measured).sum()) if n else 0
        rows.append(
            {
                "band": label,
                "cells": n,
                "measured": m,
                "measured_pct": round(100 * m / n, 1) if n else None,
            }
        )
    return rows


def main() -> int:
    with rasterio.open(TOPO) as src:
        topo = src.read(1)
        profile = src.profile
        transform = src.transform
    with rasterio.open(MASK) as src:
        masked = src.read(1)

    measured = ~np.isnan(masked)
    gulf = gulf_mask(topo, transform)
    xs, ys = cell_coords(transform, topo.shape)
    area = cell_area_km2(transform, 8.6)

    def summarise(sel: np.ndarray) -> dict:
        n = int(sel.sum())
        m = int((sel & measured).sum())
        return {
            "cells": n,
            "area_km2": round(n * area, 1),
            "measured": m,
            "measured_pct": round(100 * m / n, 1) if n else None,
        }

    # --- artefacts -------------------------------------------------------------
    suspect = gulf & (topo < PLAUSIBLE_MAX_DEPTH_M)
    labels, n_clusters = ndimage.label(suspect)
    clusters = []
    for i in range(1, n_clusters + 1):
        sel = labels == i
        if sel.sum() < 20:
            continue
        clusters.append(
            {
                "cells": int(sel.sum()),
                "area_km2": round(int(sel.sum()) * area, 2),
                "lon_range": [round(float(xs[sel].min()), 4), round(float(xs[sel].max()), 4)],
                "lat_range": [round(float(ys[sel].min()), 4), round(float(ys[sel].max()), 4)],
                "depth_range_m": [round(float(topo[sel].min()), 1), round(float(topo[sel].max()), 1)],
                "flagged_measured_by_gmrt": int((sel & measured).sum()),
            }
        )
    clusters.sort(key=lambda c: -c["cells"])

    plausible = gulf & (topo >= PLAUSIBLE_MAX_DEPTH_M)

    stats = {
        "generated": today(),
        "source": {
            "topo": rel(TOPO),
            "mask": rel(MASK),
            "service": "https://www.gmrt.org/services/GridServer",
            "notes": [
                "GMRT returned a byte-identical grid for resolution=max, high and med, "
                "so there is one native grid for this area — 'max' is not finer than 'med'.",
                "The gulf is defined by flood fill from a seed in the inner basin with a "
                "cut across the mouth, not by a bounding box; see pipeline/geometry.py.",
            ],
        },
        "grid": {
            "width": int(topo.shape[1]),
            "height": int(topo.shape[0]),
            "approx_cell_size_m": round(abs(transform.a) * 111320 * float(np.cos(np.radians(8.6))), 1),
        },
        "gulf": summarise(gulf),
        "gulf_excluding_artefacts": summarise(plausible),
        "by_depth_band": band_rows(topo, measured, gulf),
        "depth_distribution": {
            "median_m": round(float(np.median(topo[gulf])), 1),
            "mean_m": round(float(topo[gulf].mean()), 1),
            "p99_m": round(float(np.percentile(topo[gulf], 1)), 1),
            "deepest_plausible_m": round(float(topo[plausible].min()), 1),
        },
        "artefacts": {
            "threshold_m": PLAUSIBLE_MAX_DEPTH_M,
            "cells": int(suspect.sum()),
            "pct_of_gulf": round(100 * int(suspect.sum()) / int(gulf.sum()), 2),
            "deepest_m": round(float(topo[suspect].min()), 1) if suspect.any() else None,
            "clusters": clusters,
            "comment": (
                "The literature puts the maximum depth of the Golfo Dulce a little over "
                "200 m. These clusters reach roughly ten times that. They are reported, "
                "not silently corrected — anyone using GEBCO or GMRT for this gulf "
                "inherits them."
            ),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "coverage_stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")

    cov = np.where(gulf, measured.astype("uint8"), 255).astype("uint8")
    profile.update(dtype="uint8", count=1, nodata=255, compress="deflate")
    with rasterio.open(OUT_DIR / "coverage.tif", "w", **profile) as dst:
        dst.write(cov, 1)

    # --- report ----------------------------------------------------------------
    g = stats["gulf"]
    print("Golfo Dulce — bathymetric data coverage\n")
    print(f"  grid {stats['grid']['width']}×{stats['grid']['height']}, ~{stats['grid']['approx_cell_size_m']} m cells")
    print(f"  gulf area {g['area_km2']:,} km²  ({g['cells']:,} cells)")
    print(f"  median depth {stats['depth_distribution']['median_m']} m, "
          f"deepest plausible {stats['depth_distribution']['deepest_plausible_m']} m\n")
    print(f"  \033[1mmeasured by real soundings: {g['measured']:,} of {g['cells']:,} cells "
          f"({g['measured_pct']}%)\033[0m\n")
    print("  by depth band:")
    for row in stats["by_depth_band"]:
        pct = "—" if row["measured_pct"] is None else f"{row['measured_pct']}%"
        print(f"    {row['band']:<20} {pct:>6}  ({row['cells']:,} cells)")

    art = stats["artefacts"]
    print(f"\n  artefacts deeper than {art['threshold_m']} m: {art['cells']:,} cells "
          f"({art['pct_of_gulf']}% of gulf), reaching {art['deepest_m']} m")
    for c in art["clusters"]:
        print(f"    cluster {c['cells']:,} cells at {c['lat_range'][0]}–{c['lat_range'][1]}N, "
              f"{c['lon_range'][0]}–{c['lon_range'][1]}W → {c['depth_range_m'][0]} m "
              f"({c['flagged_measured_by_gmrt']} flagged as measured)")

    print(f"\nWrote {rel(OUT_DIR / 'coverage_stats.json')} and {rel(OUT_DIR / 'coverage.tif')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
