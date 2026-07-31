"""Shared plumbing for the fetch scripts.

Two ideas run through this module. First, raw downloads are immutable: once a file lands
in data/raw it is never edited, only superseded. Second, every download records where it
came from, when, and what its checksum is — so a later reader can tell whether the file
they have is the file we described.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
MANIFEST = ROOT / "data" / "manifest.json"

# The gulf plus a margin for the sill and outer approaches. Everything downstream
# uses this one definition so extents cannot silently drift between datasets.
BBOX = {
    "west": -83.60,
    "east": -83.00,
    "south": 8.35,
    "north": 8.80,
}

# The gulf is a diagonal NW-SE body, so a rectangle is a poor description of it: a box
# tight enough to exclude the open Pacific also clips the gulf, and a box loose enough to
# contain the gulf pulls in 2000 m Pacific water that badly skews any depth statistic.
# Instead the gulf is defined as the water connected to this seed point, with a barrier
# across the mouth. See gulf_mask() in geometry.py.
GULF_SEED = (-83.35, 8.65)  # lon, lat — well inside the inner basin

# Latitude of the cut across the mouth, between Cabo Matapalo on the Osa side and the
# Punta Banco side. Water south of this line is open Pacific and is excluded.
GULF_MOUTH_LAT = 8.42

USER_AGENT = "golfodulce-pipeline/0.1 (research; mailto:sebastian.soecker@gmail.com)"

# The default Windows console codepage is cp1252, which cannot encode the degree
# signs, arrows and accented place names these scripts print. Force UTF-8 so output
# is identical on Windows and on a Linux runner.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class Provenance:
    """What we must be able to say about every file in data/raw."""

    dataset: str
    path: str
    source_url: str
    accessed: str
    licence: str
    licence_url: str
    sha256: str
    bytes: int
    notes: str = ""
    # Free-form: pixel counts, record counts, CRS, resolution — whatever makes the
    # file checkable without opening it.
    properties: dict[str, Any] | None = None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, *, params: dict | None = None, timeout: int = 180) -> Path:
    """Stream a URL to disk. Writes to a .part file first so a failed download
    never leaves a truncated file looking like a complete one."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(
        url, params=params, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    return dest


def record(prov: Provenance) -> None:
    """Add or update an entry in the dataset manifest, keyed by dataset name."""
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, Any] = {}
    if MANIFEST.exists():
        entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries[prov.dataset] = asdict(prov)
    MANIFEST.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def today() -> str:
    return date.today().isoformat()


def rel(path: Path) -> str:
    """Manifest paths are repo-relative and forward-slashed, so they read the same
    on Windows and on a Linux CI runner."""
    return path.relative_to(ROOT).as_posix()
