"""Generate a deterministic synthetic replay file (offline demo, CI fixtures)."""

from __future__ import annotations

import argparse
from pathlib import Path

from skyradar_ingestion.synthetic import generate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/replay/synthetic.parquet"))
    parser.add_argument("--flights", type=int, default=600)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-time", type=int, default=None, help="unix time of first snapshot")
    args = parser.parse_args()
    path = generate(
        args.output,
        flights=args.flights,
        duration_s=args.minutes * 60,
        interval_s=args.interval,
        seed=args.seed,
        start_time=args.start_time,
    )
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
