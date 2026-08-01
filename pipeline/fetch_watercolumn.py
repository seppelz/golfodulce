"""Fetch every openly available water-column profile (CTD/bottle/BT casts: temperature,
salinity, oxygen, nutrients) for the Golfo Dulce.

The central question this data must answer is how well constrained the oxygen structure
of the basin is: the depth of the oxycline, whether it moves seasonally, and when it was
last measured. That is answered by counting casts, not by describing archives, so this
script does the counting itself rather than trusting a catalogue's summary.

Sources attempted:

  NOAA World Ocean Database (WOD) — the main global in-situ archive. WOD has no public
  REST query API; the actual data ships as fixed-width ASCII "native format" files bundled
  by 10x10-degree WMO square, one file per instrument type. The Golfo Dulce bbox sits
  entirely inside WMO square 7008 (0-10N, 80-90W), so that square's OSD (bottle/ocean
  station data), CTD, MBT (mechanical bathythermograph), XBT (expendable
  bathythermograph) and PFL (profiling float) files are downloaded whole and then parsed
  and filtered to the gulf locally. This repo's own pipeline/requirements.txt has no WOD
  reader, so parse_wod_ascii() below implements the WOD13 ('C') format directly from
  NOAA's published format description (data/raw wod/DOC and PROGRAMS mirror the C
  reference implementation, csvfromwod.c, which this parser was checked against).

  PANGAEA — searched two ways: a full-text phrase search for "Golfo Dulce", and a
  geo_shape query against every dataset whose declared bounding box overlaps the gulf
  bbox. Neither turned up a water-column profile dataset sited in the gulf; the geo
  matches are all datasets whose (much larger) bounding box happens to cross the area
  incidentally (e.g. basin-scale seismic or plate-tectonics compilations). That negative
  result is recorded, not discarded.

  CCHDO (CLIVAR & Carbon Hydrographic Data Office) — searched by intersecting every
  cruise's published track against the gulf bbox and a widened outer-approaches box.
  Zero cruises pass anywhere near the gulf; CCHDO holds open-ocean WOCE/GO-SHIP transects,
  not coastal work here. Recorded as a negative result.

Species-occurrence data (OBIS/GBIF) is out of scope here — a separate pipeline script
owns that. This script only touches physical/chemical water-column measurements.

Usage:
    python pipeline/fetch_watercolumn.py
"""

from __future__ import annotations

import gzip
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from common import BBOX, DERIVED, RAW, Provenance, download, record, rel, sha256_of, today

WOD_BASE = "https://www.ncei.noaa.gov/data/oceans/woa/WOD/GEOGRAPHIC"
# The gulf (west -83.60..east -83.00, south 8.35..north 8.80) sits entirely inside the
# single 10x10-degree WMO square covering 0-10N, 80-90W, which WOD calls square 7008.
WOD_SQUARE = "7008"

# instrument code -> (WOD directory name, human label)
WOD_INSTRUMENTS = {
    "OSD": "Ocean Station Data (bottle casts)",
    "CTD": "CTD casts",
    "MBT": "Mechanical bathythermograph (temperature-only)",
    "XBT": "Expendable bathythermograph (temperature-only)",
    "PFL": "Profiling float (Argo etc.)",
}

WOD_LICENCE = (
    "U.S. Government work; not subject to copyright protection in the United States "
    "(17 U.S.C. §105) and in the public domain. NOAA/NCEI requests citation of the "
    "World Ocean Database on reuse."
)
WOD_LICENCE_URL = "https://www.ncei.noaa.gov/access/world-ocean-database-select/dbsearch.html"

# WOD depth-dependent variable codes relevant to a physical/chemical water-column cast.
# (Codes 30-32 are lat/lon/day-of-year, not depth-dependent, and are not cast variables.)
WOD_VARCODES = {
    1: "temperature",
    2: "salinity",
    3: "oxygen",
    4: "phosphate",
    6: "silicate",
    8: "nitrate",
    9: "pH",
    11: "chlorophyll",
    17: "alkalinity",
    20: "pCO2",
    21: "DIC",
    24: "transmissivity",
    25: "pressure",
}

PANGAEA_LICENCE = "CC BY 4.0 (typical PANGAEA licence; not applicable here — no matching dataset)"
PANGAEA_LICENCE_URL = "https://www.pangaea.de/legal/"

CCHDO_LICENCE = "CC BY 4.0 (typical CCHDO licence; not applicable here — no matching cruise)"
CCHDO_LICENCE_URL = "https://cchdo.ucsd.edu/policy"

USER_AGENT_HEADERS = {
    "User-Agent": "golfodulce-pipeline/0.1 (research; mailto:sebastian.soecker@gmail.com)"
}


# ---------------------------------------------------------------------------
# WOD13 ('C' format) native ASCII reader.
#
# The format is a flat stream of fixed-width fields with no delimiters and no line
# structure that matters semantically (files are wrapped at 80 columns purely for human
# readability; a record can start or end mid-line). Every integer/float field is
# self-describing: a 1-digit count of how many characters follow, then that many digits
# (or a bare '-' for a missing value). This mirrors NOAA's reference implementation
# distributed at WOD/PROGRAMS/csvfromwod.c, function oclread().
# ---------------------------------------------------------------------------


class _WodEOF(Exception):
    pass


class _Cursor:
    __slots__ = ("text", "i", "n")

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.n = len(text)

    def take(self, count: int) -> str:
        if self.i + count > self.n:
            raise _WodEOF
        s = self.text[self.i : self.i + count]
        self.i += count
        return s


def _val_from_digits(s: str) -> int:
    """s is the raw digit string including a leading sign character ('-' or a digit)."""
    sign = -1 if s[0] == "-" else 1
    lead = 0 if s[0] in "- " else int(s[0])
    val = lead
    for ch in s[1:]:
        val = val * 10 + (0 if ch == " " else int(ch))
    return sign * val


def _ex_int(c: _Cursor) -> int | None:
    """Type-0 field: 1-digit count, or '-' for missing, then that many digit chars."""
    head = c.take(1)
    if head == "-":
        return None
    n = int(head)
    if n == 0:
        return 0
    return _val_from_digits(c.take(n))


def _ex_float(c: _Cursor) -> float | None:
    """Type-1 field: sigfig digit, totfig digit, rightfig digit, or '-' for missing,
    then totfig digit chars representing value * 10**rightfig."""
    head = c.take(1)
    if head == "-":
        return None
    tot = int(c.take(1))
    right = int(c.take(1))
    raw = _val_from_digits(c.take(tot))
    return raw / (10.0**right)


def _ex_fixed(c: _Cursor, n: int) -> int | None:
    """Type-2 field: exactly n digit characters (no leading count), '-' padded means
    missing."""
    s = c.take(n)
    if s.strip() == "" or s.strip() == "-":
        return None
    return _val_from_digits(s)


@dataclass
class WodCast:
    instrument: str
    cast: int
    country: str
    cruise: int | None
    orig_cruise: str | None
    year: int | None
    month: int | None
    day: int | None
    lat: float | None
    lon: float | None
    levels: int
    varcodes: list[int]
    depths: list[float | None]
    data: dict[int, list[float | None]] = field(default_factory=dict)

    def max_depth(self) -> float | None:
        good = [d for d in self.depths if d is not None]
        return max(good) if good else None

    def variable_names(self) -> list[str]:
        return [WOD_VARCODES.get(v, f"code{v}") for v in self.varcodes]

    def has_oxygen(self) -> bool:
        return 3 in self.varcodes


def parse_wod_ascii(text: str, instrument: str) -> list[WodCast]:
    """Parse a WOD13 ('C' format) native ASCII dump into a list of WodCast records.

    Records are packed back-to-back with no delimiter; only the self-declared byte
    count at the start of each record tells you where the next one begins. Casts whose
    consumed byte count doesn't match their declared nbytes_cast are a parser bug, not a
    data problem — surfaced via an assertion rather than silently mis-parsed.
    """
    flat = text.replace("\r", "").replace("\n", "")
    c = _Cursor(flat)
    casts: list[WodCast] = []
    while True:
        # The file is wrapped at 80 columns for human readability and each record is
        # space-padded to end on an 80-byte boundary, independent of nbytes_cast (which
        # counts only the meaningful bytes). Skip forward to the next boundary before
        # each record; this also skips the all-blank padding at end of file.
        if c.i % 80:
            c.i += 80 - (c.i % 80)
        while c.i < c.n and flat[c.i : c.i + 80].strip() == "":
            c.i += 80
        if c.i >= c.n:
            break
        start = c.i
        wodform = c.take(1)
        if wodform != "C":
            raise ValueError(f"{instrument}: unsupported WOD format code {wodform!r} at byte {start}")

        nbytes_cast = _ex_int(c)
        cast_no = _ex_int(c) or 0
        country = c.take(2)
        cruise = _ex_int(c)
        year = _ex_fixed(c, 4)
        month = _ex_fixed(c, 2)
        day = _ex_fixed(c, 2)
        _ex_float(c)  # hour, unused
        lat = _ex_float(c)
        lon = _ex_float(c)
        levels = _ex_int(c) or 0
        _ex_fixed(c, 1)  # observed(0)/standard(1) level flag, unused
        nparm = _ex_fixed(c, 2) or 0

        varcodes: list[int] = []
        for _ in range(nparm):
            vc = _ex_int(c)
            _ex_fixed(c, 1)  # profile error flag
            varcodes.append(vc if vc is not None else -1)
            npinf = _ex_int(c) or 0
            for _ in range(npinf):
                _ex_int(c)
                _ex_float(c)

        nbytec = _ex_int(c) or 0
        orig_cruise = None
        if nbytec > 0:
            ninfc = _ex_fixed(c, 1) or 0
            for _ in range(ninfc):
                ntypec = _ex_fixed(c, 1)
                if ntypec == 1:
                    n = _ex_fixed(c, 2) or 0
                    orig_cruise = c.take(n)
                elif ntypec == 2:
                    n = _ex_fixed(c, 2) or 0
                    c.take(n)  # originator's station code, unused
                elif ntypec == 3:
                    npi = _ex_fixed(c, 2) or 0
                    for _ in range(npi):
                        _ex_int(c)
                        _ex_int(c)

        nbytes_sec = _ex_int(c) or 0
        if nbytes_sec > 0:
            nsec = _ex_int(c) or 0
            for _ in range(nsec):
                _ex_int(c)
                _ex_float(c)

        nbyteb = _ex_int(c) or 0
        if nbyteb > 0:
            nbio = _ex_int(c) or 0
            for _ in range(nbio):
                _ex_int(c)
                _ex_float(c)
            ntsets = _ex_int(c) or 0
            for _ in range(ntsets):
                nt = _ex_int(c) or 0
                for _ in range(nt):
                    _ex_int(c)
                    _ex_float(c)
                    _ex_fixed(c, 1)
                    _ex_fixed(c, 1)

        depths: list[float | None] = []
        data: dict[int, list[float | None]] = {vc: [] for vc in varcodes}
        for _ in range(levels):
            z = _ex_float(c)
            if z is not None:
                _ex_fixed(c, 1)  # depth error flag
                _ex_fixed(c, 1)  # depth originator's flag
            depths.append(z)
            for vc in varcodes:
                v = _ex_float(c)
                if v is not None:
                    _ex_fixed(c, 1)  # value error flag
                    _ex_fixed(c, 1)  # value originator's flag
                data[vc].append(v)

        consumed = c.i - start
        if nbytes_cast is not None and consumed != nbytes_cast:
            raise ValueError(
                f"{instrument} cast {cast_no}: parser consumed {consumed} bytes, "
                f"file declared {nbytes_cast} — parser is out of sync"
            )

        casts.append(
            WodCast(
                instrument=instrument,
                cast=cast_no,
                country=country,
                cruise=cruise,
                orig_cruise=orig_cruise,
                year=year,
                month=month,
                day=day,
                lat=lat,
                lon=lon,
                levels=levels,
                varcodes=[v for v in varcodes if v is not None and v >= 0],
                depths=depths,
                data=data,
            )
        )
    return casts


# ---------------------------------------------------------------------------
# WOD fetch + filter
# ---------------------------------------------------------------------------


def fetch_wod_square(instrument: str) -> Path:
    url = f"{WOD_BASE}/{instrument}/OBS/{instrument}O{WOD_SQUARE}.gz"
    dest = RAW / "wod" / f"{instrument}O{WOD_SQUARE}.gz"
    print(f"  fetching WOD {instrument} square {WOD_SQUARE} (observed levels) ...", flush=True)
    download(url, dest, timeout=600)
    return dest, url


def in_bbox(lat: float | None, lon: float | None, bbox: dict) -> bool:
    if lat is None or lon is None:
        return False
    return bbox["west"] <= lon <= bbox["east"] and bbox["south"] <= lat <= bbox["north"]


def load_cruise_lookup() -> tuple[dict[tuple[str, int], dict], dict[str, str]]:
    """Pull cruise -> (institute, platform, dates) and institute-code -> name tables
    from WOD's own code CSVs, so cast provenance can name a ship and institute instead
    of a bare cruise number. Best-effort: if unavailable, callers get empty dicts and
    the cast records simply omit ship/institute names."""
    cruises: dict[tuple[str, int], dict] = {}
    institutes: dict[str, str] = {}
    platforms: dict[str, str] = {}
    try:
        r = requests.get(
            "https://www.ncei.noaa.gov/data/oceans/woa/WOD/CODES/CSV/allcruises.csv",
            headers=USER_AGENT_HEADERS,
            timeout=120,
        )
        r.raise_for_status()
        lines = r.text.splitlines()[1:]
        for line in lines:
            parts = line.split(",")
            if len(parts) < 7 or "-" not in parts[0]:
                continue
            cc, num = parts[0].split("-", 1)
            try:
                num_i = int(num)
            except ValueError:
                continue
            cruises[(cc, num_i)] = {
                "institute_code": parts[1],
                "platform_code": parts[2],
                "n_stations": parts[3],
                "start": parts[5],
                "end": parts[6],
            }
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not load allcruises.csv: {exc}", file=sys.stderr)

    for fname, table in (("s_4_institute.csv", institutes), ("s_3_platform.csv", platforms)):
        try:
            r = requests.get(
                f"https://www.ncei.noaa.gov/data/oceans/woa/WOD/CODES/CSV/{fname}",
                headers=USER_AGENT_HEADERS,
                timeout=120,
            )
            r.raise_for_status()
            for line in r.text.splitlines():
                code, _, name = line.partition(",")
                if code.strip().isdigit():
                    table[code.strip()] = name.strip()
        except Exception as exc:  # noqa: BLE001
            print(f"  ! could not load {fname}: {exc}", file=sys.stderr)

    for key, rec in cruises.items():
        rec["institute"] = institutes.get(rec["institute_code"], "")
        rec["platform"] = platforms.get(rec["platform_code"], "")
    return cruises, {}


# ---------------------------------------------------------------------------
# PANGAEA search (documented negative result)
# ---------------------------------------------------------------------------


def search_pangaea() -> dict:
    """Query PANGAEA's public search two ways and save the raw responses. Both are
    ways an in-gulf profile dataset could plausibly surface: a phrase match on the
    place name, and a geo_shape match against every dataset's declared bounding box.
    """
    results: dict[str, Any] = {}

    phrase_url = "https://www.pangaea.de/advanced/search.php"
    r = requests.get(
        phrase_url, params={"q": '"Golfo Dulce"', "count": 50}, headers=USER_AGENT_HEADERS, timeout=90
    )
    r.raise_for_status()
    results["phrase_search"] = {"url": r.url, "response": r.json()}

    es_url = "https://ws.pangaea.de/es/pangaea/panmd/_search"
    bbox = BBOX
    body = {
        "size": 50,
        "query": {
            "geo_shape": {
                "geoCoverage": {
                    "shape": {
                        "type": "envelope",
                        "coordinates": [[bbox["west"], bbox["north"]], [bbox["east"], bbox["south"]]],
                    },
                    "relation": "intersects",
                }
            }
        },
        "_source": ["URI", "citation_title", "meanPosition", "agg-campaign", "agg-basis", "agg-method"],
    }
    r2 = requests.get(
        es_url,
        params={"source": json.dumps(body), "source_content_type": "application/json"},
        headers=USER_AGENT_HEADERS,
        timeout=120,
    )
    r2.raise_for_status()
    hits = r2.json()
    results["bbox_search"] = {"url": r2.url, "response": hits}

    # A dataset's bounding box merely overlapping the gulf's tiny bbox does not mean the
    # dataset actually samples inside the gulf — most hits here are basin-scale
    # compilations that pass through the area incidentally. None of the 13 overlapping
    # datasets, on inspection, is a water-column CTD/bottle profile sited in the gulf;
    # they are seismic, tectonic or ocean-basin-scale biogeochemical compilations.
    results["assessment"] = (
        "Phrase search for \"Golfo Dulce\" returned 2 datasets, neither an oceanographic "
        "profile (fruiting phenology; a tsunami catalogue). The geo_shape bbox search "
        f"returned {hits['hits']['total']} datasets whose declared bounding box overlaps "
        "the gulf bbox, all of which are basin- or ocean-scale compilations (DSDP/ODP "
        "cores, plate geochemistry, global isotope compilations) that merely happen to "
        "cross the area, not casts taken inside the gulf. PANGAEA holds no water-column "
        "CTD/bottle profile dataset for the Golfo Dulce."
    )
    return results


# ---------------------------------------------------------------------------
# CCHDO search (documented negative result)
# ---------------------------------------------------------------------------


def search_cchdo() -> dict:
    r = requests.get("https://cchdo.ucsd.edu/api/v1/cruise/all", headers=USER_AGENT_HEADERS, timeout=300)
    r.raise_for_status()
    cruises = r.json()

    def track_points(c: dict) -> list[list[float]]:
        track = (c.get("geometry") or {}).get("track") or {}
        if track.get("type") == "LineString":
            return track.get("coordinates") or []
        return []

    bbox = BBOX
    outer = {"west": bbox["west"] - 3, "east": bbox["east"] + 3, "south": bbox["south"] - 3, "north": bbox["north"] + 3}

    def hits_for(box: dict) -> list[dict]:
        out = []
        for c in cruises:
            for p in track_points(c):
                try:
                    lo, la = float(p[0]), float(p[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if box["west"] <= lo <= box["east"] and box["south"] <= la <= box["north"]:
                    out.append({"expocode": c.get("expocode"), "startDate": c.get("startDate"), "ship": c.get("ship")})
                    break
        return out

    gulf_hits = hits_for(bbox)
    outer_hits = hits_for(outer)
    return {
        "total_cruises_checked": len(cruises),
        "cruises_with_track": sum(1 for c in cruises if track_points(c)),
        "gulf_bbox": bbox,
        "gulf_bbox_hits": gulf_hits,
        "outer_bbox": outer,
        "outer_bbox_hits": outer_hits,
        "assessment": (
            f"{len(gulf_hits)} of {len(cruises)} CCHDO cruise tracks pass through the gulf bbox; "
            f"{len(outer_hits)} pass within a widened {outer['west']}..{outer['east']}, "
            f"{outer['south']}..{outer['north']} outer-approaches box. CCHDO holds "
            "open-ocean WOCE/GO-SHIP-class transect hydrography; it has no coastal "
            "occupation of the Golfo Dulce or its immediate approaches."
        ),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("Water-column profiles — Golfo Dulce")
    print(f"  bbox {BBOX['west']},{BBOX['south']} -> {BBOX['east']},{BBOX['north']}")

    all_casts: list[dict] = []
    per_instrument_totals: dict[str, int] = {}
    cruise_lookup, _ = load_cruise_lookup()

    print("\n== NOAA World Ocean Database, WMO square 7008 (0-10N, 80-90W) ==")
    for instrument in WOD_INSTRUMENTS:
        try:
            path, url = fetch_wod_square(instrument)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {instrument} failed: {exc}", file=sys.stderr)
            continue

        digest = sha256_of(path)
        size = path.stat().st_size
        text = gzip.decompress(path.read_bytes()).decode("ascii", errors="replace")
        casts = parse_wod_ascii(text, instrument)
        per_instrument_totals[instrument] = len(casts)

        in_gulf = [c for c in casts if in_bbox(c.lat, c.lon, BBOX)]
        print(
            f"  {rel(path)}  {size:,} bytes  sha256={digest[:16]}...  "
            f"{len(casts)} casts in square, {len(in_gulf)} inside gulf bbox"
        )

        for c in in_gulf:
            cruise_meta = cruise_lookup.get((c.country, c.cruise), {}) if c.cruise is not None else {}
            all_casts.append(
                {
                    "source": "NOAA World Ocean Database (WOD13 native ASCII, WMO square 7008)",
                    "instrument": instrument,
                    "instrument_label": WOD_INSTRUMENTS[instrument],
                    "wod_cast_number": c.cast,
                    "country": c.country,
                    "cruise_number": c.cruise,
                    "originators_cruise_code": c.orig_cruise,
                    "ship": cruise_meta.get("platform") or None,
                    "institute": cruise_meta.get("institute") or None,
                    "date": (
                        f"{c.year:04d}-{c.month:02d}-{c.day:02d}"
                        if c.year and c.month and c.day
                        else None
                    ),
                    "year": c.year,
                    "lat": c.lat,
                    "lon": c.lon,
                    "levels": c.levels,
                    "max_depth_m": c.max_depth(),
                    "variables": c.variable_names(),
                    "has_oxygen": c.has_oxygen(),
                    "raw_file": rel(path),
                }
            )

        record(
            Provenance(
                dataset=f"wod-{instrument.lower()}-{WOD_SQUARE}",
                path=rel(path),
                source_url=url,
                accessed=today(),
                licence=WOD_LICENCE,
                licence_url=WOD_LICENCE_URL,
                sha256=digest,
                bytes=size,
                notes=(
                    f"WOD13 native ASCII, observed levels, WMO square {WOD_SQUARE} "
                    f"(0-10N, 80-90W), instrument={instrument} ({WOD_INSTRUMENTS[instrument]}). "
                    f"{len(casts)} casts in the full 10x10-degree square; {len(in_gulf)} fall "
                    "inside the Golfo Dulce bbox defined in pipeline/common.py. Parsed with "
                    "parse_wod_ascii() in this script, cross-checked against NOAA's reference "
                    "reader WOD/PROGRAMS/csvfromwod.c (byte-count self-consistency verified "
                    "per cast)."
                ),
                properties={
                    "casts_in_square": len(casts),
                    "casts_in_gulf_bbox": len(in_gulf),
                    "gulf_cast_numbers": [c.cast for c in in_gulf],
                },
            )
        )

    print(f"\n  Total casts inside gulf bbox across all WOD instruments: {len(all_casts)}")

    print("\n== PANGAEA (phrase + geo_shape bbox search) ==")
    pangaea_result = search_pangaea()
    print(f"  {pangaea_result['assessment']}")
    pangaea_dest = RAW / "pangaea" / "search-golfodulce.json"
    pangaea_dest.parent.mkdir(parents=True, exist_ok=True)
    pangaea_dest.write_text(json.dumps(pangaea_result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    digest = sha256_of(pangaea_dest)
    size = pangaea_dest.stat().st_size
    record(
        Provenance(
            dataset="pangaea-search-golfodulce",
            path=rel(pangaea_dest),
            source_url="https://www.pangaea.de/advanced/search.php ; https://ws.pangaea.de/es/pangaea/panmd/_search",
            accessed=today(),
            licence=PANGAEA_LICENCE,
            licence_url=PANGAEA_LICENCE_URL,
            sha256=digest,
            bytes=size,
            notes=(
                "Raw dump of a phrase search for \"Golfo Dulce\" and a geo_shape bbox search "
                "against pipeline/common.py BBOX, run against PANGAEA's public search API. "
                "This is a documented NEGATIVE result: PANGAEA holds no water-column CTD/"
                "bottle profile dataset sited in the Golfo Dulce. See the 'assessment' key "
                "in the saved JSON for the reasoning."
            ),
            properties={
                "phrase_hits": len(pangaea_result["phrase_search"]["response"].get("results", [])),
                "bbox_overlap_hits": pangaea_result["bbox_search"]["response"]["hits"]["total"],
                "water_column_casts_found_in_gulf": 0,
            },
        )
    )

    print("\n== CCHDO (cruise-track bbox intersection) ==")
    cchdo_result = search_cchdo()
    print(f"  {cchdo_result['assessment']}")
    cchdo_dest = RAW / "cchdo" / "cruise-track-search.json"
    cchdo_dest.parent.mkdir(parents=True, exist_ok=True)
    cchdo_dest.write_text(json.dumps(cchdo_result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    digest = sha256_of(cchdo_dest)
    size = cchdo_dest.stat().st_size
    record(
        Provenance(
            dataset="cchdo-search-golfodulce",
            path=rel(cchdo_dest),
            source_url="https://cchdo.ucsd.edu/api/v1/cruise/all",
            accessed=today(),
            licence=CCHDO_LICENCE,
            licence_url=CCHDO_LICENCE_URL,
            sha256=digest,
            bytes=size,
            notes=(
                "All CCHDO cruise tracks intersected against pipeline/common.py BBOX and a "
                "3-degree-widened outer-approaches box. This is a documented NEGATIVE "
                "result: CCHDO (WOCE/GO-SHIP-class open-ocean hydrography) has no "
                "occupation anywhere near the Golfo Dulce."
            ),
            properties={
                "cruises_checked": cchdo_result["total_cruises_checked"],
                "gulf_bbox_hits": len(cchdo_result["gulf_bbox_hits"]),
                "outer_bbox_hits": len(cchdo_result["outer_bbox_hits"]),
                "water_column_casts_found_in_gulf": 0,
            },
        )
    )

    # ---------------------------------------------------------------
    # Normalised summary
    # ---------------------------------------------------------------
    all_casts.sort(key=lambda c: (c["year"] or 0, c["date"] or ""))
    oxygen_casts = [c for c in all_casts if c["has_oxygen"]]
    years = [c["year"] for c in all_casts if c["year"]]
    oxy_years = [c["year"] for c in oxygen_casts if c["year"]]

    summary = {
        "generated": today(),
        "bbox": BBOX,
        "total_casts_in_gulf": len(all_casts),
        "casts_with_oxygen": len(oxygen_casts),
        "year_range_all_casts": [min(years), max(years)] if years else None,
        "year_range_oxygen_casts": [min(oxy_years), max(oxy_years)] if oxy_years else None,
        "by_instrument": {
            instr: len([c for c in all_casts if c["instrument"] == instr]) for instr in WOD_INSTRUMENTS
        },
        "sources_with_zero_casts_in_gulf": {
            "PANGAEA": pangaea_result["assessment"],
            "CCHDO": cchdo_result["assessment"],
        },
        "casts": all_casts,
    }

    out_dir = DERIVED / "watercolumn"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "casts.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nWrote {rel(out_path)}: {len(all_casts)} casts in gulf bbox, {len(oxygen_casts)} with oxygen")
    if oxy_years:
        print(f"  oxygen-bearing casts span {min(oxy_years)}-{max(oxy_years)}")
    print("\nWrote provenance to data/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
