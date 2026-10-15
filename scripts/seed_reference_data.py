"""Download reference data (never committed) into ``data/reference``.

* OurAirports airports.csv (public domain) - airports, coordinates, ICAO/IATA codes.
* OurAirports countries.csv - country names for ISO codes.
* OpenSky aircraft database (icao24 -> registration, type, operator; ~95 MB) and the
  ICAO Doc 8643 aircraft types (type designator -> model, engines, wake category),
  both published by the OpenSky Network for non-commercial use. Skipped with
  ``--no-aircraft-db`` or ``SKYRADAR_SKIP_AIRCRAFT_DB=1``.
* OpenFlights airlines.dat (Open Database License) - ICAO airline designator -> name.

Every file is optional for the pipeline: a missing file yields empty dimensions.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data"
OPENFLIGHTS = "https://raw.githubusercontent.com/jpatokal/openflights/master/data"
FILES = {
    "airports.csv": f"{OURAIRPORTS}/airports.csv",
    "countries.csv": f"{OURAIRPORTS}/countries.csv",
    "airlines.dat": f"{OPENFLIGHTS}/airlines.dat",
}
OPENSKY_METADATA = "https://s3.opensky-network.org/data-samples/metadata"
AIRCRAFT_FILES = {
    "aircraft_database.csv": f"{OPENSKY_METADATA}/aircraftDatabase.csv",
    "aircraft_types.csv": f"{OPENSKY_METADATA}/doc8643AircraftTypes.csv",
}


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
    parser.add_argument(
        "--no-aircraft-db", action="store_true", help="skip the OpenSky aircraft database (~95 MB)"
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    files = dict(FILES)
    if not (args.no_aircraft_db or os.getenv("SKYRADAR_SKIP_AIRCRAFT_DB") == "1"):
        files.update(AIRCRAFT_FILES)
    failures = 0
    for name, url in files.items():
        try:
            download(url, args.dest / name, args.force)
        except PermissionError as exc:
            # Typically a folder created as root by Docker before this script ran.
            print(
                f"✗ {name}: {exc}\n  The folder belongs to another user: run"
                f" `sudo chown -R $USER {args.dest}` and start again.",
                file=sys.stderr,
            )
            return 1
        except OSError as exc:
            failures += 1
            print(f"✗ {name}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
