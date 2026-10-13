"""Download reference data (never committed) into ``data/reference``.

* OurAirports airports.csv (public domain) - airports, coordinates, ICAO/IATA codes.
* OurAirports countries.csv - country names for ISO codes.
* Optional: OpenSky aircraft database (icao24 -> registration, type, operator),
  enabled with ``--aircraft-db`` (large file, ~100 MB).
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data"
FILES = {
    "airports.csv": f"{OURAIRPORTS}/airports.csv",
    "countries.csv": f"{OURAIRPORTS}/countries.csv",
}
AIRCRAFT_DB = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"


def download(url: str, target: Path, force: bool) -> None:
    if target.exists() and target.stat().st_size > 0 and not force:
        print(f"= {target} (already present)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    print(f"↓ {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "SkyRadar seed script"})
    with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    tmp.replace(target)
    print(f"✓ {target} ({target.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path("data/reference"))
    parser.add_argument("--aircraft-db", action="store_true", help="also fetch OpenSky aircraft DB")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    files = dict(FILES)
    if args.aircraft_db:
        files["aircraft_database.csv"] = AIRCRAFT_DB
    failures = 0
    for name, url in files.items():
        try:
            download(url, args.dest / name, args.force)
        except OSError as exc:
            failures += 1
            print(f"✗ {name}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
