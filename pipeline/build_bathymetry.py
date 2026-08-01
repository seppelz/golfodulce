"""Render the depth map the site serves.

Design decision worth stating plainly: 97.4% of this gulf has never been sounded, so a
smooth, uniformly confident depth map would misrepresent the state of knowledge. The
renderer therefore produces two tile sets over the same grid —

    tiles/depth/     the depth surface
    tiles/coverage/  where that surface rests on real soundings and where it does not

— plus contours as vector data. The site shows depth by default and lets the reader
switch the coverage layer on. Seeing the two together is the point.

Artefact cells (implausibly deep clusters in the inner basin, see analyse_coverage.py)
are rendered in a distinct colour rather than blended into the ramp, so they read as
"something is wrong here" rather than as a trench.

Outputs land in site/public/ so they are served as static files with no runtime
dependency — MapLibre cannot read a COG without a plugin, and pre-rendered tiles keep
the deploy a pure static upload.

Usage:
    python pipeline/build_bathymetry.py
    python pipeline/build_bathymetry.py --zoom 8 14
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

import numpy as np
import rasterio
from contourpy import contour_generator
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject

from common import DERIVED, RAW, ROOT, rel
from geometry import gulf_mask

TOPO = RAW / "gmrt" / "gmrt_max.tif"
MASK = RAW / "gmrt" / "gmrt_topo-mask.tif"
PUBLIC = ROOT / "site" / "public"
TILE_SIZE = 256

ARTEFACT_DEPTH_M = -250.0

# Contours at intervals that suit a basin a little over 200 m deep. Sparse in the
# shallows where the interpolation is least trustworthy, tighter through the oxycline.
CONTOUR_LEVELS = [-10, -25, -50, -75, -100, -125, -150, -175, -200, -225]

# Depth ramp, shallow to deep. Cool blues darkening with depth; the shallows keep a hint
# of warmth so the mangrove margins stay legible against land.
DEPTH_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (222, 239, 246)),
    (-10.0, (183, 219, 237)),
    (-25.0, (146, 199, 228)),
    (-50.0, (105, 173, 214)),
    (-75.0, (72, 145, 196)),
    (-100.0, (48, 118, 175)),
    (-125.0, (33, 94, 150)),
    (-150.0, (23, 72, 124)),
    (-175.0, (16, 54, 99)),
    (-200.0, (10, 39, 76)),
    (-250.0, (6, 26, 55)),
]

ARTEFACT_RGB = (196, 92, 74)      # implausible depths — flagged, not blended
LAND_RGBA = (0, 0, 0, 0)          # transparent; the basemap supplies land
MEASURED_RGBA = (64, 173, 128, 190)
INTERPOLATED_RGBA = (208, 74, 62, 90)


def ramp_lut() -> np.ndarray:
    """A 1 m resolution lookup table from depth to RGB, built once."""
    depths = np.arange(0, -1001, -1, dtype=np.float32)
    stops = sorted(DEPTH_STOPS, key=lambda s: -s[0])
    xs = np.array([s[0] for s in stops], dtype=np.float32)[::-1]
    rgb = np.array([s[1] for s in stops], dtype=np.float32)[::-1]
    out = np.empty((depths.size, 3), dtype=np.uint8)
    for c in range(3):
        out[:, c] = np.interp(depths, xs, rgb[:, c]).astype(np.uint8)
    return out


LUT = ramp_lut()


def colourise_depth(depth: np.ndarray, gulf: np.ndarray) -> np.ndarray:
    """Depth grid to RGBA. Land and anything outside the gulf is transparent."""
    h, w = depth.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    water = gulf & np.isfinite(depth)

    # NaN casts to int are undefined, so neutralise them before indexing. These cells
    # are transparent in the output anyway, but the LUT index must still be in range.
    safe = np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0)
    idx = np.clip((-safe).astype(np.int32), 0, LUT.shape[0] - 1)
    rgb = LUT[idx]
    out[..., :3] = rgb
    out[..., 3] = np.where(water, 255, 0)

    artefact = water & (depth < ARTEFACT_DEPTH_M)
    out[artefact, 0], out[artefact, 1], out[artefact, 2] = ARTEFACT_RGB
    return out


def colourise_coverage(measured: np.ndarray, gulf: np.ndarray) -> np.ndarray:
    h, w = measured.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[gulf & measured] = MEASURED_RGBA
    out[gulf & ~measured] = INTERPOLATED_RGBA
    return out


# --- web mercator ------------------------------------------------------------------

def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def tile_bounds_3857(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """Tile bounds in EPSG:3857 metres."""
    circumference = 2 * math.pi * 6378137.0
    origin = -circumference / 2
    size = circumference / (2**z)
    left = origin + x * size
    top = -origin - y * size
    return left, top - size, left + size, top


def reproject_to_3857(src_path: Path, band: np.ndarray, resampling: Resampling):
    """Warp a single band from the source CRS to EPSG:3857."""
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, "EPSG:3857", src.width, src.height, *src.bounds
        )
        dst = np.full((height, width), np.nan, dtype="float32")
        reproject(
            source=band.astype("float32"),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs="EPSG:3857",
            resampling=resampling,
            src_nodata=np.nan,
            dst_nodata=np.nan,
        )
    return dst, transform


def cut_tiles(rgba: np.ndarray, transform, out_dir: Path, zooms: range, bounds_ll) -> int:
    """Slice an EPSG:3857 RGBA array into an XYZ tile pyramid."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    h, w = rgba.shape[:2]
    written = 0

    for z in zooms:
        x0, y0 = lonlat_to_tile(bounds_ll[0], bounds_ll[3], z)
        x1, y1 = lonlat_to_tile(bounds_ll[2], bounds_ll[1], z)
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                left, bottom, right, top = tile_bounds_3857(tx, ty, z)
                # Map tile pixel grid back into source array indices.
                cols = (np.linspace(left, right, TILE_SIZE, endpoint=False) - transform.c) / transform.a
                rows = (np.linspace(top, bottom, TILE_SIZE, endpoint=False) - transform.f) / transform.e
                cc = np.clip(cols.astype(np.int32), 0, w - 1)
                rr = np.clip(rows.astype(np.int32), 0, h - 1)
                inside = (
                    (cols >= 0) & (cols < w)
                )[None, :] & ((rows >= 0) & (rows < h))[:, None]

                tile = rgba[np.ix_(rr, cc)]
                tile = np.where(inside[..., None], tile, 0).astype(np.uint8)
                if tile[..., 3].max() == 0:
                    continue  # fully transparent, nothing to serve

                dest = out_dir / str(z) / str(tx)
                dest.mkdir(parents=True, exist_ok=True)
                Image.fromarray(tile, "RGBA").save(dest / f"{ty}.png", optimize=True)
                written += 1
    return written


def build_contours(depth: np.ndarray, gulf: np.ndarray, transform) -> dict:
    """Depth contours as GeoJSON, in lon/lat."""
    field = np.where(gulf, depth, np.nan)
    gen = contour_generator(z=field, name="serial", corner_mask=True)
    features = []
    for level in CONTOUR_LEVELS:
        for line in gen.lines(level):
            arr = np.asarray(line)
            if arr.shape[0] < 8:
                continue
            lon = transform.c + (arr[:, 0] + 0.5) * transform.a
            lat = transform.f + (arr[:, 1] + 0.5) * transform.e
            coords = [[round(float(a), 5), round(float(b), 5)] for a, b in zip(lon, lat)]
            features.append(
                {
                    "type": "Feature",
                    "properties": {"depth_m": abs(level)},
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def build_land(topo: np.ndarray, transform) -> dict:
    """Land polygons derived from our own grid.

    Deliberately not a third-party basemap: this keeps the site free of API keys and
    external tile dependencies, and means the coastline is consistent with the same
    elevation data the depths come from. Small islands and speckle are dropped.
    """
    from rasterio.features import shapes  # noqa: PLC0415

    land = (topo >= 0).astype(np.uint8)
    features = []
    for geom, value in shapes(land, mask=land.astype(bool), transform=transform):
        if value != 1:
            continue
        rings = geom["coordinates"]
        # Drop specks: a ring of fewer than ~40 vertices at this cell size is noise.
        if not rings or len(rings[0]) < 40:
            continue
        rounded = [[[round(float(x), 5), round(float(y), 5)] for x, y in ring] for ring in rings]
        features.append(
            {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": rounded}}
        )
    return {"type": "FeatureCollection", "features": features}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zoom", nargs=2, type=int, default=[8, 14], metavar=("MIN", "MAX"))
    args = ap.parse_args()
    zooms = range(args.zoom[0], args.zoom[1] + 1)

    with rasterio.open(TOPO) as src:
        topo = src.read(1)
        transform_ll = src.transform
        bounds_ll = src.bounds
    with rasterio.open(MASK) as src:
        measured_ll = ~np.isnan(src.read(1))

    gulf_ll = gulf_mask(topo, transform_ll)

    print("Building depth map")
    print(f"  gulf cells {gulf_ll.sum():,}, zooms {zooms.start}-{zooms.stop - 1}")

    # Contours from the native lon/lat grid — no reprojection error in the geometry.
    contours = build_contours(topo, gulf_ll, transform_ll)
    out_data = PUBLIC / "data"
    out_data.mkdir(parents=True, exist_ok=True)
    (out_data / "contours.geojson").write_text(json.dumps(contours), encoding="utf-8")
    print(f"  contours: {len(contours['features'])} lines at {len(CONTOUR_LEVELS)} levels")

    land = build_land(topo, transform_ll)
    (out_data / "land.geojson").write_text(json.dumps(land), encoding="utf-8")
    land_kb = len(json.dumps(land)) // 1024
    print(f"  land: {len(land['features'])} polygons ({land_kb} kB)")

    # Tiles need EPSG:3857.
    depth_3857, tf = reproject_to_3857(TOPO, np.where(gulf_ll, topo, np.nan), Resampling.bilinear)
    gulf_3857 = np.isfinite(depth_3857)
    n = cut_tiles(colourise_depth(depth_3857, gulf_3857), tf, PUBLIC / "tiles" / "depth", zooms, bounds_ll)
    print(f"  depth tiles: {n}")

    # Coverage is categorical — nearest neighbour, never interpolated.
    cov_src = np.where(gulf_ll, measured_ll.astype("float32"), np.nan)
    cov_3857, tf2 = reproject_to_3857(MASK, cov_src, Resampling.nearest)
    cov_gulf = np.isfinite(cov_3857)
    n = cut_tiles(
        colourise_coverage(cov_3857 > 0.5, cov_gulf), tf2, PUBLIC / "tiles" / "coverage", zooms, bounds_ll
    )
    print(f"  coverage tiles: {n}")

    # A small metadata file so the site never hard-codes extents or legend values.
    meta = {
        "bounds": [round(v, 5) for v in bounds_ll],
        "minzoom": zooms.start,
        "maxzoom": zooms.stop - 1,
        "contour_levels_m": [abs(v) for v in CONTOUR_LEVELS],
        "artefact_threshold_m": ARTEFACT_DEPTH_M,
        "legend": {
            "depth_stops": [{"depth_m": d, "rgb": list(c)} for d, c in DEPTH_STOPS],
            "artefact_rgb": list(ARTEFACT_RGB),
            "measured_rgba": list(MEASURED_RGBA),
            "interpolated_rgba": list(INTERPOLATED_RGBA),
        },
    }
    (out_data / "bathymetry.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nWrote {rel(out_data)}/ and {rel(PUBLIC / 'tiles')}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
