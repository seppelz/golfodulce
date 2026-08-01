"""Record metadata for the nautical charts covering the Golfo Dulce.

This deliberately does NOT download or store chart imagery. Two of the three charts
identified in the literature harvest are commercial products sold by resellers
(nauticalchartsonline.com, toddchart.com) and are paywalled outright. The third, an 1822
Spanish Hydrographic Office chart, is itself long in the public domain, but the only copy
found online is a dealer's own photographic reproduction (geographicus.com) — dealer scans
of public-domain originals sit in a genuine copyright grey area (they typically assert
rights over their own photography even when the underlying work is free), so it is treated
as not safe to mirror.

What this script does instead: fetch each listing page, extract the factual metadata that
is not copyrightable (scale, edition, edition date, coverage, condition), and record it
with a link back to the source rather than a copy of the image. This is the responsible
reading of "chart digitization" given the licensing situation, not a shortcut — a reader
who wants the actual chart image is one click away via the recorded URL.

Usage:
    python pipeline/fetch_charts.py
"""

from __future__ import annotations

import json
import re

import requests

from common import RAW, Provenance, USER_AGENT, record, rel, today

OUT = RAW / "charts" / "charts.json"

# Sourced from bibliography/harvest/bathymetry.json; kept here as a small, independent
# fetch so the metadata can be re-verified against the live listing without touching the
# bibliography harvest itself.
CHARTS = [
    {
        "id": "nga-1998-chart21562",
        "title": "NGA Nautical Chart 21562, Golfo Dulce",
        "url": "https://www.nauticalchartsonline.com/chart/detail/21562-Golfo-Dulce",
        "publisher": "US National Geospatial-Intelligence Agency (NGA)",
        "coverage": "Golfo Dulce (dedicated single-sheet chart)",
    },
    {
        "id": "ukho-2026-chart2493",
        "title": "Admiralty Chart 2493, Ports on the Pacific Coast of Costa Rica and Panama",
        "url": "https://www.toddchart.com/products/admiralty-chart-2493-ports-on-the-pacific-coast-of-costa-rica-and-panama-ac2493.html",
        "publisher": "UK Hydrographic Office (UKHO)",
        "coverage": "Three-panel plans chart; Golfo Dulce is panel A only",
    },
    {
        "id": "direcciondehidrografia-1822-cartaesferica",
        "title": (
            "Carta Esferica desde el Golfo Dulce en la Costa Rica hasta Sn. Blas en la "
            "Nueva Galicia"
        ),
        "url": "https://www.geographicus.com/P/AntiqueMap/mexicoguatemala-hidrografia-1822",
        "publisher": "Spanish Hydrographic Office (Direccion de Hidrografia)",
        "coverage": "Pacific coast from Golfo Dulce to San Blas, Nueva Galicia",
    },
]

# Patterns used to pull factual, non-copyrightable metadata out of each listing page's
# visible text. Not every field exists on every page.
FIELD_PATTERNS = {
    "scale": re.compile(r"Scale:?\s*1?:?\s*([\d,]+)", re.I),
    "edition_number": re.compile(r"Edition\s*#?:?\s*(\d+)", re.I),
    "edition_date": re.compile(r"Edition Date:?\s*([\d-]+)", re.I),
    "condition": re.compile(r"Condition:?\s*([^\n]{5,120})", re.I),
    "cancelled": re.compile(r"(cancelled by NGA|withdrawn)", re.I),
}


def strip_tags(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    return text


def extract_metadata(text: str) -> dict:
    out = {}
    for field, pattern in FIELD_PATTERNS.items():
        m = pattern.search(text)
        if m:
            out[field] = m.group(1).strip() if m.lastindex else True
    return out


def main() -> int:
    print("Chart metadata — Golfo Dulce\n")
    results = []

    for chart in CHARTS:
        print(f"  {chart['id']} …", flush=True)
        try:
            resp = requests.get(chart["url"], headers={"User-Agent": USER_AGENT}, timeout=30)
            status = resp.status_code
            meta = extract_metadata(strip_tags(resp.text)) if resp.ok else {}
        except requests.RequestException as exc:
            status = None
            meta = {}
            print(f"    ! fetch failed: {exc}")

        entry = {**chart, "http_status": status, "accessed": today(), **meta}
        results.append(entry)
        print(f"    status={status}  extracted={list(meta.keys())}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    record(
        Provenance(
            dataset="chart-metadata",
            path=rel(OUT),
            source_url="see individual entries",
            accessed=today(),
            licence=(
                "Metadata only (scale, edition, condition) — factual, not copyrightable. "
                "No chart imagery is stored; two of three source charts are commercial "
                "products, and the third's only available scan is a dealer's own "
                "photographic reproduction of a public-domain original, which is not "
                "safe to mirror. See module docstring in pipeline/fetch_charts.py."
            ),
            licence_url="",
            sha256="n/a — small JSON, see file directly",
            bytes=OUT.stat().st_size,
            notes=f"{len(results)} chart(s) catalogued",
            properties={"charts": [r["id"] for r in results]},
        )
    )
    print(f"\nWrote {rel(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
