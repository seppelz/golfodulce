"""Defining what counts as "the Golfo Dulce" in a raster.

Worth being explicit about, because getting this wrong quietly corrupts every statistic
downstream. The gulf runs diagonally NW-SE between the Osa Peninsula and the mainland.
Any bounding box tight enough to exclude the open Pacific also clips the gulf, and any
box loose enough to contain the gulf pulls in 2000 m Pacific water — which then dominates
the depth distribution and makes the basin look ten times deeper than it is.

So the gulf is defined topologically instead: the body of water connected to a seed point
in the inner basin, with a barrier drawn across the mouth.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from common import GULF_MOUTH_LAT, GULF_SEED


def cell_coords(transform, shape) -> tuple[np.ndarray, np.ndarray]:
    """Longitude and latitude of every cell centre."""
    rows, cols = np.indices(shape)
    xs = transform.c + (cols + 0.5) * transform.a
    ys = transform.f + (rows + 0.5) * transform.e
    return xs, ys


def gulf_mask(elevation: np.ndarray, transform) -> np.ndarray:
    """Boolean mask of water cells belonging to the Golfo Dulce proper.

    Land forms the natural boundary on three sides; the fourth is the artificial cut
    across the mouth at GULF_MOUTH_LAT, which stops the flood fill escaping into the
    Pacific.
    """
    xs, ys = cell_coords(transform, elevation.shape)

    water = elevation < 0
    # The mouth cut acts as a wall, not as a region to delete: cells south of it are
    # simply not traversable, so the fill terminates there.
    passable = water & (ys >= GULF_MOUTH_LAT)

    labels, _ = ndimage.label(passable)

    seed_lon, seed_lat = GULF_SEED
    seed_r = int(round((seed_lat - transform.f) / transform.e - 0.5))
    seed_c = int(round((seed_lon - transform.c) / transform.a - 0.5))
    seed_label = labels[seed_r, seed_c]
    if seed_label == 0:
        raise RuntimeError(
            f"seed point {GULF_SEED} did not land in water — check GULF_SEED against the grid"
        )

    return labels == seed_label


def cell_area_km2(transform, lat: float) -> float:
    """Approximate area of one cell at a given latitude, in square kilometres."""
    deg_lon_km = 111.320 * np.cos(np.radians(lat))
    deg_lat_km = 110.574
    return abs(transform.a) * deg_lon_km * abs(transform.e) * deg_lat_km
